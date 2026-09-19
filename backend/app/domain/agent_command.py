"""受约束创作 Agent 的领域契约。

J-01 只定义可持久化的命令、计划、结果和状态机，不包含 Planner 或执行逻辑。
所有模型禁止额外字段，避免模型输出或客户端载荷把未经验证的数据带入执行层。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

AgentIntent = Literal[
    "create_script",
    "explain",
    "revise_outline",
    "revise_script",
    "evaluate",
    "continue",
    "use_external_tool",
]
AgentTurnStatus = Literal[
    "received",
    "planning",
    "needs_input",
    "answered",
    "action_proposed",
    "failed",
]
AgentTurnType = Literal["clarification", "answer", "plan"]
AgentActionStatus = Literal[
    "proposed",
    "queued",
    "running",
    "completed",
    "needs_review",
    "failed",
    "cancelled",
    "stale",
    "rejected",
]
AgentGoalStatus = Literal["achieved", "partially_achieved", "blocked"]

TURN_TRANSITIONS: dict[str, frozenset[str]] = {
    "received": frozenset({"planning", "failed"}),
    "planning": frozenset({"needs_input", "answered", "action_proposed", "failed"}),
    "needs_input": frozenset(),
    "answered": frozenset(),
    "action_proposed": frozenset(),
    "failed": frozenset(),
}

ACTION_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"queued", "stale", "rejected"}),
    "queued": frozenset({"running", "failed", "cancelled"}),
    "running": frozenset({"completed", "needs_review", "failed", "cancelled"}),
    "completed": frozenset(),
    # 分段创作（L-3/L-4）让一个 Action 跨越多个 Run 幕次：门上暂停后再续跑，
    # 续跑后的终态必须能回写，否则失败/完成对用户不可见
    "needs_review": frozenset({"completed", "failed", "cancelled"}),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "stale": frozenset(),
    "rejected": frozenset(),
}


class ActiveArtifactContext(BaseModel):
    """用户发起 Turn 时显式选中的页面上下文。

    scene_number（W1-03）：仅 script_draft 合法，表示用户正在看/选中
    的场；服务端校验其存在于该确切版本。纳入 request_hash。
    """

    model_config = {"extra": "forbid"}

    artifact_id: UUID
    artifact_type: str = Field(..., min_length=1, max_length=50)
    episode_number: int | None = Field(default=None, ge=1)
    scene_number: int | None = Field(
        default=None, ge=1,
        description="剧本内的场景锚点（仅 script_draft 合法，服务端校验存在）",
    )
    version: int | None = Field(default=None, ge=1)
    checksum: str | None = Field(default=None, min_length=64, max_length=64)


class ArtifactCitation(BaseModel):
    """解释引文的服务端结构（W1-03，Message.metadata.explanation_citations）。

    服务端回填 artifact_id/version/checksum（模型只输出 source_index）；
    引文必须经原文归一化匹配验证，否则不进入该结构。
    """

    model_config = {"extra": "forbid"}

    artifact_id: UUID
    version: int
    scene_number: int | None = Field(default=None, ge=1)
    quote: str = Field(..., min_length=1, max_length=200)
    checksum: str = Field(..., min_length=64, max_length=64)


class ActionTarget(BaseModel):
    """服务端解析后的受限动作目标。"""

    model_config = {"extra": "forbid"}

    target_type: Literal[
        "project",
        "story_bible",
        "outline",
        "script",
        "evaluation",
        "external_tool",
    ]
    artifact_id: UUID | None = None
    episode_number: int | None = Field(default=None, ge=1)
    version: int | None = Field(default=None, ge=1)


class ArtifactSnapshot(BaseModel):
    """Action 规划时记录的来源 Artifact 快照。"""

    model_config = {"extra": "forbid"}

    artifact_id: UUID
    artifact_type: str = Field(..., min_length=1, max_length=50)
    episode_number: int = Field(default=1, ge=1)
    version: int = Field(..., ge=1)
    checksum: str = Field(..., min_length=64, max_length=64)


class ActionStep(BaseModel):
    """展示给用户的单个计划步骤，不包含任意工具名或 URL。"""

    model_config = {"extra": "forbid"}

    step_id: str = Field(..., min_length=1, max_length=80)
    title: str = Field(..., min_length=1, max_length=120)
    description: str = Field(..., min_length=1, max_length=1000)


class CreateScriptCommand(BaseModel):
    """首次创作命令。"""

    model_config = {"extra": "forbid"}

    intent: Literal["create_script"] = "create_script"
    user_input: str = Field(..., min_length=1)
    outline_count: int = Field(default=10, ge=1, le=50)
    script_count: int = Field(default=3, ge=1, le=50)
    stop_after: Literal["outline"] | None = Field(
        default=None,
        description="分段创作门（L-2）：outline = SB+大纲后暂停等确认，不写剧本",
    )


class ExplainCommand(BaseModel):
    """只读解释命令。"""

    model_config = {"extra": "forbid"}

    intent: Literal["explain"] = "explain"
    target: ActionTarget


class ReviseOutlineCommand(BaseModel):
    """大纲修订命令。"""

    model_config = {"extra": "forbid"}

    intent: Literal["revise_outline"] = "revise_outline"
    source_outline_id: UUID
    constraints: list[str] = Field(default_factory=list)
    # IR-3 §8.3：原始用户请求（完整授权边界）。结构化 constraints 是索引，
    # 模型遗漏约束时下游仍可从原文恢复；旧计划缺省 None（legacy）。
    user_request: str | None = Field(default=None, max_length=4000)


class ReviseScriptCommand(BaseModel):
    """指定剧本版本的修订命令。"""

    model_config = {"extra": "forbid"}

    intent: Literal["revise_script"] = "revise_script"
    source_script_id: UUID
    episode_number: int = Field(..., ge=1)
    constraints: list[str] = Field(default_factory=list)
    # IR-3 §8.3：原始用户请求（完整授权边界），与 constraints 并行携带
    user_request: str | None = Field(default=None, max_length=4000)


class EvaluateCommand(BaseModel):
    """项目或单集评估命令。"""

    model_config = {"extra": "forbid"}

    intent: Literal["evaluate"] = "evaluate"
    scope: Literal["project", "episode"] = "project"
    episode_number: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _episode_scope_requires_number(self) -> EvaluateCommand:
        if self.scope == "episode" and self.episode_number is None:
            raise ValueError("episode scope requires episode_number")
        if self.scope == "project" and self.episode_number is not None:
            raise ValueError("project scope cannot include episode_number")
        return self


class ContinueCommand(BaseModel):
    """确认门续跑命令（L-3/L-4，W1-01 增加阶段世代快照）。

    target_run_id 由服务端在计划构建时解析（项目最新停在 stage_gate 的
    Run），Planner 不提供 Run 标识；确认时二次校验 Run 仍在门上。
    expected_stage_generation 是计划构建时的 Run 世代快照——确认时与
    当前世代不符（其间发生过续跑）则计划作废，防止旧计划重放多写一批。
    旧计划（W1-01 之前持久化）缺该字段：仅当 Run 自计划创建后未再推进
    时按当前世代恢复，否则按 stale 处理。
    """

    model_config = {"extra": "forbid"}

    intent: Literal["continue"] = "continue"
    target_run_id: UUID
    expected_stage_generation: int | None = Field(
        default=None,
        ge=0,
        description="计划构建时的 Run stage_generation 快照；None = 旧计划，确认时恢复",
    )
    batch_size: int | None = Field(
        default=None, ge=1, le=50,
        description="本批集数：None = 写完剩余全部（与 /runs/{id}/continue 语义一致）",
    )


class MCPToolCallCommand(BaseModel):
    """MCP 外部工具调用命令（MCP-03）。

    模型只提供选择器：qualified_tool_name 必须逐字来自服务端下发的有界
    工具目录；server_id/tool_name/定义 digest 由服务端回填并二次校验。
    不携带 URL、认证信息、Run ID 或任意执行器名称。
    """

    model_config = {"extra": "forbid"}

    intent: Literal["use_external_tool"] = "use_external_tool"
    server_id: str = Field(..., min_length=1, max_length=64)
    tool_name: str = Field(..., min_length=1, max_length=200)
    qualified_tool_name: str = Field(..., min_length=1, max_length=300)
    arguments: dict[str, Any] = Field(default_factory=dict)
    tool_definition_digest: str = Field(
        ..., min_length=64, max_length=64,
        description="计划构建时的工具定义 digest；确认与执行时二次校验",
    )
    purpose: str = Field(default="", max_length=2000, description="调用目的（审计展示）")

    @model_validator(mode="after")
    def _qualified_name_consistent(self) -> MCPToolCallCommand:
        if self.qualified_tool_name != f"mcp__{self.server_id}__{self.tool_name}":
            raise ValueError("qualified_tool_name 必须与 server_id/tool_name 一致")
        return self


AgentCommand = Annotated[
    CreateScriptCommand
    | ExplainCommand
    | ReviseOutlineCommand
    | ReviseScriptCommand
    | EvaluateCommand
    | ContinueCommand
    | MCPToolCallCommand,
    Field(discriminator="intent"),
]


class AgentActionPlan(BaseModel):
    """等待用户确认的结构化执行计划。"""

    model_config = {"extra": "forbid"}

    goal: str = Field(..., min_length=1, max_length=2000)
    intent: AgentIntent
    command: AgentCommand
    target: ActionTarget
    constraints: list[str] = Field(default_factory=list)
    steps: list[ActionStep] = Field(..., min_length=1)
    expected_impact: list[str] = Field(default_factory=list)
    # IR-3 §8.3：触发本计划的用户原始请求。旧计划缺省 None（legacy plan，
    # 沿用旧 constraints）；修订类命令同时把原文写入 command.user_request，
    # 使其可追踪到 Run config 与修订工作流输入。
    user_request: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _intent_matches_command(self) -> AgentActionPlan:
        if self.intent != self.command.intent:
            raise ValueError("plan intent must match command intent")
        return self


class RecommendedNextAction(BaseModel):
    """Outcome 可选的一次后续动作建议。"""

    model_config = {"extra": "forbid"}

    intent: AgentIntent
    target: ActionTarget
    constraints: list[str] = Field(default_factory=list)


class OutcomeEvidenceRef(BaseModel):
    """Outcome 证据锚点（W1-04）——指向本轮实际产出/依据的 Artifact。

    与 evidence_artifact_ids（纯 ID 列表）互补：role 说明该证据在本轮
    任务中的位置（source_script=修订依据 / script=本轮新稿 / …），
    前端据此生成"查看本轮稿件"入口，而非跳通用版本页的 latest。
    """

    model_config = {"extra": "forbid"}

    artifact_id: UUID
    role: Literal[
        "source_script",
        "source_outline",
        "story_bible",
        "outline",
        "script",
        "evaluation",
        "continuity",
        "revision_plan",
    ]


ConstraintCheckStatus = Literal["satisfied", "unsatisfied", "unverified"]


class ConstraintCheck(BaseModel):
    """单条创作要求的核验结果（W1-04）。

    satisfied/unsatisfied 只能来自明确执行过的可复核检查；自然语言
    创作要求（如"女主更主动"）没有对应正文检查时如实标 unverified，
    由作者判断——执行完成、评分上涨、Schema 合法都不自动视为满足。
    """

    model_config = {"extra": "forbid"}

    constraint: str = Field(..., min_length=1, max_length=2000)
    status: ConstraintCheckStatus
    reason: str = Field(default="", max_length=1000)
    evidence_refs: list[UUID] = Field(default_factory=list)


class AgentOutcome(BaseModel):
    """Action 终态的目标达成判断。

    goal_status 保持旧三态（旧客户端兼容）；语义未验证映射
    partially_achieved。verification_status 区分"执行是否结束且结论
    经过核验"（verified=纯确定性结论）与"存在待作者判断的要求"
    （unverified）。旧结果反序列化时默认 unverified——历史行没有
    逐项核验记录，不得自称已验证。
    """

    model_config = {"extra": "forbid"}

    goal_status: AgentGoalStatus
    verification_status: Literal["verified", "unverified"] = "unverified"
    constraint_checks: list[ConstraintCheck] = Field(default_factory=list)
    evidence_artifact_ids: list[UUID] = Field(default_factory=list)
    evidence_refs: list[OutcomeEvidenceRef] = Field(default_factory=list)
    score_delta: float | None = None
    remaining_constraints: list[str] = Field(default_factory=list)
    recommended_next_action: RecommendedNextAction | None = None
    replan_depth: int = Field(default=0, ge=0, le=1)


class ConstraintJudgment(BaseModel):
    """Evaluator 对单条用户语义约束的判断（J-09）。"""

    model_config = {"extra": "forbid"}

    constraint: str = Field(..., min_length=1, max_length=2000)
    satisfied: bool
    reason: str = Field(default="", max_length=1000)


class OutcomeRecommendation(BaseModel):
    """Evaluator 的一次后续动作建议（不含执行句柄，目标由服务端重解析）。"""

    model_config = {"extra": "forbid"}

    intent: AgentIntent
    episode_number: int | None = Field(default=None, ge=1)
    reason: str = Field(default="", max_length=1000)


class AgentOutcomeEvaluatorInput(BaseModel):
    """AgentOutcomeEvaluatorSkill 的服务端输入（J-09）。"""

    model_config = {"extra": "forbid"}

    goal: str = Field(..., min_length=1, max_length=2000)
    intent: AgentIntent
    run_status: str = Field(..., max_length=32)
    deterministic_goal_status: AgentGoalStatus
    user_constraints: list[str] = Field(default_factory=list, max_length=20)
    evidence_summary: str = Field(default="", max_length=6000)


class AgentOutcomeEvaluatorOutput(BaseModel):
    """Evaluator 输出——只允许判断语义约束与建议后续意图。

    模型不得改变 goal_status / score_delta / evidence_artifact_ids；
    这些字段由服务端确定性证据决定，服务端合并时忽略模型的越权输出。
    """

    model_config = {"extra": "forbid"}

    constraint_judgments: list[ConstraintJudgment] = Field(default_factory=list, max_length=20)
    recommended_next_action: OutcomeRecommendation | None = None


class AgentTurnResponse(BaseModel):
    """AgentTurn 的持久化响应快照。"""

    model_config = {"extra": "forbid"}

    id: UUID
    project_id: UUID
    conversation_id: UUID
    user_message_id: UUID
    idempotency_key: str
    request_hash: str
    status: AgentTurnStatus
    turn_type: AgentTurnType | None = None
    response_message_id: UUID | None = None
    action_id: UUID | None = None
    error_code: str | None = None
    error_detail: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentActionResponse(BaseModel):
    """AgentAction 的审计响应快照。"""

    model_config = {"extra": "forbid"}

    id: UUID
    project_id: UUID
    conversation_id: UUID
    agent_turn_id: UUID
    parent_action_id: UUID | None = None
    replan_depth: int = Field(ge=0, le=1)
    intent: AgentIntent
    status: AgentActionStatus
    requires_confirmation: bool
    plan: AgentActionPlan
    source_artifact_ids: list[ArtifactSnapshot] = Field(default_factory=list)
    result: AgentOutcome | None = None
    run_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


def _json_default(value: Any) -> Any:
    """把常见领域值转换为稳定 JSON 表示。"""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (UUID, datetime)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported request hash value: {type(value).__name__}")


def compute_request_hash(payload: Mapping[str, Any] | BaseModel) -> str:
    """计算与字典键顺序无关的规范化 SHA256 请求哈希。"""
    normalized: Any
    normalized = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else dict(payload)
    canonical = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
