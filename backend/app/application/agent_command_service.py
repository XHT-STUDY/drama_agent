"""Agent 命令服务(J-04):对话 Turn 三段式执行与 Action 确认。

职责边界:
- Turn 分三段执行(短事务 A → 事务外 Planner → 短事务 B),LLM 调用期间不持任何事务/行锁;
- 计划(ActionPlan)由服务端按 intent 模板生成,Planner 只提供意图/约束/影响;
- 确认只消费服务端持久化的 Plan,创建 Run 前做来源快照过期检测与单活跃 Run 守卫。

幂等语义:
- AgentTurn 以 (project_id, idempotency_key) 为请求收据,重复请求返回持久化结果;
- WorkflowRun 以 agent-action:{action_id} 为幂等键,确认接口可安全重试。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.application.agent_context_service import AgentContextService
from app.application.conversation_service import ConversationService, MessageService
from app.application.run_service import RunService
from app.application.workflow_dispatcher import schedule_worker
from app.core.config import Settings
from app.core.errors import (
    AgentActionStaleError,
    AgentStateTransitionError,
    AppError,
    IdempotencyKeyReusedError,
    InvalidActiveContextError,
    NotFoundError,
    ProjectHasActiveRunError,
    UnsupportedAgentIntentError,
)
from app.core.logging import get_logger
from app.db.models.agent_action import AgentAction
from app.db.models.agent_turn import AgentTurn
from app.db.models.artifact import Artifact
from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.db.models.project import Project
from app.db.models.workflow_run import WorkflowRun
from app.db.repositories.agent_actions import AgentActionRepository
from app.db.repositories.agent_turns import AgentTurnRepository
from app.db.repositories.artifacts import ArtifactRepository
from app.domain.agent_command import (
    ActionStep,
    ActionTarget,
    AgentActionPlan,
    AgentActionResponse,
    AgentActionStatus,
    AgentCommand,
    AgentIntent,
    AgentOutcome,
    AgentTurnResponse,
    AgentTurnStatus,
    AgentTurnType,
    ArtifactSnapshot,
    CreateScriptCommand,
    EvaluateCommand,
    compute_request_hash,
)
from app.domain.agent_planner import AgentPlannerInput, AgentPlannerOutput
from app.domain.conversation import ConversationCreate, MessageCreate
from app.prompts.loader import PromptLoader
from app.skills.agent_command_planner import (
    DEFAULT_AVAILABLE_INTENTS,
    AgentCommandPlannerSkill,
)

logger = get_logger(__name__)

# intent → WorkflowRun action 的固定映射;explain 不创建 Run(DESIGN §6.2)。
INTENT_RUN_ACTION: dict[str, str] = {
    "create_script": "create_script",
    "evaluate": "evaluate",
    "revise_script": "revise_script",
    "revise_outline": "revise_outline",
}

_TERMINAL_TURN_STATUSES = frozenset({"needs_input", "answered", "action_proposed", "failed"})

MessageKind = Literal["text", "clarification", "action_plan", "action_result", "error"]


class AgentCommandService:
    """对话命令的编排服务:Turn 收据、Planner 调度与 Action 确认。"""

    def __init__(
        self,
        *,
        settings: Settings,
        planner_agent: BaseAgent,
        prompt_loader: PromptLoader | None = None,
        planner_skill: Any | None = None,
        run_service: RunService | None = None,
        context_service: AgentContextService | None = None,
        message_service: MessageService | None = None,
    ) -> None:
        self._settings = settings
        self._planner_agent = planner_agent
        self._prompt_loader = prompt_loader or PromptLoader()
        self._planner_skill = planner_skill or AgentCommandPlannerSkill()
        self._run_service = run_service or RunService()
        self._context_service = context_service or AgentContextService(settings=settings)
        self._message_service = message_service or MessageService()
        self._conversation_service = ConversationService()

    # ========================================================================
    # Turn:三段式执行
    # ========================================================================

    async def create_turn(
        self,
        db: AsyncSession,
        *,
        project_id: uuid.UUID,
        content: str,
        conversation_id: uuid.UUID | None,
        active_context: Any | None,
        idempotency_key: str,
    ) -> tuple[AgentTurnResponse, int]:
        """执行一次对话 Turn,返回 (响应快照, HTTP 状态码)。

        - 200:本次(或重复请求的)终态结果,含 failed;
        - 202:Turn 仍在他人有效租约下规划中。
        """
        request_hash = compute_request_hash(
            {
                "content": content,
                "conversation_id": str(conversation_id) if conversation_id else None,
                "active_context": (
                    active_context.model_dump(mode="json") if active_context else None
                ),
            }
        )
        turn_repo = AgentTurnRepository(db)

        # ---- 事务 A:校验 + get-or-create Turn + 一条 user 消息(全程短事务) ----
        project = await self._load_project(db, project_id)

        # 幂等预检:命中既有 Turn 时直接复用,不再追加消息或调用模型。
        existing = await turn_repo.get_by_idempotency_key(project_id, idempotency_key)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IdempotencyKeyReusedError(detail="同一 idempotency_key 已用于不同的请求载荷")
            existing_id = existing.id  # rollback 会过期 ORM 对象,先捕获 ID
            await db.rollback()
            return await self._duplicate_outcome(db, existing_id)

        conversation = await self._resolve_conversation(
            db, project, conversation_id, fallback_title=content
        )
        if active_context is not None:
            # 在持久化任何数据前拒绝非法活动上下文,避免留下无法完成的 Turn。
            await self._context_service.validate_active_context(db, project, active_context)

        message = await self._append_message(
            db,
            conversation.id,
            role="user",
            content=content,
            kind="text",
            metadata={"idempotency_key": idempotency_key},
        )
        turn, created = await turn_repo.get_or_create(
            project_id=project.id,
            conversation_id=conversation.id,
            user_message_id=message.id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if not created:
            # 并发同 key 竞争败者:丢弃本次多追加的消息,复用胜者结果。
            loser_turn_id = turn.id  # rollback 前捕获,避免属性过期触发隐式刷新
            await db.rollback()
            return await self._duplicate_outcome(db, loser_turn_id)
        turn_id = turn.id
        await db.execute(
            update(Message)
            .where(Message.id == message.id)
            .values(
                message_metadata={
                    "agent_turn_id": str(turn_id),
                    "idempotency_key": idempotency_key,
                }
            )
        )
        await db.commit()  # 事务 A 提交,释放全部行锁

        # ---- 事务外:原子领取 planning lease(独立短事务) ----
        lease_owner = f"agent-turn:{uuid.uuid4().hex[:16]}"
        lease_expires_at = datetime.now(UTC) + timedelta(
            seconds=self._settings.agent_turn_lease_seconds
        )
        claimed = await turn_repo.claim_planning_lease(
            turn_id, lease_owner=lease_owner, lease_expires_at=lease_expires_at
        )
        await db.commit()  # lease 立即持久化;此后会话无打开事务
        if claimed is None:
            return await self._duplicate_outcome(db, turn_id)

        # ---- 构建有界上下文 + 未解决轮数(读事务,读完关闭) ----
        try:
            context_text, _manifest = await self._context_service.build(
                db, project, conversation, active_context, content
            )
            unresolved = await self._count_unresolved_turns(
                db, conversation.id, exclude_turn_id=turn_id
            )
            await db.commit()  # 关闭只读事务 → Planner 调用期间零事务
        except Exception as exc:
            logger.exception("Planner 上下文构建失败: turn=%s", turn_id)
            return await self._fail_turn(db, turn_id, conversation.id, lease_owner, exc)

        planner_input = AgentPlannerInput(
            user_request=content,
            project_title=project.title or "",
            target_episode_count=max(1, project.target_episode_count),
            available_intents=list(DEFAULT_AVAILABLE_INTENTS),
            active_context=active_context,
            project_context=context_text[:12000],
            unresolved_turn_count=unresolved,
        )

        # ---- Planner(唯一无事务段) ----
        try:
            output = await self._planner_skill.execute(
                {
                    "input": planner_input,
                    "agent": self._planner_agent,
                    "prompt_loader": self._prompt_loader,
                }
            )
        except Exception as exc:
            logger.exception("Planner 执行失败: turn=%s", turn_id)
            return await self._fail_turn(db, turn_id, conversation.id, lease_owner, exc)

        # ---- 事务 B:写入终态并终结 Turn ----
        try:
            final_turn = await self._finalize_turn(
                db, turn_id, conversation.id, lease_owner, output, project, content
            )
        except AgentStateTransitionError:
            # 租约被接管(超期后他人完成):放弃本次结果,返回持久化胜者。
            await db.rollback()
            return await self._duplicate_outcome(db, turn_id)
        return await self._turn_response(db, final_turn), 200

    async def get_turn(self, db: AsyncSession, turn_id: uuid.UUID) -> AgentTurnResponse:
        """查询 Turn 的持久化快照(含关联 Action)。"""
        turn = await AgentTurnRepository(db).get(turn_id)
        if turn is None:
            raise NotFoundError(detail=f"AgentTurn 不存在: {turn_id}", code="AGENT_TURN_NOT_FOUND")
        return await self._turn_response(db, turn)

    # ========================================================================
    # Action:确认 / 拒绝 / 查询
    # ========================================================================

    async def get_action(self, db: AsyncSession, action_id: uuid.UUID) -> AgentActionResponse:
        """查询 Action 的持久化快照。"""
        action = await AgentActionRepository(db).get(action_id)
        if action is None:
            raise NotFoundError(
                detail=f"AgentAction 不存在: {action_id}", code="AGENT_ACTION_NOT_FOUND"
            )
        return self._action_response(action)

    async def confirm_action(
        self, db: AsyncSession, action_id: uuid.UUID
    ) -> tuple[AgentActionResponse, WorkflowRun]:
        """确认 proposed Action:过期检测 → 创建/复用 Run → Action→queued。"""
        action_repo = AgentActionRepository(db)
        artifact_repo = ArtifactRepository(db)

        action = await action_repo.get_for_update(action_id)
        if action is None:
            raise NotFoundError(
                detail=f"AgentAction 不存在: {action_id}", code="AGENT_ACTION_NOT_FOUND"
            )

        # 重复确认:直接返回原 Run,不再创建。
        if action.run_id is not None:
            run = await self._run_service.get_run(db, action.run_id)
            await db.commit()
            return self._action_response(action), run

        if action.status != "proposed":
            current_status = action.status  # rollback 前捕获,避免属性过期触发隐式刷新
            await db.rollback()
            raise AgentStateTransitionError(
                detail=f"Action 当前状态 {current_status} 不可确认", entity="AGENT_ACTION"
            )

        # 行锁内做过期检测:每个快照必须仍是 (project, type, episode) 的最新 valid 版本。
        for raw in action.source_artifact_ids or []:
            snapshot = ArtifactSnapshot.model_validate(raw)
            current = await artifact_repo.get_latest_valid(
                action.project_id, snapshot.artifact_type, snapshot.episode_number
            )
            if (
                current is None
                or current.id != snapshot.artifact_id
                or current.version != snapshot.version
                or current.checksum != snapshot.checksum
            ):
                await action_repo.transition(
                    action_id, "stale", expected_statuses={"proposed"}
                )
                await db.commit()  # 先持久化 stale 再抛错,保证状态可见
                raise AgentActionStaleError(detail="计划基于的 Artifact 已更新,请重新发起规划")

        run_action = INTENT_RUN_ACTION.get(action.intent)
        if run_action is None:
            intent = action.intent  # rollback 前捕获
            await db.rollback()
            raise UnsupportedAgentIntentError(detail=f"intent 不支持确认执行: {intent}")

        plan = AgentActionPlan.model_validate(action.plan)  # 只信服务端持久化 Plan
        try:
            run = await self._run_service.create_run(
                db,
                project_id=action.project_id,
                action=run_action,
                config=self._build_run_config(plan),
                idempotency_key=f"agent-action:{action_id}",
            )
        except ProjectHasActiveRunError:
            raise
        except AgentStateTransitionError:
            raise
        except AppError as exc:
            # 并发确认被 partial unique index 拦截时,RunService 已重查过同键 Run;
            # 仍冲突则转为对用户可读的单活跃 Run 错误。
            await db.rollback()
            if exc.code == "RUN_ALREADY_ACTIVE":
                raise ProjectHasActiveRunError(detail="项目已有任务运行中,不能同时执行两个计划") from exc
            raise

        await action_repo.transition(action_id, "queued", run_id=run.id)
        await db.commit()  # durable 后再做 best-effort 唤醒
        schedule_worker(run.id, run.action, run.config_snapshot or {})
        return self._action_response(action), run

    async def reject_action(
        self, db: AsyncSession, action_id: uuid.UUID
    ) -> AgentActionResponse:
        """拒绝 proposed Action(仅 proposed→rejected)。"""
        action_repo = AgentActionRepository(db)
        action = await action_repo.get_for_update(action_id)
        if action is None:
            raise NotFoundError(
                detail=f"AgentAction 不存在: {action_id}", code="AGENT_ACTION_NOT_FOUND"
            )
        action = await action_repo.transition(
            action_id, "rejected", expected_statuses={"proposed"}
        )
        await db.commit()
        return self._action_response(action)

    # ========================================================================
    # 私有辅助
    # ========================================================================

    async def _load_project(self, db: AsyncSession, project_id: uuid.UUID) -> Project:
        result = await db.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if project is None or project.deleted_at is not None:
            raise NotFoundError(detail=f"项目不存在: {project_id}", code="PROJECT_NOT_FOUND")
        return project

    async def _resolve_conversation(
        self,
        db: AsyncSession,
        project: Project,
        conversation_id: uuid.UUID | None,
        *,
        fallback_title: str,
    ) -> Conversation:
        """解析会话;conversation_id 为空时创建会话并取首条消息前 30 字作标题。"""
        if conversation_id is None:
            created = await self._conversation_service.create(
                db, project.id, ConversationCreate(title=fallback_title.strip()[:30])
            )
            result = await db.execute(
                select(Conversation).where(Conversation.id == created.id)
            )
            return result.scalar_one()
        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if conversation is None or conversation.deleted_at is not None:
            raise NotFoundError(
                detail=f"会话不存在: {conversation_id}", code="CONVERSATION_NOT_FOUND"
            )
        if conversation.project_id != project.id:
            # 跨项目会话视作活动上下文非法,在追加消息前拒绝。
            raise InvalidActiveContextError(detail="会话不属于当前项目")
        return conversation

    async def _count_unresolved_turns(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        *,
        exclude_turn_id: uuid.UUID | None = None,
    ) -> int:
        """统计会话内从最新往回连续处于 needs_input 的 Turn 数。

        排除当前正在执行的 Turn(它总是最新的 planning 行)。
        """
        stmt = select(AgentTurn.status).where(
            AgentTurn.conversation_id == conversation_id
        )
        if exclude_turn_id is not None:
            stmt = stmt.where(AgentTurn.id != exclude_turn_id)
        result = await db.execute(stmt.order_by(AgentTurn.created_at.desc()).limit(10))
        count = 0
        for (status,) in result.all():
            if status == "needs_input":
                count += 1
            else:
                break
        return count

    async def _append_message(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        *,
        role: str,
        content: str,
        kind: MessageKind,
        metadata: dict[str, Any],
    ) -> Any:
        data = MessageCreate(role=role, content=content, kind=kind, metadata=metadata)
        return await self._message_service.append(db, conversation_id, data)

    async def _duplicate_outcome(
        self, db: AsyncSession, turn_id: uuid.UUID
    ) -> tuple[AgentTurnResponse, int]:
        """重复请求的统一出口:终态返回 200,仍规划中返回 202。"""
        turn = await AgentTurnRepository(db).get(turn_id)
        if turn is None:
            raise NotFoundError(
                detail=f"AgentTurn 不存在: {turn_id}", code="AGENT_TURN_NOT_FOUND"
            )
        status_code = 200 if turn.status in _TERMINAL_TURN_STATUSES else 202
        return await self._turn_response(db, turn), status_code

    async def _finalize_turn(
        self,
        db: AsyncSession,
        turn_id: uuid.UUID,
        conversation_id: uuid.UUID,
        lease_owner: str,
        output: AgentPlannerOutput,
        project: Project,
        user_request: str,
    ) -> AgentTurn:
        """事务 B:按 Planner 输出写入 clarification/answer/plan 并终结 Turn。"""
        turn_repo = AgentTurnRepository(db)
        planner_snapshot = output.model_dump(mode="json")

        if output.turn_type == "clarification":
            msg = await self._append_message(
                db,
                conversation_id,
                role="assistant",
                content=output.clarification_question or "",
                kind="clarification",
                metadata={"agent_turn_id": str(turn_id)},
            )
            turn = await turn_repo.transition(
                turn_id,
                "needs_input",
                expected_statuses={"planning"},
                lease_owner=lease_owner,
                planner_output=planner_snapshot,
                response_message_id=msg.id,
            )
        elif output.turn_type == "answer" or output.intent == "explain":
            # explain 归一化为只读答复,不落 AgentAction。
            content = output.answer or self._render_explain_answer(output)
            msg = await self._append_message(
                db,
                conversation_id,
                role="assistant",
                content=content,
                kind="text",
                metadata={"agent_turn_id": str(turn_id)},
            )
            turn = await turn_repo.transition(
                turn_id,
                "answered",
                expected_statuses={"planning"},
                lease_owner=lease_owner,
                planner_output=planner_snapshot,
                response_message_id=msg.id,
            )
        else:
            plan, snapshots = await self._build_action_plan(db, project, output, user_request)
            action = AgentAction(
                project_id=project.id,
                conversation_id=conversation_id,
                agent_turn_id=turn_id,
                replan_depth=0,
                intent=plan.intent,
                status="proposed",
                requires_confirmation=True,
                plan=plan.model_dump(mode="json"),
                source_artifact_ids=[s.model_dump(mode="json") for s in snapshots],
            )
            db.add(action)
            await db.flush()
            msg = await self._append_message(
                db,
                conversation_id,
                role="assistant",
                content=self._render_plan_message(plan),
                kind="action_plan",
                metadata={
                    "agent_turn_id": str(turn_id),
                    "agent_action_id": str(action.id),
                },
            )
            turn = await turn_repo.transition(
                turn_id,
                "action_proposed",
                expected_statuses={"planning"},
                lease_owner=lease_owner,
                planner_output=planner_snapshot,
                response_message_id=msg.id,
            )
        await db.commit()
        return turn

    async def _fail_turn(
        self,
        db: AsyncSession,
        turn_id: uuid.UUID,
        conversation_id: uuid.UUID,
        lease_owner: str,
        exc: Exception,
    ) -> tuple[AgentTurnResponse, int]:
        """Planner 失败:Turn→failed + error 消息;不创建 AgentAction 或 Run。"""
        error_code = exc.code if isinstance(exc, AppError) else "PLANNER_FAILED"
        error_detail = (str(exc) or exc.__class__.__name__)[:2000]
        try:
            msg = await self._append_message(
                db,
                conversation_id,
                role="assistant",
                content="未能理解本次请求,请重试或使用示例表达。",
                kind="error",
                metadata={"agent_turn_id": str(turn_id), "error_code": error_code},
            )
            await AgentTurnRepository(db).transition(
                turn_id,
                "failed",
                expected_statuses={"planning"},
                lease_owner=lease_owner,
                error_code=error_code,
                error_detail=error_detail,
                response_message_id=msg.id,
            )
            await db.commit()
        except Exception:
            logger.exception("写入 Turn 失败状态时出错: turn=%s", turn_id)
            await db.rollback()
        return await self._duplicate_outcome(db, turn_id)

    async def _build_action_plan(
        self,
        db: AsyncSession,
        project: Project,
        output: AgentPlannerOutput,
        user_request: str,
    ) -> tuple[AgentActionPlan, list[ArtifactSnapshot]]:
        """把 Planner 输出转换为服务端模板化的非执行计划与来源快照。"""
        constraints = list(output.constraints)
        expected_impact = list(output.expected_impact)
        intent: AgentIntent = "create_script"  # 分支内按白名单重赋值

        if output.intent == "create_script":
            outline_count = self._settings.mvp_outline_count
            script_count = self._settings.mvp_script_count
            command: AgentCommand = CreateScriptCommand(
                user_input=user_request,
                outline_count=outline_count,
                script_count=script_count,
            )
            target = ActionTarget(target_type="project")
            goal = f"根据用户输入创建短剧剧本:{user_request}"[:2000]
            steps = [
                ActionStep(
                    step_id="requirement",
                    title="解析创作需求",
                    description="把用户输入归一化为结构化创作需求,锁定题材、主线与边界约束",
                ),
                ActionStep(
                    step_id="story_bible",
                    title="生成 Story Bible",
                    description="基于需求生成人物、世界观与核心设定",
                ),
                ActionStep(
                    step_id="outline",
                    title=f"生成 {outline_count} 集分集大纲",
                    description="按目标集数产出分集大纲,保持主线连续",
                ),
                ActionStep(
                    step_id="scripts",
                    title=f"生成前 {script_count} 集剧本",
                    description="逐集生成剧本初稿,遵循大纲与 Story Bible",
                ),
                ActionStep(
                    step_id="evaluate",
                    title="自动评估与修订决策",
                    description="对生成剧本执行评估,低分集进入自动修订或人工复核",
                ),
            ]
            snapshots: list[ArtifactSnapshot] = []
        elif output.intent == "evaluate":
            episode = output.target.episode_number if output.target else None
            scope: Literal["project", "episode"] = (
                "episode" if episode is not None else "project"
            )
            intent = "evaluate"
            command = EvaluateCommand(scope=scope, episode_number=episode)
            target = ActionTarget(target_type="evaluation", episode_number=episode)
            scope_label = f"第 {episode} 集" if episode is not None else "整个项目"
            goal = f"评估{scope_label}的最新有效剧本并产出报告"[:2000]
            steps = [
                ActionStep(
                    step_id="collect",
                    title="收集最新剧本版本",
                    description="按集数取每集最新有效剧本作为评估对象",
                ),
                ActionStep(
                    step_id="evaluate",
                    title="逐集执行评估",
                    description="使用评分维度与 Rubric 对剧本打分并列出问题",
                ),
                ActionStep(
                    step_id="report",
                    title="生成评估报告",
                    description="汇总每集得分与修订建议,产出评估 Artifact",
                ),
            ]
            snapshots = await self._script_snapshots(db, project.id, episode)
        else:
            # Planner 白名单已限定意图;到达这里说明服务端与 Planner 白名单漂移,直接拒绝。
            raise UnsupportedAgentIntentError(
                detail=f"intent 不支持生成执行计划: {output.intent}"
            )

        plan = AgentActionPlan(
            goal=goal,
            intent=intent,
            command=command,
            target=target,
            constraints=constraints,
            steps=steps,
            expected_impact=expected_impact,
        )
        return plan, snapshots

    async def _script_snapshots(
        self, db: AsyncSession, project_id: uuid.UUID, episode: int | None
    ) -> list[ArtifactSnapshot]:
        """为 evaluate 计划建立受评估剧本的来源快照。"""
        artifact_repo = ArtifactRepository(db)
        snapshots: list[ArtifactSnapshot] = []
        artifacts: list[Artifact] = []
        if episode is not None:
            latest = await artifact_repo.get_latest_valid(project_id, "script_draft", episode)
            if latest is not None:
                artifacts = [latest]
        else:
            artifacts = await artifact_repo.list_by_project(
                project_id, "script_draft", offset=0, limit=1000
            )
            # 每集只保留版本号最高的 valid 版本。
            per_episode: dict[int, Artifact] = {}
            for artifact in artifacts:
                if artifact.status != "valid":
                    continue
                current = per_episode.get(artifact.episode_number)
                if current is None or artifact.version > current.version:
                    per_episode[artifact.episode_number] = artifact
            artifacts = [per_episode[ep] for ep in sorted(per_episode)]
        for artifact in artifacts:
            if artifact.checksum is None:
                continue  # 无 checksum 的资产无法做过期比对,不纳入快照
            snapshots.append(
                ArtifactSnapshot(
                    artifact_id=artifact.id,
                    artifact_type=artifact.type,
                    episode_number=artifact.episode_number,
                    version=artifact.version,
                    checksum=artifact.checksum,
                )
            )
        return snapshots

    def _build_run_config(self, plan: AgentActionPlan) -> dict[str, Any]:
        """按 intent 生成与 Dispatcher 读取格式对齐的 Run config。"""
        command = plan.command
        if isinstance(command, CreateScriptCommand):
            return {
                "options": {
                    "user_input": command.user_input,
                    "source_type": "idea",
                    "outline_count": command.outline_count,
                    "script_count": command.script_count,
                }
            }
        if isinstance(command, EvaluateCommand):
            options: dict[str, Any] = {"scope": command.scope}
            if command.episode_number is not None:
                options["episode_number"] = command.episode_number
            return {"options": options}
        return {"options": command.model_dump(mode="json")}

    def _render_plan_message(self, plan: AgentActionPlan) -> str:
        """把计划渲染为用户可读的 action_plan 消息文本。"""
        lines = [f"计划:{plan.goal}", ""]
        for step in plan.steps:
            lines.append(f"- {step.title}:{step.description}")
        if plan.expected_impact:
            lines.append("")
            lines.append("预期影响:" + ";".join(plan.expected_impact))
        lines.append("")
        lines.append("回复「确认」后开始执行。")
        return "\n".join(lines)

    def _render_explain_answer(self, output: AgentPlannerOutput) -> str:
        """模型未直接给 answer 时,由 steps/影响拼出只读解释。"""
        parts: list[str] = []
        if output.answer:
            parts.append(output.answer)
        if output.steps:
            parts.extend(f"- {s.title}:{s.description}" for s in output.steps)
        if not parts:
            parts.append("(模型未返回可读答复)")
        return "\n".join(parts)

    async def _turn_response(self, db: AsyncSession, turn: AgentTurn) -> AgentTurnResponse:
        action = await AgentActionRepository(db).get_by_turn_id(turn.id)
        return AgentTurnResponse(
            id=turn.id,
            project_id=turn.project_id,
            conversation_id=turn.conversation_id,
            user_message_id=turn.user_message_id,
            idempotency_key=turn.idempotency_key,
            request_hash=turn.request_hash,
            status=cast(AgentTurnStatus, turn.status),
            turn_type=cast(AgentTurnType | None, turn.turn_type),
            response_message_id=turn.response_message_id,
            action_id=action.id if action is not None else None,
            error_code=turn.error_code,
            error_detail=turn.error_detail,
            created_at=turn.created_at,
            updated_at=turn.updated_at,
        )

    def _action_response(self, action: AgentAction) -> AgentActionResponse:
        return AgentActionResponse(
            id=action.id,
            project_id=action.project_id,
            conversation_id=action.conversation_id,
            agent_turn_id=action.agent_turn_id,
            parent_action_id=action.parent_action_id,
            replan_depth=action.replan_depth,
            intent=cast(AgentIntent, action.intent),
            status=cast(AgentActionStatus, action.status),
            requires_confirmation=action.requires_confirmation,
            plan=AgentActionPlan.model_validate(action.plan),
            source_artifact_ids=[
                ArtifactSnapshot.model_validate(raw)
                for raw in (action.source_artifact_ids or [])
            ],
            result=AgentOutcome.model_validate(action.result) if action.result else None,
            run_id=action.run_id,
            created_at=action.created_at,
            updated_at=action.updated_at,
        )
