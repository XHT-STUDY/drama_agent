"""剧本模型 — EpisodeWriterInput、DialogueLine、Scene 与 ScriptDraft (§5.7, C-05).

word_count 和 dialogue_ratio 由确定性 Tool 计算，
不可信任 LLM 自报数值。
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class EpisodeWriterInput(BaseModel):
    """Episode Writer Skill 的输入模型 (C-05).

    封装单集大纲、StoryBible、前集摘要和连续性状态。
    """

    model_config = {"extra": "forbid"}

    episode_number: int = Field(..., description="目标集号", ge=1)
    episode_outline: dict[str, Any] = Field(
        ..., description="本集大纲 (EpisodeOutline 的 dict 表示)"
    )
    story_bible: dict[str, Any] = Field(
        ..., description="完整 StoryBible (dict 表示)"
    )
    previous_summary: str = Field(
        default="", description="前集摘要文本 (第1集为空)"
    )
    continuity_state: str = Field(
        default="", description="当前连续性状态 (人物状态/伏笔/时间线)"
    )
    rag_context: str = Field(
        default="", description="知识库检索片段 (MVP 可为空)"
    )


class DialogueLine(BaseModel):
    """单句对白。"""

    model_config = {"extra": "forbid"}

    speaker: str = Field(..., description="说话角色名", min_length=1)
    text: str = Field(..., description="对白文本", min_length=1)
    parenthetical: str | None = Field(
        default=None, description="括号内动作提示，如“(低声)”"
    )


class Scene(BaseModel):
    """单场戏。"""

    model_config = {"extra": "forbid"}

    scene_number: int = Field(..., description="场次编号", ge=1)
    location: str = Field(..., description="场景地点", min_length=1)
    time_of_day: str = Field(..., description="时间，如“日/夜/傍晚”", min_length=1)
    characters: list[str] = Field(default_factory=list, description="出场角色列表")
    action: str = Field(..., description="动作描写", min_length=1)
    dialogue: list[DialogueLine] = Field(
        default_factory=list, description="对白列表"
    )


class ScriptDraft(BaseModel):
    """单集剧本草稿。

    每集独立为一个 Artifact；word_count 与 dialogue_ratio
    由服务端确定性 Tool 计算后覆盖 LLM 自报值。
    """

    model_config = {"extra": "forbid"}

    episode_number: int = Field(..., description="集号", ge=1)
    title: str = Field(..., description="本集标题", min_length=1)
    opening_hook: str = Field(..., description="开头钩子文本", min_length=1)
    scenes: list[Scene] = Field(..., description="场次列表")
    ending_hook: str = Field(..., description="结尾钩子文本", min_length=1)
    plain_text: str = Field(..., description="纯文本全文", min_length=1)
    word_count: int = Field(default=0, description="服务端计算的字数", ge=0)
    dialogue_ratio: float = Field(
        default=0.0, description="服务端计算的对白占比", ge=0.0, le=1.0
    )
    referenced_outline_artifact_id: UUID = Field(
        ..., description="引用的分集大纲 Artifact ID"
    )

    @model_validator(mode="after")
    def _check_min_scenes(self) -> "ScriptDraft":
        """每集至少需要 2 场戏。"""
        if len(self.scenes) < 2:
            raise ValueError(
                f"第 {self.episode_number} 集至少需要 2 场戏，"
                f"当前为 {len(self.scenes)}"
            )
        return self

    @model_validator(mode="after")
    def _check_scene_numbers_consecutive(self) -> "ScriptDraft":
        """场次编号应连续且不重复，从 1 开始。"""
        numbers = [s.scene_number for s in self.scenes]
        expected = list(range(1, len(numbers) + 1))
        if sorted(numbers) != expected:
            duplicates = {n for n in numbers if numbers.count(n) > 1}
            if duplicates:
                raise ValueError(f"场次编号重复: {sorted(duplicates)}")
            raise ValueError(
                f"场次编号不连续，期望从 1 开始的连续编号，"
                f"实际: {sorted(numbers)}"
            )
        return self
