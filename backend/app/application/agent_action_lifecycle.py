"""AgentActionLifecycle — Run 终态回写 Action、Outcome 与一次后续计划（J-09）。

职责:
- queued→running 同步（Dispatcher 执行器启动时）;
- Run 终态 → Action 终态 + AgentOutcome 回写 + assistant result 消息
  + 可空的一次后续 action_plan 消息（proposed 子 Action，深度 1）;
- reconciliation：Worker 在 Run 终态后崩溃时，GET Action 或后台重放
  可安全重入 finalize——结果回写以"终态 Action + 已有 result"幂等，
  消息以 (conversation, kind, agent_action_id) 幂等，
  子提案以 (parent_action_id, replan_depth) 数据库唯一约束幂等。

子 Action 只展示和等待确认（status=proposed，不自动创建 Run）；
replan_depth=1 的 Action 完成后不再生成子提案（深度上限 1）。
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.application.agent_outcome_service import AgentOutcomeService
from app.application.conversation_service import MessageService
from app.core.errors import AgentStateTransitionError
from app.core.logging import get_logger
from app.db.models.agent_action import AgentAction
from app.db.models.message import Message
from app.db.models.workflow_run import WorkflowRun
from app.db.repositories.agent_actions import AgentActionRepository
from app.domain.agent_command import (
    AgentActionPlan,
    AgentOutcome,
    ArtifactSnapshot,
    RecommendedNextAction,
)
from app.domain.conversation import MessageCreate
from app.events.publisher import EventPublisher
from app.prompts.loader import PromptLoader

logger = get_logger(__name__)

# Run 终态 → AgentAction 终态（同名校验后直接映射）
_RUN_TO_ACTION_STATUS: dict[str, str] = {
    "completed": "completed",
    "failed": "failed",
    "needs_review": "needs_review",
    "cancelled": "cancelled",
}

_TERMINAL_ACTION_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "needs_review", "stale", "rejected"}
)


class AgentActionLifecycle:
    """Run ↔ Action 状态同步与终态回写。"""

    def __init__(self) -> None:
        self._actions = None  # 惰性 AgentActionRepository（需要 db）
        self._messages = MessageService()
        self._publisher = EventPublisher()

    # ------------------------------------------------------------------
    # 运行中同步
    # ------------------------------------------------------------------

    async def mark_running(self, db: AsyncSession, action_id: uuid.UUID) -> None:
        """Run 开始执行时把 Action queued→running（非 queued 时幂等跳过）。"""
        with contextlib.suppress(AgentStateTransitionError):
            # 重复唤醒 / 已被接管：状态由数据库唯一事实源决定
            await AgentActionRepository(db).transition(
                action_id, "running", expected_statuses={"queued"}
            )

    # ------------------------------------------------------------------
    # 终态回写
    # ------------------------------------------------------------------

    async def finalize(
        self,
        db: AsyncSession,
        *,
        action_id: uuid.UUID,
        run: WorkflowRun,
        final_state: dict[str, Any] | None = None,
        agent: BaseAgent | None = None,
        prompt_loader: PromptLoader | None = None,
    ) -> AgentAction | None:
        """Run 终态后回写 Action：状态 + Outcome + 消息 + 可空子提案。

        幂等：Action 已终态且 result 已写入时直接返回；消息与子提案
        各自幂等，重复 reconciliation 不会重复产生。
        """
        action = await AgentActionRepository(db).get_for_update(action_id)
        if action is None:
            logger.warning("finalize 找不到 AgentAction: %s", action_id)
            return None
        if action.run_id is not None and action.run_id != run.id:
            logger.warning("Action %s 关联的 Run 与当前 Run 不一致，跳过", action_id)
            return None

        # Run 尚未终态 → 不回写（Dispatcher 在终态后才调用；防御性守卫）
        if run.status not in _RUN_TO_ACTION_STATUS:
            return action
        # 已完成过回写（崩溃后重入 / 重复 reconciliation）→ 幂等返回
        if action.status in _TERMINAL_ACTION_STATUSES and action.result is not None:
            return action

        outcome = await AgentOutcomeService().evaluate(
            db,
            action=action,
            run=run,
            final_state=final_state,
            agent=agent,
            prompt_loader=prompt_loader,
        )

        # 状态回写：cancelled/failed 允许 queued 直达；completed/needs_review
        # 需先补 queued→running（Worker 在标记 running 前崩溃的场景）。
        target_status = _RUN_TO_ACTION_STATUS[run.status]
        repo = AgentActionRepository(db)
        try:
            if (
                action.status == "queued"
                and target_status in ("completed", "needs_review")
            ):
                action = await repo.transition(
                    action_id, "running", expected_statuses={"queued"}
                )
            action = await repo.transition(
                action_id,
                target_status,  # type: ignore[arg-type]
                expected_statuses={"queued", "running"},
                result=outcome.model_dump(mode="json"),
            )
        except AgentStateTransitionError:
            # 并发回写胜者已写入（result hash 一致性由确定性评估保证）
            await db.rollback()
            action = await AgentActionRepository(db).get_for_update(action_id)
            if action is None or action.status not in _TERMINAL_ACTION_STATUSES:
                raise
            return action

        # assistant 结果消息（幂等：同 action + kind 只追加一次）
        result_message = await self._append_result_message(
            db, action, run, outcome, final_state=final_state
        )

        # 一次后续计划：partially/blocked + 深度 0 + 白名单建议 → proposed 子 Action
        child_action = await self._maybe_create_child_action(
            db, action=action, outcome=outcome,
            result_message_id=result_message.id if result_message else None,
        )
        if child_action is not None:
            await self._append_plan_message(db, child_action)

        # 收尾指引：修订类任务完成而主创作任务仍停在确认门时，追加可读的
        # 下一步提示（配合对话短路，输入「继续」即可续跑）。
        await self._append_next_step_message(db, action=action, final_state=final_state)

        await self._publisher.publish_agent_action_event(
            db,
            run_id=run.id,
            agent_action_id=action.id,
            status=action.status,
            goal_status=outcome.goal_status,
            payload={
                "child_action_id": str(child_action.id) if child_action else None,
                "remaining_constraints": outcome.remaining_constraints,
            },
            autocommit=True,
        )
        return action

    async def reconcile(
        self,
        db: AsyncSession,
        *,
        action_id: uuid.UUID,
        agent: BaseAgent | None = None,
        prompt_loader: PromptLoader | None = None,
    ) -> AgentAction | None:
        """后台/GET 触发的补写：从 Run 终态与 state_summary 重放 finalize。"""
        action = await AgentActionRepository(db).get(action_id)
        if action is None or action.run_id is None:
            return action
        from app.application.run_service import RunService

        run = await RunService().get_run(db, action.run_id)
        return await self.finalize(
            db,
            action_id=action_id,
            run=run,
            final_state=run.state_summary or {},
            agent=agent,
            prompt_loader=prompt_loader,
        )

    # ------------------------------------------------------------------
    # 消息（幂等）
    # ------------------------------------------------------------------

    async def _find_message(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        *,
        kind: str,
        agent_action_id: uuid.UUID,
        message_type: str,
    ) -> Message | None:
        result = await db.execute(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.kind == kind,
                Message.message_metadata["agent_action_id"].astext == str(agent_action_id),
                Message.message_metadata["message_type"].astext == message_type,
            )
        )
        return result.scalar_one_or_none()

    async def _append_message(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        *,
        role: str,
        content: str,
        kind: Literal["action_result", "action_plan", "text"],
        metadata: dict[str, Any],
    ) -> Any:
        data = MessageCreate(role=role, content=content, kind=kind, metadata=metadata)
        # MessageService 返回 MessageResponse（含 .id，与 ORM 行一致）
        return await self._messages.append(db, conversation_id, data)

    async def _append_result_message(
        self,
        db: AsyncSession,
        action: AgentAction,
        run: WorkflowRun,
        outcome: AgentOutcome,
        *,
        final_state: dict[str, Any] | None = None,
    ) -> Any:
        existing = await self._find_message(
            db, action.conversation_id,
            kind="action_result", agent_action_id=action.id, message_type="result",
        )
        if existing is not None:
            return existing

        gate = (final_state or {}).get("stage_gate")
        if gate in ("outline", "scripts"):
            # 确认门是设计内的阶段暂停：可读的阶段性进展消息，
            # 不渲染 goal_status 术语/未完成约束/产物清单（前端按
            # stage_gate metadata 走最小化卡片）。
            state = final_state or {}
            written = len(state.get("script_artifact_ids") or {})
            target = state.get("target_episode_count") or 0
            content = (
                "StoryBible 与分集大纲已生成，等待确认后继续创作剧本。"
                if gate == "outline"
                else f"本批剧本已完成（共 {written}/{target} 集），可继续下一批或先修改剧本。"
            )
            metadata: dict[str, Any] = {
                "agent_action_id": str(action.id),
                "run_id": str(run.id),
                "message_type": "result",
                "goal_status": outcome.goal_status,
                "stage_gate": str(gate),
            }
        else:
            # 可读自然文案；状态详情由计划卡的 OutcomeView 承载，避免重复
            status_text = {
                "achieved": "本轮任务已完成。",
                "partially_achieved": "本轮任务部分完成。",
                "blocked": "本轮任务未能完成。",
                "cancelled": "任务已取消。",
            }.get(outcome.goal_status, "本轮任务已结束。")
            lines = [status_text]
            if outcome.score_delta is not None:
                lines.append(f"评分变化：{outcome.score_delta:+.1f}")
            for constraint in outcome.remaining_constraints:
                lines.append(f"未完成：{constraint}")
            content = "\n".join(lines)
            metadata = {
                "agent_action_id": str(action.id),
                "run_id": str(run.id),
                "message_type": "result",
                "goal_status": outcome.goal_status,
                "score_delta": outcome.score_delta,
                "remaining_constraints": outcome.remaining_constraints,
            }
        return await self._append_message(
            db,
            action.conversation_id,
            role="assistant",
            content=content,
            kind="action_result",
            metadata=metadata,
        )

    async def _append_next_step_message(
        self,
        db: AsyncSession,
        *,
        action: AgentAction,
        final_state: dict[str, Any] | None,
    ) -> None:
        """任务完成后项目仍停在确认门 → 追加可读的下一步指引（幂等）。

        场景：用户在门上多轮聊天修订，每轮修订是独立任务；完成后聊天
        底部只剩"已完成"卡，主创作任务的门卡淹没在历史里——用户不知
        如何推进。指引配合对话短路层，输入「继续」即直达续跑。
        """
        # 本任务自己停在门上：门消息/门按钮已是指引，不重复
        if (final_state or {}).get("stage_gate"):
            return
        if action.status != "completed":
            return
        existing = await self._find_message(
            db, action.conversation_id,
            kind="text", agent_action_id=action.id, message_type="next_step",
        )
        if existing is not None:
            return
        from app.skills.agent_shortcut import find_gated_run

        gated = await find_gated_run(db, action.project_id)
        if gated is None:
            return
        gate = (gated.state_summary or {}).get("stage_gate")
        hint = (
            "直接输入「继续」写下一批剧本，或继续提出修改意见。"
            if gate == "scripts"
            else "直接输入「继续」开始写剧本，或继续提出修改意见。"
        )
        # 门上多轮修订时每轮完成都会到这里：清掉上一条指引再追加，
        # 聊天里始终只有一条指向当前位置的提示，不刷屏。
        from sqlalchemy import delete

        await db.execute(
            delete(Message).where(
                Message.conversation_id == action.conversation_id,
                Message.kind == "text",
                Message.message_metadata["message_type"].astext == "next_step",
            )
        )
        await self._append_message(
            db,
            action.conversation_id,
            role="assistant",
            content=f"上一轮任务已完成。创作任务停在确认门：{hint}",
            kind="text",
            metadata={
                "agent_action_id": str(action.id),
                "message_type": "next_step",
            },
        )

    async def _append_plan_message(self, db: AsyncSession, child: AgentAction) -> Any:
        from app.application.agent_command_service import render_plan_message

        existing = await self._find_message(
            db, child.conversation_id,
            kind="action_plan", agent_action_id=child.id, message_type="follow_up",
        )
        if existing is not None:
            return existing
        plan = AgentActionPlan.model_validate(child.plan)
        return await self._append_message(
            db,
            child.conversation_id,
            role="assistant",
            content=render_plan_message(plan),
            kind="action_plan",
            metadata={
                "agent_action_id": str(child.id),
                "parent_action_id": str(child.parent_action_id),
                "message_type": "follow_up",
            },
        )

    # ------------------------------------------------------------------
    # 一次后续计划
    # ------------------------------------------------------------------

    async def _maybe_create_child_action(
        self,
        db: AsyncSession,
        *,
        action: AgentAction,
        outcome: AgentOutcome,
        result_message_id: uuid.UUID | None,
    ) -> AgentAction | None:
        """部分达成/受阻 + 深度 0 + 白名单建议 → 创建 proposed 子 Action。

        目标用当前最新 Artifact 重新解析（建议不含可执行句柄）；
        解析不到目标或唯一约束冲突时跳过（不阻断终态回写）。
        """
        if outcome.goal_status not in ("partially_achieved", "blocked"):
            return None
        if action.replan_depth != 0:
            return None  # 深度 1 的 Action 完成后不得继续生成子 Action
        recommendation = outcome.recommended_next_action
        if recommendation is None:
            return None
        existing_child = await db.execute(
            select(AgentAction).where(AgentAction.parent_action_id == action.id)
        )
        if existing_child.scalar_one_or_none() is not None:
            return None  # (parent, depth) 唯一：已有子提案时不再重复

        built = await self._build_child_plan(
            db, action=action, recommendation=recommendation,
            constraints=outcome.remaining_constraints,
        )
        if built is None:
            logger.info(
                "后续计划目标不可解析，跳过子提案: action=%s intent=%s",
                action.id, recommendation.intent,
            )
            return None
        plan, child_snapshots = built

        # agent_turn_id / user_message_id 均唯一（一消息一 Turn 一 Action）：
        # follow-up Turn 以结果消息为触发消息，血缘可追溯且不伪造用户输入。
        if result_message_id is None:
            logger.warning("缺少结果消息，跳过子提案: %s", action.id)
            return None
        from app.db.models.agent_turn import AgentTurn

        follow_up_turn = AgentTurn(
            project_id=action.project_id,
            conversation_id=action.conversation_id,
            user_message_id=result_message_id,
            idempotency_key=f"follow-up:{action.id}",
            request_hash="0" * 64,
            status="action_proposed",
            turn_type="plan",
        )
        db.add(follow_up_turn)
        await db.flush()  # 生成 follow_up_turn.id（客户端默认值在 flush 时生效）
        child = AgentAction(
            project_id=action.project_id,
            conversation_id=action.conversation_id,
            agent_turn_id=follow_up_turn.id,
            parent_action_id=action.id,
            replan_depth=1,
            intent=plan.intent,
            status="proposed",
            requires_confirmation=True,
            plan=plan.model_dump(mode="json"),
            source_artifact_ids=[
                s.model_dump(mode="json") for s in child_snapshots
            ],
        )
        db.add(child)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            # (parent_action_id, replan_depth) 唯一约束：重复 reconciliation 胜者已建
            logger.info("子 Action 已存在（唯一约束），跳过: parent=%s", action.id)
            return None
        logger.info("创建后续计划: parent=%s child=%s intent=%s", action.id, child.id, plan.intent)
        return child

    async def _build_child_plan(
        self,
        db: AsyncSession,
        *,
        action: AgentAction,
        recommendation: RecommendedNextAction,
        constraints: list[str] | None = None,
    ) -> tuple[AgentActionPlan, list[ArtifactSnapshot]] | None:
        """用当前最新 Artifact 重解析目标，构建服务端模板化子计划。

        子计划携带父计划未自动确认的约束——否则后续修订等于在无约束
        地重新生成，"已达成"没有意义。
        """
        from app.application.agent_command_service import (
            build_evaluate_plan,
            build_revise_outline_plan,
            build_revise_script_plan,
        )
        from app.artifacts.store import ArtifactStore

        carried = [c for c in (constraints or []) if c][:20]
        store = ArtifactStore()
        intent = recommendation.intent
        episode = recommendation.target.episode_number
        built: tuple[Any, ...] | None = None

        if intent == "revise_script":
            if episode is None:
                return None
            source = await store.get_latest(db, action.project_id, "script_draft", episode)
            if source is None:
                return None
            built = build_revise_script_plan(source=source, constraints=carried)
        elif intent == "revise_outline":
            source_outline = await store.get_latest(
                db, action.project_id, "episode_outline_set", 1
            )
            if source_outline is None:
                return None
            built = build_revise_outline_plan(
                source_outline=source_outline, constraints=carried
            )
        elif intent == "evaluate":
            head = build_evaluate_plan(episode=episode)
            built = (*head, [])
        else:
            return None  # create_script 等其余意图暂不构成后续修订链路

        _intent, command, target, goal, steps, snapshots = built
        plan = AgentActionPlan(
            goal=goal,
            intent=intent,
            command=command,
            target=target,
            constraints=carried,
            steps=steps,
            expected_impact=(
                ["落实上一轮未自动确认的修改要求"] if carried else ["延续上一个计划的未完成约束"]
            ),
        )
        return plan, snapshots

