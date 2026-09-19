"""AgentActionLifecycle — Run 终态回写 Action、Outcome 与一次后续计划（J-09）。

职责:
- queued→running 同步（Dispatcher 执行器启动时）;
- Run 终态 → Action 终态 + AgentOutcome 回写 + assistant result 消息
  + 可空的一次后续 action_plan 消息（proposed 子 Action，深度 1）;
- reconciliation：Worker 在 Run 终态后崩溃时，GET Action 或后台重放
  可安全重入 finalize——回写以"幕次键（last_synced_phase）一致 + 已有
  result"幂等（分段创作一个 Action 跨多幕：门上暂停与最终结局各有
  一条结果消息），消息以 (conversation, kind, agent_action_id, run_status)
  幂等，子提案以 (parent_action_id, replan_depth) 数据库唯一约束幂等。

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

from app.application.agent_outcome_service import AgentOutcomeService
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
from app.memory.wiring import get_message_service

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


def _run_phase(run: WorkflowRun, final_state: dict[str, Any] | None) -> str:
    """Run 幕次键（v2，带世代与执行次数）：区分同一 Action 的多次回写。

    v2 = 状态[:门类型]:g{stage_generation}:a{attempt_count}。g 区分门确认
    续跑的批次（W1-01：门续跑不再累加 attempt，两个 scripts 门若只看
    attempt 会重新吞并）；a 区分同批次内的故障重试（重试后的再次失败是
    新幕次，必须再次回写）。不带 v2 前缀的旧格式值视为存量冻结（与
    NULL 同语义，见 0010 迁移）。
    """
    if run.status == "needs_review":
        gate = (final_state or {}).get("stage_gate")
        gate_key = gate if gate in ("outline", "scripts") else "paused"
        return f"v2:needs_review:{gate_key}:g{run.stage_generation}:a{run.attempt_count}"
    return f"v2:{run.status}:g{run.stage_generation}:a{run.attempt_count}"


def _stored_phase_matches(stored: str, phase: str) -> bool:
    """比较已回写幕次键与新幕次键，容忍 W1-01 之前的旧 v2 格式。

    旧格式（v2:状态[:门]:a{N}，bda1302 写入）没有 g 段——按世代 0
    归一后比较：同一幕次不重复回写，新幕次（世代或 attempt 已推进）
    正常放行。无 v2 前缀的更早行不在此处理（冻结语义）。
    """
    if ":g" not in stored:
        head, _, attempt = stored.rpartition(":a")
        if head:
            stored = f"{head}:g0:a{attempt}"
    return stored == phase


def _render_mcp_result_message(
    action: AgentAction,
    run: WorkflowRun,
    outcome: AgentOutcome,
    final_state: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """MCP 调用完成的可读结果消息与 metadata（内容为外部不可信文本）。

    文本受 2000 字符上限；metadata 携带完整规范化结果（受 256 KiB 上限）
    供前端按内容类型渲染；图片/音频/embedded resource 只显示类型，不
    自动下载。
    """
    mcp = final_state.get("mcp_result") or {}
    text_blocks = [
        str(block.get("text"))
        for block in mcp.get("content", [])
        if block.get("kind") == "text" and block.get("text")
    ]
    unsupported = [
        block for block in mcp.get("content", [])
        if block.get("kind") in ("image", "audio", "embedded_resource")
    ]
    lines = [
        f"外部工具 {mcp.get('tool_name')}（来源 {mcp.get('server_id')}）"
        f"调用完成，用时 {mcp.get('duration_ms', 0)} ms。",
    ]
    body = "\n".join(text_blocks)[:2000]
    if body:
        lines.append(body)
    if mcp.get("truncated"):
        lines.append("（结果超出大小上限，已按内容块截断）")
    if unsupported:
        kinds = sorted({str(b.get("kind")) for b in unsupported})
        lines.append(f"（结果包含 {len(unsupported)} 个暂不支持直接展示的内容类型：{'、'.join(kinds)}）")
    if mcp.get("resource_links"):
        lines.append(f"资源链接 {len(mcp['resource_links'])} 个（见结果详情）")
    lines.append("以上内容由外部工具返回，请注意甄别；它不会直接改动任何稿件。")
    metadata: dict[str, Any] = {
        "agent_action_id": str(action.id),
        "run_id": str(run.id),
        "message_type": "result",
        "goal_status": outcome.goal_status,
        "verification_status": outcome.verification_status,
        "run_status": run.status,
        "message_subtype": "mcp_tool_result",
        "mcp_result": mcp,
    }
    return "\n".join(lines), metadata


class AgentActionLifecycle:
    """Run ↔ Action 状态同步与终态回写。"""

    def __init__(self) -> None:
        self._actions = None  # 惰性 AgentActionRepository（需要 db）
        # M-02:与其他消息入口共享统一记忆挂载工厂
        self._messages = get_message_service()
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

        # W1-01 所有者守卫：Run 的当前回写所有者是 config_snapshot.
        # agent_action_id（continue 计划确认后切换归属）。非所有者的旧
        # Action 已冻结历史结果——不能拿当前 Run 终态覆盖它已存的结局
        # （旧 Action 的 reconcile/GET 补写在此短路）。
        owner_id = (run.config_snapshot or {}).get("agent_action_id")
        if owner_id and str(owner_id) != str(action_id):
            logger.info(
                "Action %s 不是 Run %s 的当前所有者（owner=%s），保留已存结果",
                action_id, run.id, owner_id,
            )
            return action

        # Run 尚未终态 → 不回写（Dispatcher 在终态后才调用；防御性守卫）
        if run.status not in _RUN_TO_ACTION_STATUS:
            return action
        phase = _run_phase(run, final_state)
        # 同幕次幂等（崩溃重入 / 重复 reconciliation）：本幕已回写过则直接
        # 返回。分段创作（L-3/L-4）让一个 Action 跨越多个 Run 幕次——门上
        # 暂停 → 续跑 → 终态，新幕次必须再次回写，否则续跑后的失败/完成
        # 对用户不可见（看到的永远是门上那句"等待确认"）。
        # last_synced_phase 为 NULL 或旧格式（无 v2 前缀）的存量行保持冻结
        # 语义（旧行为不变）；旧 v2 格式（无 g 段）按世代 0 归一比较。
        if action.result is not None and (
            action.last_synced_phase is None
            or _stored_phase_matches(action.last_synced_phase, phase)
            or not action.last_synced_phase.startswith("v2:")
        ):
            return action

        outcome = await AgentOutcomeService().evaluate(
            db,
            action=action,
            run=run,
            final_state=final_state,
        )

        # 状态回写：cancelled/failed 允许 queued 直达；completed/needs_review
        # 需先补 queued→running（Worker 在标记 running 前崩溃的场景）；
        # needs_review 的 Action 在续跑后允许迁移到新幕次终态。
        # 同状态新幕次（needs_review→needs_review：下一批门 / 修订轮用尽，
        # 或 failed→failed：重试后再次失败）不走状态机自迁移——状态不变，
        # 但 result 与幕次键必须更新，结局与消息才对用户可见。
        target_status = _RUN_TO_ACTION_STATUS[run.status]
        repo = AgentActionRepository(db)
        try:
            if action.status == target_status:
                action = await repo.sync_result(
                    action_id,
                    result=outcome.model_dump(mode="json"),
                    last_synced_phase=phase,
                )
            else:
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
                    expected_statuses={"queued", "running", "needs_review"},
                    result=outcome.model_dump(mode="json"),
                    last_synced_phase=phase,
                )
        except AgentStateTransitionError:
            # 并发回写胜者已写入（result hash 一致性由确定性评估保证）
            await db.rollback()
            action = await AgentActionRepository(db).get_for_update(action_id)
            if action is None or action.status not in _TERMINAL_ACTION_STATUSES:
                raise
            return action

        # assistant 结果消息（幂等：同 action + 幕次键只追加一次）
        result_message = await self._append_result_message(
            db, action, run, outcome, phase=phase, final_state=final_state
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
        phase: str | None = None,
    ) -> Message | None:
        conditions = [
            Message.conversation_id == conversation_id,
            Message.kind == kind,
            Message.message_metadata["agent_action_id"].astext == str(agent_action_id),
            Message.message_metadata["message_type"].astext == message_type,
        ]
        if phase is not None:
            # 同一 Action 可有多条结果消息（门上暂停、每幕终态各一条），
            # 幂等键必须是幕次键（含 attempt）：只按 run_status 查重会把
            # 续跑/重试后的新结局误判为已写入（两次 needs_review 互吞）
            conditions.append(
                Message.message_metadata["phase"].astext == phase
            )
        result = await db.execute(select(Message).where(*conditions))
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
        phase: str,
        final_state: dict[str, Any] | None = None,
    ) -> Any:
        existing = await self._find_message(
            db, action.conversation_id,
            kind="action_result", agent_action_id=action.id, message_type="result",
            phase=phase,
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
                "run_status": run.status,
                "phase": phase,
            }
        else:
            if run.status == "failed":
                # 失败必须如实、可读、给出路——这是用户信任对话状态的基础
                from app.skills.agent_shortcut import describe_run_failure

                if run.error_code == "WORKFLOW_RECOVERY_EXHAUSTED":
                    # 耗尽Run再「重试」只会立刻再耗尽：指重新发起而非重试
                    content = (
                        f"创作任务失败了：{describe_run_failure(run)}。\n"
                        "该任务无法再自动重试；已生成的产物不受影响，"
                        "建议重新发起创作，或直接告诉我你想调整什么。"
                    )
                else:
                    content = (
                        f"创作任务失败了：{describe_run_failure(run)}。\n"
                        "已生成的产物不受影响。输入「重试」可从断点重新执行，"
                        "或直接告诉我你想调整什么。"
                    )
            elif (final_state or {}).get("mcp_result") is not None:
                # MCP 外部工具调用完成（MCP-03）：可读摘要 + 受限结果文本；
                # 结果内容为外部不可信文本，只作内容展示，不改变控制流
                content, metadata = _render_mcp_result_message(action, run, outcome, final_state or {})
                return await self._append_message(
                    db, action.conversation_id,
                    role="assistant",
                    content=content,
                    kind="action_result",
                    metadata=metadata,
                )
            else:
                # 可读自然文案；状态详情由计划卡的 OutcomeView 承载，避免重复。
                # W1-04：未完成（已知失败）与待判断（缺核验证据）分开陈述，
                # 不把未验证要求伪装成已失败
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
                unverified = [
                    c for c in outcome.constraint_checks if c.status == "unverified"
                ]
                if unverified:
                    lines.append(
                        f"以下 {len(unverified)} 项创作要求需要你阅读本轮稿件后判断："
                    )
                    lines.extend(f"待判断：{c.constraint}" for c in unverified)
                content = "\n".join(lines)
            metadata = {
                "agent_action_id": str(action.id),
                "run_id": str(run.id),
                "message_type": "result",
                "goal_status": outcome.goal_status,
                "verification_status": outcome.verification_status,
                "constraint_checks": [
                    c.model_dump(mode="json") for c in outcome.constraint_checks
                ],
                "evidence_refs": [
                    r.model_dump(mode="json") for r in outcome.evidence_refs
                ],
                "run_status": run.status,
                "phase": phase,
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

