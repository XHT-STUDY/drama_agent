"""对话命令 Planner 的非执行领域契约（J-03）。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.agent_command import ActiveArtifactContext

# ---- 目标对象关键词表（W1-03：Planner 预检与解释目标解析的同一词表） ----
TARGET_KEYWORDS: dict[str, list[str]] = {
    "outline": ["大纲"],
    "story_bible": ["设定", "故事圣经", "story bible", "storybible"],
}
TARGET_OUTLINE_RE = re.compile("|".join(TARGET_KEYWORDS["outline"]))
TARGET_STORY_BIBLE_RE = re.compile(
    "|".join(TARGET_KEYWORDS["story_bible"]), re.IGNORECASE
)


class AgentPlannerInput(BaseModel):
    """Planner 的服务端输入；available_intents 由服务端生成。"""

    model_config = {"extra": "forbid"}

    user_request: str = Field(..., min_length=1, max_length=4000)
    project_title: str = Field(default="", max_length=200)
    target_episode_count: int = Field(default=1, ge=1, le=500)
    available_intents: list[str] = Field(..., min_length=1)
    active_context: ActiveArtifactContext | None = None
    # W1-03：builder 按 agent_context_budget_tokens（token）裁剪；字符上限
    # 只防异常超大输入，不再是二次裁切
    project_context: str = Field(default="", max_length=60000)
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
    batch_size: int | None = Field(
        default=None, ge=1, le=50,
        description="仅 intent=continue 时有意义：本批集数，缺省写完全部",
    )


# ============================================================
# 原文解释（W1-03）——ArtifactExplainerSkill 的输入/输出契约
# ============================================================


class ExplanationSourceText(BaseModel):
    """解释的单个原文来源（服务端组装并编号，模型只回引 source_index）。"""

    model_config = {"extra": "forbid"}

    source_index: int = Field(..., ge=1)
    kind: Literal["script_scene", "story_bible", "outline"]
    label: str = Field(..., max_length=120, description="给模型看的位置说明")
    scene_number: int | None = Field(default=None, ge=1)
    text: str = Field(..., max_length=60000)


class ArtifactExplanationInput(BaseModel):
    """解释模型输入：用户问题 + 已读原文（受预算约束的服务端组装）。"""

    model_config = {"extra": "forbid"}

    question: str = Field(..., min_length=1, max_length=4000)
    target_label: str = Field(..., max_length=200, description="解释对象（如 第3集 v2）")
    sources: list[ExplanationSourceText] = Field(..., min_length=1, max_length=20)


class ExplanationCitationOutput(BaseModel):
    """模型输出的一条引文：仅 source_index + 场次 + 逐字原文。

    服务端验证 quote 确实出现在对应来源原文中，再回填
    ArtifactCitation（artifact_id/version/checksum）。
    """

    model_config = {"extra": "forbid"}

    source_index: int = Field(..., ge=1)
    scene_number: int | None = Field(default=None, ge=1)
    quote: str = Field(..., min_length=1, max_length=200)


class ArtifactExplanationOutput(BaseModel):
    """解释模型输出：回答 + 引用的 source_index（不含任何 Artifact ID）。"""

    model_config = {"extra": "forbid"}

    answer: str = Field(..., min_length=1, max_length=2000)
    citations: list[ExplanationCitationOutput] = Field(
        default_factory=list, max_length=10,
        description="支撑回答的原文引文；无法引用原文的论断不应出现在 answer 中",
    )
