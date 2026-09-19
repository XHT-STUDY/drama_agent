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

import contextlib
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.application.agent_context_service import AgentContextService
from app.application.conversation_service import ConversationService, MessageService
from app.application.run_service import RunService
from app.application.workflow_dispatcher import schedule_worker
from app.artifacts.store import ArtifactStore
from app.core.config import Settings
from app.core.errors import (
    AgentActionStaleError,
    AgentStateTransitionError,
    AppError,
    IdempotencyKeyReusedError,
    InvalidActiveContextError,
    MCPToolArgumentInvalidError,
    MCPToolNotAvailableError,
    NotFoundError,
    OutlineNotFoundForRevisionError,
    ProjectHasActiveRunError,
    RunNotRetryableError,
    RunStageStaleError,
    ScriptNotFoundForRevisionError,
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
    ActiveArtifactContext,
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
    ContinueCommand,
    CreateScriptCommand,
    EvaluateCommand,
    MCPToolCallCommand,
    ReviseOutlineCommand,
    ReviseScriptCommand,
    compute_request_hash,
)
from app.domain.agent_planner import (
    AgentPlannerInput,
    AgentPlannerOutput,
    PlannerExternalTool,
)
from app.domain.conversation import ConversationCreate, MessageCreate
from app.llm.budget import enter_run, exit_run
from app.memory.wiring import get_message_service
from app.prompts.loader import PromptLoader
from app.skills.agent_command_planner import (
    DEFAULT_AVAILABLE_INTENTS,
    AgentCommandPlannerSkill,
)
from app.skills.agent_shortcut import (
    describe_run_failure,
    detect_shortcut,
    find_active_run,
    find_gated_run,
    find_latest_failed_run,
    find_latest_proposed_action,
    find_latest_run,
    render_continue_answer,
)

logger = get_logger(__name__)

# 单 Turn 模型调用护栏（W1-03）：Planner + 解释共用一个预算上下文
_TURN_SOFT_CALLS = 4
_TURN_HARD_CALLS = 6

# intent → WorkflowRun action 的固定映射;explain 不创建 Run(DESIGN §6.2)。
INTENT_RUN_ACTION: dict[str, str] = {
    "create_script": "create_script",
    "evaluate": "evaluate",
    "revise_script": "revise_script",
    "revise_outline": "revise_outline",
    # MCP-03：外部工具调用复用确认链路与单活跃 Run 约束
    "use_external_tool": "mcp_tool_call",
}

# Planner 有界工具目录上限（MCP-03：按描述匹配后最多提供 20 个）
MCP_PLANNER_TOOL_LIMIT = 20

_MCP_TOOL_ID_RE = re.compile(r"^mcp__[a-z0-9_]+__[^\s_][^\s]*$")

_TERMINAL_TURN_STATUSES = frozenset({"needs_input", "answered", "action_proposed", "failed"})

MessageKind = Literal["text", "clarification", "action_plan", "action_result", "error"]


# ========================================================================
# 服务端计划模板（模块级函数）——Turn 规划（J-04/06/08）与一次后续计划
# （J-09 lifecycle）共用同一模板，保证父子 Action 的计划语义一致。
# 返回 (intent, command, target, goal, steps, snapshots)。
# ========================================================================


def build_revise_script_plan(
    *, source: Artifact, constraints: list[str], user_request: str | None = None
) -> tuple[str, ReviseScriptCommand, ActionTarget, str, list[ActionStep], list[ArtifactSnapshot]]:
    """revise_script 计划模板：source 为服务端解析的目标集最新 valid 剧本。"""
    episode = source.episode_number
    steps = [
        ActionStep(
            step_id="prepare_target",
            title="锁定修订目标",
            description="解析目标剧本版本，锁定原稿与设定/大纲上下文",
        ),
        ActionStep(
            step_id="ensure_evaluation",
            title="补齐目标评估",
            description="目标剧本缺少评估时先仅评估该集并持久化报告",
        ),
        ActionStep(
            step_id="revise",
            title="生成修订计划与新稿",
            description="把用户约束写入修订计划，产出候选新稿（保持 draft）",
        ),
        ActionStep(
            step_id="continuity_check",
            title="连续性检查",
            description="候选稿通过连续性检查后提升为有效版本，失败保留诊断稿",
        ),
        ActionStep(
            step_id="re_evaluate",
            title="重新评估",
            description="对修订后的剧本重新评估，产出对比报告",
        ),
    ]
    snapshots = (
        [
            ArtifactSnapshot(
                artifact_id=source.id,
                artifact_type=source.type,
                episode_number=source.episode_number,
                version=source.version,
                checksum=source.checksum,
            )
        ]
        if source.checksum is not None
        else []
    )
    return (
        "revise_script",
        ReviseScriptCommand(
            source_script_id=source.id,
            episode_number=episode,
            constraints=constraints,
            user_request=user_request,
        ),
        ActionTarget(target_type="script", episode_number=episode),
        f"按用户要求修订第 {episode} 集剧本并重评"[:2000],
        steps,
        snapshots,
    )


def build_revise_outline_plan(
    *, source_outline: Artifact, constraints: list[str], user_request: str | None = None
) -> tuple[str, ReviseOutlineCommand, ActionTarget, str, list[ActionStep], list[ArtifactSnapshot]]:
    """revise_outline 计划模板：source_outline 为项目最新 valid 大纲。"""
    episode_count = len(source_outline.content.get("episodes", []))
    steps = [
        ActionStep(
            step_id="prepare_target",
            title="锁定修订目标",
            description="加载当前最新有效大纲与 Story Bible 锁定事实",
        ),
        ActionStep(
            step_id="revise_outline",
            title="生成修订大纲",
            description="按用户约束输出完整大纲，保持集数与锁定事实不变",
        ),
        ActionStep(
            step_id="impact",
            title="影响分析",
            description="逐字段比较新旧大纲，找出受影响的集与剧本",
        ),
        ActionStep(
            step_id="persist",
            title="版本落库",
            description="新大纲成为最新有效版本，旧版本不可变",
        ),
    ]
    snapshots = (
        [
            ArtifactSnapshot(
                artifact_id=source_outline.id,
                artifact_type=source_outline.type,
                episode_number=source_outline.episode_number,
                version=source_outline.version,
                checksum=source_outline.checksum,
            )
        ]
        if source_outline.checksum is not None
        else []
    )
    return (
        "revise_outline",
        ReviseOutlineCommand(
            source_outline_id=source_outline.id,
            constraints=constraints,
            user_request=user_request,
        ),
        ActionTarget(target_type="outline"),
        f"按用户要求修订分集大纲（{episode_count} 集）并分析影响"[:2000],
        steps,
        snapshots,
    )


