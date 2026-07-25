"""摘要模型 — SummaryInput 与 SummaryOutput (C-06).

Summarizer Skill 的输入/输出模型，用于在每集完成后
生成结构化摘要并更新连续性状态。
"""

from typing import Any

from pydantic import BaseModel, Field


class SummaryInput(BaseModel):
    """Summarizer Skill 的输入模型 (C-06).

    封装剧本草稿与当前连续性状态，供 SummarizerSkill 消费。
    """

    model_config = {"extra": "forbid"}

    episode_number: int = Field(..., description="目标集号", ge=1)
    script_draft: dict[str, Any] = Field(
        ..., description="本集 ScriptDraft 的 dict 表示"
    )
    continuity_state: dict[str, Any] = Field(
        default_factory=dict, description="当前 ContinuityState 的 dict 表示"
    )


class SummaryOutput(BaseModel):
    """Summarizer Skill 的综合输出 (C-06).

    包含剧集摘要文本和连续性更新数据。
    ContinuityManager 使用此输出更新 ContinuityState。
    """

    model_config = {"extra": "forbid"}

    episode_number: int = Field(..., description="集号", ge=1)
    summary: str = Field(..., description="剧情摘要", min_length=1)
    key_events: list[str] = Field(
        default_factory=list, description="本集关键事件列表"
    )
    ending_state: str = Field(
        default="", description="本集结束时的状态描述"
    )
    character_changes: list[dict[str, Any]] = Field(
        default_factory=list,
        description="人物状态变化列表，每项含 character_id/name/field/changes",
    )
    new_loops: list[dict[str, Any]] = Field(
        default_factory=list,
        description="本集新引入的伏笔，每项含 loop_id/description",
    )
    resolved_loops: list[str] = Field(
        default_factory=list,
        description="本集回收的伏笔 loop_id 列表",
    )
    timeline_events: list[dict[str, Any]] = Field(
        default_factory=list,
        description="本集时间线事件，每项含 event_id/description/order_in_episode",
    )
