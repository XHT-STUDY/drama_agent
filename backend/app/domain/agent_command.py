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
    "needs_review": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "stale": frozenset(),
    "rejected": frozenset(),
}


class ActiveArtifactContext(BaseModel):
    """用户发起 Turn 时显式选中的页面上下文。"""

    model_config = {"extra": "forbid"}

    artifact_id: UUID
    artifact_type: str = Field(..., min_length=1, max_length=50)
    episode_number: int | None = Field(default=None, ge=1)
    version: int | None = Field(default=None, ge=1)
    checksum: str | None = Field(default=None, min_length=64, max_length=64)


class ActionTarget(BaseModel):
    """服务端解析后的受限动作目标。"""

    model_config = {"extra": "forbid"}

    target_type: Literal[
        "project",
        "story_bible",
        "outline",
        "script",
        "evaluation",
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


class ReviseScriptCommand(BaseModel):
    """指定剧本版本的修订命令。"""

    model_config = {"extra": "forbid"}

    intent: Literal["revise_script"] = "revise_script"
    source_script_id: UUID
    episode_number: int = Field(..., ge=1)
    constraints: list[str] = Field(default_factory=list)


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


AgentCommand = Annotated[
    CreateScriptCommand | ExplainCommand | ReviseOutlineCommand | ReviseScriptCommand | EvaluateCommand,
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


class AgentOutcome(BaseModel):
    """Action 终态的目标达成判断。"""

    model_config = {"extra": "forbid"}

    goal_status: AgentGoalStatus
    evidence_artifact_ids: list[UUID] = Field(default_factory=list)
    score_delta: float | None = None
    remaining_constraints: list[str] = Field(default_factory=list)
    recommended_next_action: RecommendedNextAction | None = None
    replan_depth: int = Field(default=0, ge=0, le=1)


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