def build_evaluate_plan(
    *, episode: int | None
) -> tuple[str, EvaluateCommand, ActionTarget, str, list[ActionStep]]:
    """evaluate 计划模板（快照由调用方按最新剧本建立）。"""
    scope: Literal["project", "episode"] = "episode" if episode is not None else "project"
    scope_label = f"第 {episode} 集" if episode is not None else "整个项目"
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
    return (
        "evaluate",
        EvaluateCommand(scope=scope, episode_number=episode),
        ActionTarget(target_type="evaluation", episode_number=episode),
        f"评估{scope_label}的最新有效剧本并产出报告"[:2000],
        steps,
    )


def render_plan_message(plan: AgentActionPlan) -> str:
    """把计划渲染为用户可读的 action_plan 消息文本（J-04/09 共用）。"""
    lines = [f"计划:{plan.goal}", ""]
    for step in plan.steps:
        lines.append(f"- {step.title}:{step.description}")
    if plan.expected_impact:
        lines.append("")
        lines.append("预期影响:" + ";".join(plan.expected_impact))
    lines.append("")
    lines.append("回复「确认」后开始执行。")
    return "\n".join(lines)


# ========================================================================
# MCP 有界工具目录（MCP-03）——服务端构建、按描述匹配、最多 20 个
# ========================================================================


def _text_tokens(text: str) -> set[str]:
    """请求/描述的粗粒度 token 集合（拉丁词 + CJK 单字与二元组）。"""
    tokens: set[str] = set()
    for word in re.findall(r"[a-zA-Z0-9]+", text.lower()):
        if len(word) >= 2:
            tokens.add(word)
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    tokens.update(cjk)
    tokens.update(a + b for a, b in zip(cjk, cjk[1:], strict=False))
    return tokens


def summarize_input_schema(schema: dict[str, Any]) -> str:
    """inputSchema → 可读参数摘要（属性名/类型/必填，最多 12 个属性）。"""
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    parts: list[str] = []
    for name, spec in list(properties.items())[:12]:
        prop_type = spec.get("type", "any") if isinstance(spec, dict) else "any"
        flag = "必填" if name in required else "可选"
        parts.append(f"{name}（{prop_type}，{flag}）")
    return "；".join(parts)[:2000]


def build_planner_external_tools(
    definitions: list[Any],
    user_request: str,
    *,
    limit: int = MCP_PLANNER_TOOL_LIMIT,
) -> list[PlannerExternalTool]:
    """catalog 定义 → 有界 Planner 工具目录。

    按描述与用户请求的 token 重叠排序（防大目录撑爆上下文），最多
    limit 个；描述与参数摘要均按上限裁剪（外部不可信内容）。
    """
    request_tokens = _text_tokens(user_request)
    scored: list[tuple[int, Any]] = []
    for definition in definitions:
        text = f"{definition.description or ''} {definition.title or ''} {definition.tool_name}"
        score = len(request_tokens & _text_tokens(text))
        scored.append((score, definition))
    scored.sort(key=lambda pair: (-pair[0], pair[1].qualified_tool_name))

    tools: list[PlannerExternalTool] = []
    for _score, definition in scored[:limit]:
        risk_hints: list[str] = []
        annotations = definition.annotations
        if annotations is not None:
            if annotations.read_only_hint:
                risk_hints.append("只读提示")
            if annotations.destructive_hint:
                risk_hints.append("可能有破坏性")
            if annotations.idempotent_hint:
                risk_hints.append("幂等提示")
            if annotations.open_world_hint:
                risk_hints.append("访问外部世界")
        tools.append(
            PlannerExternalTool(
                qualified_tool_name=definition.qualified_tool_name,
                server_id=definition.server_id,
                display_name=(definition.title or definition.tool_name)[:200],
                description=(definition.description or "")[:2000],
                parameters=summarize_input_schema(definition.input_schema or {}),
                risk_hints=risk_hints[:6],
            )
        )
    return tools


