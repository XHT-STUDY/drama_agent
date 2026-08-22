"""对话命令 Planner 的非执行领域契约（J-03）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.agent_command import ActiveArtifactContext


class AgentPlannerInput(BaseModel):
    """Planner 的服务端输入；available_intents 由服务端生成。"""

    model_config = {"extra": "forbid"}

    user_request: str = Field(..., min_length=1, max_length=4000)
    project_title: str = Field(default="", max_length=200)
    target_episode_count: int = Field(default=1, ge=1, le=500)
    available_intents: list[str] = Field(..., min_length=1)
    active_context: ActiveArtifactContext | None = None
    project_context: str = Field(default="", max_length=12000)
    unresolved_turn_count: int = Field(default=0, ge=0, le=3)


class PlannerTarget(BaseModel):
    """仅描述用户可读目标，不携带 Artifact ID 或执行句柄。"""

    model_config = {"extra": "forbid"}

    target_type: Literal[
        "project", "story_bible", "outline", "script", "evaluation"
    ]
    episode_number: int | None = Field(default=None, ge=1)


class PlannerStep(BaseModel):
    """可读的意图说明；不是可执行 ActionStep。"""

    model_config = {"extra": "forbid"}

    title: str = Field(..., min_length=1, max_length=120)
    description: str = Field(..., min_length=1, max_length=500)


class AgentPlannerOutput(BaseModel):
    """Planner 输出的非执行计划。"""

    model_config = {"extra": "forbid"}

    turn_type: Literal["clarification", "answer", "plan"]
    intent: str | None = Field(default=None, min_length=1, max_length=40)
    target: PlannerTarget | None = None
    constraints: list[str] = Field(default_factory=list, max_length=20)
    steps: list[PlannerStep] = Field(default_factory=list, max_length=12)
    expected_impact: list[str] = Field(default_factory=list, max_length=20)
    clarification_question: str | None = Field(default=None, max_length=1000)
    answer: str | None = Field(default=None, max_length=4000)
