"""需求归一化模型 — NormalizedRequirement（§5.4）。

将用户的 Idea、Outline 或文件输入归一化为结构化创作需求。
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class NormalizedRequirement(BaseModel):
    """归一化后的创作需求。

    所有字段必须经过 Pydantic 校验后才能写入 Artifact content。
    """

    model_config = {"extra": "forbid"}

    title: str = Field(..., description="项目标题", min_length=1)
    logline: str = Field(..., description="一句话故事梗概", min_length=1)
    genre: str = Field(..., description="题材标签，如“都市/逆袭/足球”", min_length=1)
    tone: list[str] = Field(default_factory=list, description="调性标签，如“爽文”“热血”")
    audience: str | None = Field(default=None, description="目标受众")
    target_episode_count: int = Field(
        default=10, description="目标总集数，MVP 固定 10", ge=1
    )
    episode_duration_seconds: int = Field(
        default=120, description="单集时长（秒）", ge=1
    )
    protagonist_seed: str = Field(
        ..., description="主角初始设定，如“被青训队抛弃的足球少年”", min_length=1
    )
    conflict_seed: str = Field(
        ..., description="核心冲突种子", min_length=1
    )
    must_have: list[str] = Field(
        default_factory=list, description="必须包含的元素"
    )
    must_avoid: list[str] = Field(
        default_factory=list, description="必须避免的内容"
    )
    source_type: Literal["idea", "outline", "txt", "docx"] = Field(
        ..., description="原始输入类型"
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="模型在不明确之处做出的假设，需返回给用户确认",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="仍需用户澄清的问题",
    )

    @field_validator("tone", "must_have", "must_avoid", "assumptions", "open_questions")
    @classmethod
    def _ensure_list_not_none(cls, v: list[str] | None) -> list[str]:
        """列表字段显式返回空列表，不返回 None。"""
        return v if v is not None else []