async def _latest_assistant_message_confirmable(db: AsyncSession, conversation_id: uuid.UUID) -> bool:
    """会话内最近一条 assistant 消息是否仍是可确认对象（IR-3 §8.6）。

    action_plan = 待确认计划；action_result 覆盖确认门/执行结果通知。
    最近一条是澄清或普通答复时，"好的"不能再确认更早的计划——回落
    Planner 处理。会话无 assistant 消息同样视为不可确认。
    """
    result = await db.execute(
        select(Message.kind)
        .where(
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
        )
        .order_by(Message.sequence.desc())
        .limit(1)
    )
    kind = result.scalar_one_or_none()
    return kind in {"action_plan", "action_result"}


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
        mcp_manager_provider: Callable[[], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._planner_agent = planner_agent
        self._prompt_loader = prompt_loader or PromptLoader()
        self._planner_skill = planner_skill or AgentCommandPlannerSkill()
        self._run_service = run_service or RunService()
        self._context_service = context_service or AgentContextService(settings=settings)
        # M-02:与其他消息入口共享统一记忆挂载工厂(短期记忆+累计摘要)
        self._message_service = message_service or get_message_service()
        self._conversation_service = ConversationService()
        # MCP-03:惰性读取进程级 MCPClientManager（None = MCP 未启用）
        self._mcp_manager_provider = mcp_manager_provider

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
        target_episode_count: int | None = None,
        staged: bool = True,
    ) -> tuple[AgentTurnResponse, int]:
        """执行一次对话 Turn,返回 (响应快照, HTTP 状态码)。

        - 200:本次(或重复请求的)终态结果,含 failed;
        - 202:Turn 仍在他人有效租约下规划中。
        """
        request_hash = compute_request_hash(
            {
                "content": content,
                "conversation_id": str(conversation_id) if conversation_id else None,
                "active_context": (active_context.model_dump(mode="json") if active_context else None),
                "target_episode_count": target_episode_count,
                "staged": staged,
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

        conversation = await self._resolve_conversation(db, project, conversation_id, fallback_title=content)
        if active_context is not None:
            # 在持久化任何数据前拒绝非法活动上下文,避免留下无法完成的 Turn。
            await self._context_service.validate_active_context(db, project, active_context)

        # 会话 ID 提前捕获为纯值:rollback 会无条件过期 ORM 属性,
        # 异常路径再访问 conversation.id 会触发同步惰性加载(MissingGreenlet)。
        conv_id = conversation.id

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
        lease_expires_at = datetime.now(UTC) + timedelta(seconds=self._settings.agent_turn_lease_seconds)
        claimed = await turn_repo.claim_planning_lease(
            turn_id, lease_owner=lease_owner, lease_expires_at=lease_expires_at
        )
        await db.commit()  # lease 立即持久化;此后会话无打开事务
        if claimed is None:
            return await self._duplicate_outcome(db, turn_id)

        # ---- 确定性短路:确认/续跑短语直达动作,不经 Planner ----
        # 未命中或无可执行目标(无 proposed Action / 无门上 Run)时返回 None,
        # 行为与无短路时完全一致。
        shortcut = detect_shortcut(content)
        if shortcut is not None:
            handled = await self._try_shortcut(db, project, conv_id, shortcut, turn_id=turn_id)
            if handled is not None:
                try:
                    final_turn = await self._finalize_turn(
                        db,
                        turn_id,
                        conv_id,
                        lease_owner,
                        handled,
                        project,
                        content,
                    )
                except AgentStateTransitionError:
                    await db.rollback()
                    return await self._duplicate_outcome(db, turn_id)
                except Exception as exc:
                    logger.exception("Turn 终态写入失败: turn=%s", turn_id)
                    await db.rollback()
                    return await self._fail_turn(db, turn_id, conv_id, lease_owner, exc)
                return await self._turn_response(db, final_turn), 200

        # ---- 构建有界上下文 + 未解决轮数(读事务,读完关闭) ----
        try:
            context_text, _manifest = await self._context_service.build(
                db, project, conversation, active_context, content
            )
            unresolved = await self._count_unresolved_turns(db, conv_id, exclude_turn_id=turn_id)
            await db.commit()  # 关闭只读事务 → Planner 调用期间零事务
        except Exception as exc:
            logger.exception("Planner 上下文构建失败: turn=%s", turn_id)
            return await self._fail_turn(db, turn_id, conv_id, lease_owner, exc)

        catalog = self._mcp_catalog()
        planner_input = AgentPlannerInput(
            user_request=content,
            project_title=project.title or "",
            target_episode_count=max(1, project.target_episode_count),
            available_intents=await self._available_intents(db, project),
            active_context=active_context,
            # W1-03：builder 已按 token 预算裁剪（受保护段超限即抛错），
            # 不再做第二次字符截断——那会把受保护目标切掉
            project_context=context_text,
            unresolved_turn_count=unresolved,
            # MCP-03：有界工具目录（≤20，按描述匹配；空目录时不注入）
            external_tools=(
                build_planner_external_tools(catalog.list_available(), content)
                if catalog is not None
                else []
            ),
        )

        # ---- Planner + 内容解释（无事务段；共用单 Turn 预算，W1-03） ----
        budget_key = f"turn:{turn_id}"
        # Planner（含其内部重试）+ 解释（一次）合计的调用护栏；token
        # 上限是权威约束（agent_turn_max_tokens），调用数为防失控兜底
        enter_run(
            budget_key,
            soft_calls=_TURN_SOFT_CALLS,
            hard_calls=_TURN_HARD_CALLS,
            hard_tokens=self._settings.agent_turn_max_tokens,
        )
        try:
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
                return await self._fail_turn(db, turn_id, conv_id, lease_owner, exc)

            # W1-03：内容性解释读取确切原文作答（覆盖 Planner 的泛化答复），
            # 无解释目标（None）时保留 Planner 答复（项目级状态类问题）
            explanation_citations: list[dict[str, Any]] | None = None
            if output.turn_type == "answer" and output.intent == "explain":
                composed = await self._explain_content(db, project, content, active_context)
                if composed is not None:
                    output, explanation_citations = composed

            # ---- 事务 B:写入终态并终结 Turn ----
            try:
                final_turn = await self._finalize_turn(
                    db,
                    turn_id,
                    conv_id,
                    lease_owner,
                    output,
                    project,
                    content,
                    target_episode_count=target_episode_count,
                    staged=staged,
                    explanation_citations=explanation_citations,
                )
            except AgentStateTransitionError:
                # 租约被接管(超期后他人完成):放弃本次结果,返回持久化胜者。
                await db.rollback()
                return await self._duplicate_outcome(db, turn_id)
            except Exception as exc:
                # 事务 B 失败(如 revise_script 目标集无有效剧本):
                # Turn 不能停留在 planning,统一落 failed 终态。
                logger.exception("Turn 终态写入失败: turn=%s", turn_id)
                await db.rollback()
                return await self._fail_turn(db, turn_id, conv_id, lease_owner, exc)
            return await self._turn_response(db, final_turn), 200
        finally:
            exit_run(budget_key)

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
        """查询 Action 的持久化快照。

        Worker 在 Run 终态后崩溃时，Action 可能停留在 queued/running——
        此处触发 reconciliation 补写 Outcome、结果消息与可空的后续计划。
        """
        action = await AgentActionRepository(db).get(action_id)
        if action is None:
            raise NotFoundError(detail=f"AgentAction 不存在: {action_id}", code="AGENT_ACTION_NOT_FOUND")
        if action.run_id is not None and action.status in ("queued", "running"):
            run = await self._run_service.get_run(db, action.run_id)
            if run.status in ("completed", "failed", "needs_review", "cancelled"):
                from app.application.agent_action_lifecycle import AgentActionLifecycle

                await AgentActionLifecycle().reconcile(db, action_id=action_id)
                await db.commit()
                action = await AgentActionRepository(db).get(action_id)
                assert action is not None
        return self._action_response(action)

    async def confirm_action(
        self, db: AsyncSession, action_id: uuid.UUID
    ) -> tuple[AgentActionResponse, WorkflowRun]:
        """确认 proposed Action:过期检测 → 创建/复用 Run → Action→queued。"""
        action_repo = AgentActionRepository(db)
        artifact_repo = ArtifactRepository(db)

        action = await action_repo.get_for_update(action_id)
        if action is None:
            raise NotFoundError(detail=f"AgentAction 不存在: {action_id}", code="AGENT_ACTION_NOT_FOUND")

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
                await action_repo.transition(action_id, "stale", expected_statuses={"proposed"})
                await db.commit()  # 先持久化 stale 再抛错,保证状态可见
                raise AgentActionStaleError(detail="计划基于的 Artifact 已更新,请重新发起规划")

        # MCP-03：外部工具计划在确认前重校验工具仍 available 且定义
        # digest 未变化（计划后工具被移除/改定义 → Action 转 stale）。
        if action.intent == "use_external_tool":
            mcp_plan = AgentActionPlan.model_validate(action.plan)
            mcp_command = mcp_plan.command
            if not isinstance(mcp_command, MCPToolCallCommand):
                await db.rollback()
                raise UnsupportedAgentIntentError(detail="外部工具计划缺少有效的调用命令")
            catalog = self._mcp_catalog()
            definition = (
                catalog.get(mcp_command.qualified_tool_name)
                if catalog is not None
                else None
            )
            if (
                definition is None
                or definition.status != "available"
                or definition.definition_digest != mcp_command.tool_definition_digest
            ):
                await action_repo.transition(action_id, "stale", expected_statuses={"proposed"})
                await db.commit()
                raise AgentActionStaleError(
                    detail="外部工具已变化或不可用,请重新发起规划"
                )

        # continue 意图不创建新 Run，而是恢复停在确认门的既有 Run；
        # 必须在 INTENT_RUN_ACTION 查找之前分流（continue 无对应 run action）。
        if action.intent == "continue":
            plan = AgentActionPlan.model_validate(action.plan)  # 只信服务端持久化 Plan
            command = plan.command
            if not isinstance(command, ContinueCommand):
                await db.rollback()
                raise UnsupportedAgentIntentError(detail="continue 计划缺少有效的续跑命令")
            run = await self._run_service.get_run(db, command.target_run_id)
            summary = run.state_summary or {}
            if (
                run.project_id != action.project_id
                or run.status != "needs_review"
                or summary.get("stage_gate") not in ("outline", "scripts")
            ):
                # 规划后 Run 状态已变化（已续跑/已取消/门已清）→ 计划作废
                await action_repo.transition(action_id, "stale", expected_statuses={"proposed"})
                await db.commit()
                raise RunNotRetryableError(detail="计划对应的任务已不在确认门上，请重新发起")

            # W1-01 阶段世代：确认时校验计划构建时的世代快照，防止旧计划
            # 重放多写一批。旧计划（缺 expected 字段）仅当 Run 自计划创建后
            # 未再推进时按当前世代恢复，无法可靠恢复则作废。
            expected_gen = command.expected_stage_generation
            if expected_gen is None:
                run_untouched_since_plan = (
                    run.updated_at is not None
                    and action.created_at is not None
                    and run.updated_at <= action.created_at
                )
                if not run_untouched_since_plan:
                    await action_repo.transition(action_id, "stale", expected_statuses={"proposed"})
                    await db.commit()
                    raise RunStageStaleError(
                        detail=(
                            "旧续跑计划创建后任务已被推进，无法确认世代，计划已作废；请基于当前进度重新发起"
                        ),
                        current_stage_generation=run.stage_generation,
                    )
                expected_gen = run.stage_generation

            try:
                resumed_result = await self._run_service.continue_gated_run(
                    db,
                    run.id,
                    batch_size=command.batch_size,
                    expected_stage_generation=expected_gen,
                    idempotency_key=f"continue:{action_id}",
                )
            except RunStageStaleError:
                # 并发确认（按钮/聊天/重复计划）只有一个胜者：本计划作废
                await action_repo.transition(action_id, "stale", expected_statuses={"proposed"})
                await db.commit()
                raise
            if resumed_result.replayed:
                # 同键收据重放（重复确认）：返回当前持久化状态，不新增阶段
                if action.run_id is not None:
                    replay_run = await self._run_service.get_run(db, action.run_id)
                    return self._action_response(action), replay_run
                await db.commit()
                raise RunNotRetryableError(detail="续跑收据重放时缺少关联 Run，请基于当前进度重新发起")
            resumed = resumed_result.run
            assert resumed is not None
            # Run 终态回写（J-09 lifecycle）指向本 continue Action：
            # 原 create_script Action 在停门时已写入终态与结果消息，不可复用。
            resumed.config_snapshot = {
                **(resumed.config_snapshot or {}),
                "agent_action_id": str(action_id),
            }
            await action_repo.transition(action_id, "queued", run_id=resumed.id)
            await db.commit()  # durable 后再做 best-effort 唤醒
            schedule_worker(resumed.id, resumed.action, resumed.config_snapshot or {})
            return self._action_response(action), resumed

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
                config={
                    **self._build_run_config(plan),
                    "agent_action_id": str(action_id),
                },
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

    async def reject_action(self, db: AsyncSession, action_id: uuid.UUID) -> AgentActionResponse:
        """拒绝 proposed Action(仅 proposed→rejected)。"""
        action_repo = AgentActionRepository(db)
        action = await action_repo.get_for_update(action_id)
        if action is None:
            raise NotFoundError(detail=f"AgentAction 不存在: {action_id}", code="AGENT_ACTION_NOT_FOUND")
        action = await action_repo.transition(action_id, "rejected", expected_statuses={"proposed"})
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
            result = await db.execute(select(Conversation).where(Conversation.id == created.id))
            return result.scalar_one()
        result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
        conversation = result.scalar_one_or_none()
        if conversation is None or conversation.deleted_at is not None:
            raise NotFoundError(detail=f"会话不存在: {conversation_id}", code="CONVERSATION_NOT_FOUND")
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
        stmt = select(AgentTurn.status).where(AgentTurn.conversation_id == conversation_id)
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

    def _mcp_catalog(self) -> Any | None:
        """当前进程的 MCP 工具目录；MCP 未启用时为 None。"""
        if self._mcp_manager_provider is None:
            return None
        manager = self._mcp_manager_provider()
        if manager is None:
            return None
        return getattr(manager, "catalog", None)

    async def _available_intents(self, db: AsyncSession, project: Project) -> list[str]:
        """动态意图白名单：项目存在停在确认门的 Run 时开放 continue；
        MCP catalog 存在 available 工具时开放 use_external_tool（MCP-03）。

        Planner 白名单校验以本列表为准——没有门上 Run 时"继续"这类请求
        不会被判为 continue 意图；没有可用外部工具时工具类请求也不产生
        use_external_tool 计划，避免产出永远无法确认的计划。
        """
        intents = list(DEFAULT_AVAILABLE_INTENTS)
        if await find_gated_run(db, project.id) is not None:
            intents.append("continue")
        catalog = self._mcp_catalog()
        if catalog is not None and catalog.list_available():
            intents.append("use_external_tool")
        return intents

    @staticmethod
    def _limited_answer(
        text: str,
    ) -> tuple[AgentPlannerOutput, list[dict[str, Any]]]:
        """解释路径的有限答复（窄化/无正文/失败/无引文共用）：不带引文。"""
        return (
            AgentPlannerOutput(turn_type="answer", intent="explain", answer=text),
            [],
        )

    async def _explain_content(
        self,
        db: AsyncSession,
        project: Project,
        user_request: str,
        active_context: ActiveArtifactContext | None,
    ) -> tuple[AgentPlannerOutput, list[dict[str, Any]]] | None:
        """内容性解释（W1-03）：读确切原文 → 模型作答 → 验证引文回填。

        读取在只读事务内完成并先提交（模型调用期间零事务）；引文必须
        在指定版本/场景的原文中出现，服务端回填 artifact_id/version/
        checksum（模型只输出 source_index）。无解释目标返回 None（回落
        Planner 的项目级答复）。解释无 Run/Action/Artifact 写入。
        """
        from app.domain.agent_planner import (
            ArtifactExplanationInput,
            ExplanationSourceText,
        )
        from app.skills.artifact_explainer import ArtifactExplainerSkill

        try:
            ctx = await self._context_service.build_explanation_context(
                db, project, user_request, active_context
            )
            await db.commit()  # 成功路径：关闭只读事务（模型调用期间零事务）
        except Exception:
            # 读取失败回滚（不留半开事务），降级为有限答复——解释是辅助
            # 能力，不应让整个 Turn 失败
            with contextlib.suppress(Exception):
                await db.rollback()
            logger.exception("解释上下文构建失败，降级为有限答复")
            return self._limited_answer("原文读取暂时失败，稍后再试一次。")
        if ctx is None:
            return None

        if ctx.status == "narrow":
            return self._limited_answer(
                "要准确回答这个问题，我需要更具体的范围——"
                "请告诉我具体哪一集，或先在剧本页选中某一场再问；"
                "我不会在没读完正文的情况下猜测剧情。"
            )
        if ctx.status == "no_text":
            return self._limited_answer("这份稿件还没有可阅读的正文（可能只有标题），暂时无法回答剧情问题。")

        exp_input = ArtifactExplanationInput(
            question=ctx.question[:4000],
            target_label=(
                f"第 {ctx.target.episode} 集 v{ctx.target.version}"
                if ctx.target.type == "script_draft"
                else f"{ctx.target.type} v{ctx.target.version}"
            ),
            sources=[
                ExplanationSourceText(
                    source_index=src.source_index,
                    kind=src.kind,
                    label=src.label,
                    scene_number=src.scene_number,
                    text=src.text[:60000],
                )
                for src in ctx.sources
            ],
        )
        try:
            result = await ArtifactExplainerSkill().execute(
                {
                    "input": exp_input,
                    "agent": self._planner_agent,
                    "prompt_loader": self._prompt_loader,
                }
            )
        except Exception as exc:
            # 解释是辅助能力（最多一次生成、无自定义重试）：任何失败
            # 都不炸 Turn，降级为"暂不可用"的有限答复
            logger.warning("解释模型调用失败，降级为有限答复: %s", exc)
            return self._limited_answer("原文解释暂时不可用；你可以先看稿件原文，或稍后再问一次。")

        if not result.citations:
            # 诚实性：没有可验证引文 = 原文不足以确认——不附带未核实的解释
            return self._limited_answer(
                "我读了这份稿件，但没能从原文中确认这个问题的答案"
                "（引用的原文无法定位）。换个问法，或直接告诉我你"
                "在正文里看到的位置。"
            )

        # 引文回填：source_index → 目标 Artifact 的 UUID/version/checksum
        target_id = ctx.target.artifact_id
        target_version = ctx.target.version
        target_checksum = ctx.target.checksum
        citations_meta = [
            {
                "artifact_id": target_id,
                "version": target_version,
                "scene_number": c["scene_number"],
                "quote": c["quote"],
                "checksum": target_checksum,
            }
            for c in result.citations
        ]
        # 引用行附在答复后，读者可直接核对原文
        citation_lines = "\n".join(
            f"> 引文（第 {c['scene_number']} 场）：{c['quote']}"
            if c["scene_number"] is not None
            else f"> 引文：{c['quote']}"
            for c in citations_meta
        )
        answer = f"{result.answer}\n\n{citation_lines}"
        return (
            AgentPlannerOutput(turn_type="answer", intent="explain", answer=answer),
            citations_meta,
        )

    async def _try_shortcut(
        self,
        db: AsyncSession,
        project: Project,
        conversation_id: uuid.UUID,
        shortcut: tuple[str, int | None],
        *,
        turn_id: uuid.UUID,
    ) -> AgentPlannerOutput | None:
        """执行确定性短路；返回 None 表示无可执行目标，回落 Planner。

        最新 pending 优先：proposed Action 与门上 Run 同时存在时按
        updated_at 取新者，避免把过期计划确认成第二个并行 Run。
        IR-3 §8.6 上下文保护：确认类短语只有在会话内最近的 assistant
        消息仍是可确认对象（action_plan / action_result，后者覆盖确认门
        通知）时才直接执行——中间出现过澄清或新答复时回落 Planner，
        不把"好的"错认成对旧计划的确认。
        执行失败的 AppError 转为可读答复而非静默回落——用户已明确
        表达了意图，回落 Planner 只会得到一次无效澄清。
        """
        kind, batch = shortcut
        try:
            if kind == "confirm":
                if not await _latest_assistant_message_confirmable(db, conversation_id):
                    logger.info("agent_shortcut kind=confirm outcome=fallback_stale_context")
                    return None
                action = await find_latest_proposed_action(db, conversation_id)
                gated = await find_gated_run(db, project.id)
                if action is not None and (
                    gated is None
                    or gated.updated_at is None
                    or (action.updated_at or action.created_at) >= gated.updated_at
                ):
                    await self.confirm_action(db, action.id)
                    logger.info("agent_shortcut kind=confirm outcome=executed_plan")
                    return AgentPlannerOutput(turn_type="answer", answer="已确认，计划开始执行。")
                if gated is not None:
                    # 确认门上的"确认"即续跑（门消息承诺"等待确认后继续创作"）
                    logger.info("agent_shortcut kind=confirm outcome=executed_gate")
                    return await self._continue_gated(db, gated.id, batch=None, turn_id=turn_id)
                return await self._no_target_answer(db, project.id)
            if kind == "retry":
                failed = await find_latest_failed_run(db, project.id)
                if failed is None:
                    return AgentPlannerOutput(turn_type="answer", answer="当前没有失败的任务可重试。")
                return await self._retry_failed(db, failed.id)
            gated = await find_gated_run(db, project.id)
            if gated is None:
                return await self._no_target_answer(db, project.id)
            return await self._continue_gated(db, gated.id, batch=batch, turn_id=turn_id)
        except AppError as exc:
            logger.info("短路执行失败，转为可读答复: kind=%s error=%s", kind, exc)
            return AgentPlannerOutput(turn_type="answer", answer=f"未能执行：{exc.detail}")

    async def _no_target_answer(self, db: AsyncSession, project_id: uuid.UUID) -> AgentPlannerOutput | None:
        """确认/续跑未命中任何目标时的确定性答复。

        最新动态是失败 → 如实告知原因并给出重试入口，绝不回落 Planner
        凭空澄清（用户刚被告知"等待确认"，反问"想做什么"最伤信任）；
        任务执行中 → 执行中答复；确实没有任务 → 返回 None 回落 Planner。
        """
        if await find_active_run(db, project_id) is not None:
            return self._active_run_answer()
        latest = await find_latest_run(db, project_id)
        if latest is not None and latest.status == "failed":
            if latest.error_code == "WORKFLOW_RECOVERY_EXHAUSTED":
                tail = "该任务的自动重试次数已用完，建议重新发起创作，或直接告诉我你想调整什么。"
            else:
                tail = "输入「重试」可从断点重新执行，或直接告诉我你想调整什么。"
            return AgentPlannerOutput(
                turn_type="answer",
                answer=f"上一次创作任务失败了：{describe_run_failure(latest)}。\n{tail}",
            )
        return None

    async def _retry_failed(self, db: AsyncSession, run_id: uuid.UUID) -> AgentPlannerOutput:
        """从断点重试失败的 Run（I-01 retry：不重调已完成节点）。"""
        run = await self._run_service.get_run(db, run_id)
        if run.status != "failed":
            return AgentPlannerOutput(turn_type="answer", answer="当前没有失败的任务可重试。")
        if run.error_code == "WORKFLOW_RECOVERY_EXHAUSTED":
            # 恢复预算已耗尽的 Run 再排队会立刻再次耗尽——诚实拒绝，
            # 指向重新发起而非让用户陷入"重试→秒败"死循环
            return AgentPlannerOutput(
                turn_type="answer",
                answer=(
                    "该任务的自动重试次数已经用完，再重试也会立刻失败。"
                    "建议重新发起创作（已有的故事设定和大纲不受影响），"
                    "或直接告诉我你想调整什么。"
                ),
            )
        run.error_code = None
        run.error_detail = None
        await db.flush()
        await self._run_service.transition_status(db, run_id, "queued")
        schedule_worker(run.id, run.action, run.config_snapshot or {})
        logger.info("对话短路重试 failed Run: run=%s", run_id)
        return AgentPlannerOutput(
            turn_type="answer",
            answer="已开始从断点重试，已完成的步骤不会重复执行，完成后会在这里汇报。",
        )

    @staticmethod
    def _active_run_answer() -> AgentPlannerOutput:
        """任务执行中的统一答复。"""
        return AgentPlannerOutput(
            turn_type="answer",
            answer="创作任务正在执行中，完成后会在这里汇报，无需重复发送。",
        )

    async def _continue_gated(
        self,
        db: AsyncSession,
        run_id: uuid.UUID,
        *,
        batch: int | None,
        turn_id: uuid.UUID,
    ) -> AgentPlannerOutput:
        """续跑停在确认门的 Run 并生成可读答复。

        W1-01：续跑以 Turn 为幂等键（同 Turn 重放命中收据返回原接受；
        并发的按钮/聊天确认只有一个胜者，败者得到 RUN_STAGE_STALE 的
        可读答复），expected 取读取时世代——行锁内的世代校验兜底并发。
        """
        run = await self._run_service.get_run(db, run_id)
        summary = run.state_summary or {}
        gate = summary.get("stage_gate")
        written = len(summary.get("script_artifact_ids") or {})
        options = (run.config_snapshot or {}).get("options", {})
        target_count = int(options.get("outline_count") or options.get("script_count") or 0)
        result = await self._run_service.continue_gated_run(
            db,
            run_id,
            batch_size=batch,
            expected_stage_generation=run.stage_generation,
            idempotency_key=f"turn:{turn_id}",
        )
        if not result.replayed:
            # 重放命中收据说明该 Turn 的续跑已接受过——Worker 已在执行
            # 或批次已结束，无需（也不应基于行锁前的旧快照）再唤醒
            assert result.run is not None
            schedule_worker(run_id, result.run.action, result.run.config_snapshot or {})
        logger.info(
            "对话短路续跑 Run: run=%s gate=%s batch=%s replayed=%s conversation 级确认",
            run_id,
            gate,
            batch,
            result.replayed,
        )
        return AgentPlannerOutput(
            turn_type="answer",
            answer=render_continue_answer(gate=gate, written=written, target=target_count, batch=batch),
        )

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

    async def _duplicate_outcome(self, db: AsyncSession, turn_id: uuid.UUID) -> tuple[AgentTurnResponse, int]:
        """重复请求的统一出口:终态返回 200,仍规划中返回 202。"""
        turn = await AgentTurnRepository(db).get(turn_id)
        if turn is None:
            raise NotFoundError(detail=f"AgentTurn 不存在: {turn_id}", code="AGENT_TURN_NOT_FOUND")
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
        target_episode_count: int | None = None,
        staged: bool = True,
        explanation_citations: list[dict[str, Any]] | None = None,
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
            # explain 归一化为只读答复,不落 AgentAction。W1-03：内容性解释
            # 的引文（已验证的原文 ArtifactCitation）随消息 metadata 回放。
            content = output.answer or self._render_explain_answer(output)
            answer_metadata: dict[str, Any] = {"agent_turn_id": str(turn_id)}
            if explanation_citations:
                answer_metadata["explanation_citations"] = explanation_citations
                answer_metadata["message_type"] = "explanation"
            msg = await self._append_message(
                db,
                conversation_id,
                role="assistant",
                content=content,
                kind="text",
                metadata=answer_metadata,
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
            plan, snapshots = await self._build_action_plan(
                db,
                project,
                output,
                user_request,
                target_episode_count=target_episode_count,
                staged=staged,
            )
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
        target_episode_count: int | None = None,
        staged: bool = True,
    ) -> tuple[AgentActionPlan, list[ArtifactSnapshot]]:
        """把 Planner 输出转换为服务端模板化的非执行计划与来源快照。"""
        constraints = list(output.constraints)
        expected_impact = list(output.expected_impact)
        intent: AgentIntent = "create_script"  # 分支内按白名单重赋值
        command: AgentCommand  # 分支内按 intent 赋对应命令

        if output.intent == "create_script":
            # L-1：集数回退链——本次请求显式选择（Composer 设置）>
            # 项目目标集数（建项目时填的"目标 X 集"）> 系统默认。
            # 此前漏了项目层：用户建项目填 2 集、没动 Composer 设置时
            # 直接掉到系统默认 10（用户实测 outline 仍 10 集的根因之二）。
            effective_count = (
                target_episode_count or project.target_episode_count or self._settings.mvp_outline_count
            )
            outline_count = effective_count
            script_count = effective_count
            command = CreateScriptCommand(
                user_input=user_request,
                outline_count=outline_count,
                script_count=script_count,
                stop_after="outline" if staged else None,
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
            if staged:
                steps = steps[:3] + [
                    ActionStep(
                        step_id="stage_gate",
                        title="大纲确认门",
                        description="StoryBible 与大纲生成后暂停，等你确认（可聊天修改）再继续写剧本",
                    ),
                ]
            snapshots: list[ArtifactSnapshot] = []
        elif output.intent == "evaluate":
            episode = output.target.episode_number if output.target else None
            intent_str, command, target, goal, steps = build_evaluate_plan(episode=episode)
            intent = intent_str  # type: ignore[assignment]
            snapshots = await self._script_snapshots(db, project.id, episode)
        elif output.intent == "continue":
            # continue 意图（B）：目标由服务端解析为项目最新停在 stage_gate
            # 的 Run，Planner 只提供批集数。白名单已保证仅在有门上 Run 时
            # 开放该意图；到达这里却找不到属于白名单漂移，按可读错误拒绝。
            gated = await find_gated_run(db, project.id)
            if gated is None:
                raise AppError(
                    detail="当前没有停在确认门的任务可继续",
                    status_code=409,
                    code="GATED_RUN_NOT_FOUND",
                )
            summary = gated.state_summary or {}
            gate = summary.get("stage_gate")
            written = len(summary.get("script_artifact_ids") or {})
            gated_options = (gated.config_snapshot or {}).get("options", {})
            target_count = int(gated_options.get("outline_count") or gated_options.get("script_count") or 0)
            command = ContinueCommand(
                target_run_id=gated.id,
                expected_stage_generation=gated.stage_generation,
                batch_size=output.batch_size,
            )
            intent = "continue"
            target = ActionTarget(target_type="project")
            goal = f"确认当前进度并继续创作剧本（已完成 {written}/{target_count} 集）"[:2000]
            steps = [
                ActionStep(
                    step_id="continue",
                    title="恢复执行",
                    description="从确认门恢复创作流程，已完成部分不重算",
                ),
                ActionStep(
                    step_id="write_episodes",
                    title="继续写剧本",
                    description=(
                        "按确认后的大纲继续生成剧本"
                        if gate == "outline"
                        else (
                            f"继续生成后续集数（本批 {output.batch_size} 集）"
                            if output.batch_size is not None
                            else "写完剩余全部集数"
                        )
                    ),
                ),
                ActionStep(
                    step_id="evaluate",
                    title="自动评估",
                    description="新写集数完成后自动评估并决定是否修订",
                ),
            ]
            expected_impact = ["从确认门恢复执行，继续生成剧本与评估"]
            snapshots = []
        elif output.intent == "revise_script":
            # 目标由服务端解析：目标集的最新 valid 剧本，Planner 不提供 UUID。
            episode = output.target.episode_number if output.target else None
            if episode is None:
                raise ScriptNotFoundForRevisionError(detail="未能确定修订目标集数，请指定集数或先选择剧本")
            source = await ArtifactRepository(db).get_latest_valid(project.id, "script_draft", episode)
            if source is None:
                raise ScriptNotFoundForRevisionError(detail=f"第 {episode} 集没有可修订的有效剧本")
            (
                intent_str,
                command,
                target,
                goal,
                steps,
                snapshots,
            ) = build_revise_script_plan(source=source, constraints=constraints, user_request=user_request)
            intent = intent_str  # type: ignore[assignment]
        elif output.intent == "revise_outline":
            # 目标由服务端解析：项目最新 valid 大纲，Planner 不提供 UUID。
            source_outline = await ArtifactStore().get_latest(db, project.id, "episode_outline_set", 1)
            if source_outline is None:
                raise OutlineNotFoundForRevisionError(detail="项目没有可修订的有效分集大纲")
            (
                intent_str,
                command,
                target,
                goal,
                steps,
                snapshots,
            ) = build_revise_outline_plan(
                source_outline=source_outline,
                constraints=constraints,
                user_request=user_request,
            )
            intent = intent_str  # type: ignore[assignment]
        elif output.intent == "use_external_tool":
            # MCP-03：外部工具计划。Planner 只提供选择器（qualified ID +
            # 参数）；服务端对照 catalog 重新验证工具、allowlist（catalog
            # 构建时已过滤）、Schema 与 digest，不信任模型的描述。
            catalog = self._mcp_catalog()
            if catalog is None:
                raise MCPToolNotAvailableError(detail="MCP 未启用，无法调用外部工具")
            selector = output.external_tool
            if selector is None or not _MCP_TOOL_ID_RE.match(selector.qualified_tool_name):
                raise UnsupportedAgentIntentError(detail="外部工具请求缺少合法的工具选择器")
            definition = catalog.get(selector.qualified_tool_name)
            if definition is None or definition.status != "available":
                raise MCPToolNotAvailableError(
                    detail=f"外部工具不可用: {selector.qualified_tool_name}"
                )
            # 计划构建即校验参数（JSON Schema 2020-12），非法参数不产生计划
            from app.integrations.mcp.execution import _validate_json_schema

            _validate_json_schema(
                definition.input_schema,
                selector.arguments,
                error_cls=MCPToolArgumentInvalidError,
            )
            display_name = definition.title or definition.tool_name
            intent = "use_external_tool"
            command = MCPToolCallCommand(
                server_id=definition.server_id,
                tool_name=definition.tool_name,
                qualified_tool_name=definition.qualified_tool_name,
                arguments=dict(selector.arguments),
                tool_definition_digest=definition.definition_digest,
                purpose=(selector.purpose or user_request)[:2000],
            )
            target = ActionTarget(target_type="external_tool")
            purpose_text = (selector.purpose or user_request)[:200]
            goal = f"调用外部工具 {display_name}（来源 {definition.server_id}）：{purpose_text}"
            risk_flags = []
            if definition.annotations is not None:
                if definition.annotations.destructive_hint:
                    risk_flags.append("可能具有破坏性")
                if definition.annotations.open_world_hint:
                    risk_flags.append("访问外部世界")
            risk_note = "；".join(risk_flags)
            steps = [
                ActionStep(
                    step_id="confirm",
                    title="等待确认",
                    description="展示工具来源、参数与风险提示，等待你确认执行",
                ),
                ActionStep(
                    step_id="call",
                    title="调用外部工具",
                    description="经确认后调用一次外部工具，结果不直接改动任何稿件",
                ),
                ActionStep(
                    step_id="report",
                    title="回写结果",
                    description="结果以消息形式返回，供你决定下一步",
                ),
            ]
            expected_impact = ["不创建或修改任何稿件内容；仅返回外部工具结果"]
            if risk_note:
                expected_impact.append(f"风险提示：{risk_note}")
            snapshots = []
        else:
            # Planner 白名单已限定意图;到达这里说明服务端与 Planner 白名单漂移,直接拒绝。
            raise UnsupportedAgentIntentError(detail=f"intent 不支持生成执行计划: {output.intent}")

        plan = AgentActionPlan(
            goal=goal,
            intent=intent,
            command=command,
            target=target,
            constraints=constraints,
            steps=steps,
            expected_impact=expected_impact,
            user_request=user_request,
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
            if latest is None:
                # IR-4 §9.2：单集评估目标不存在时在计划阶段明确失败，
                # 不产出空快照计划、更不能静默退化为全项目评估
                raise ScriptNotFoundForRevisionError(
                    detail=f"第 {episode} 集没有可评估的有效剧本"
                )
            artifacts = [latest]
        else:
            artifacts = await artifact_repo.list_by_project(project_id, "script_draft", offset=0, limit=1000)
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
            options: dict[str, Any] = {
                "user_input": command.user_input,
                "source_type": "idea",
                "outline_count": command.outline_count,
                "script_count": command.script_count,
            }
            if command.stop_after:
                options["stop_after"] = command.stop_after
            return {"options": options}
        if isinstance(command, EvaluateCommand):
            eval_options: dict[str, Any] = {"scope": command.scope}
            options = eval_options
            if command.episode_number is not None:
                options["episode_number"] = command.episode_number
            return {"options": options}
        if isinstance(command, ReviseScriptCommand):
            return {
                "options": {
                    "source_script_artifact_id": str(command.source_script_id),
                    "episode_number": command.episode_number,
                    "user_constraints": list(command.constraints),
                    "user_request": command.user_request,
                }
            }
        if isinstance(command, ReviseOutlineCommand):
            return {
                "options": {
                    "source_outline_artifact_id": str(command.source_outline_id),
                    "user_constraints": list(command.constraints),
                    "user_request": command.user_request,
                }
            }
        if isinstance(command, MCPToolCallCommand):
            # MCP-03：Run config 只携带服务端校验过的调用参数（无 URL/Token）
            return {
                "options": {
                    "server_id": command.server_id,
                    "tool_name": command.tool_name,
                    "arguments": dict(command.arguments),
                    "tool_definition_digest": command.tool_definition_digest,
                    "purpose": command.purpose,
                }
            }
        return {"options": command.model_dump(mode="json")}

    def _render_plan_message(self, plan: AgentActionPlan) -> str:
        """把计划渲染为用户可读的 action_plan 消息文本。"""
        return render_plan_message(plan)

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
                ArtifactSnapshot.model_validate(raw) for raw in (action.source_artifact_ids or [])
            ],
            result=AgentOutcome.model_validate(action.result) if action.result else None,
            run_id=action.run_id,
            created_at=action.created_at,
            updated_at=action.updated_at,
        )
