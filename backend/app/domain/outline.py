"""分集大纲模型 — OutlineInput、EpisodeOutline 与 EpisodeOutlineSet (§5.6, C-04).

支持可配置集数 N，集号 1..N 连续不重复（精确集数由 OutlineSkill 校验）。
"""

from typing import Any

from pydantic import BaseModel, Field, model_validator


class OutlineInput(BaseModel):
    """Outline Skill 的输入模型 (C-04).

    封装 StoryBible 与知识库上下文，供 OutlineSkill 消费。
    """

    model_config = {"extra": "forbid"}

    story_bible: dict[str, Any] = Field(
        ..., description="已生成的 StoryBible (dict 表示)"
    )
    rag_context: str = Field(
        default="", description="知识库检索片段 (MVP 阶段可为空)"
    )
    outline_count: int = Field(
        default=10, description="目标集数", ge=1, le=100
    )


class EpisodeOutline(BaseModel):
    """单集大纲。

    描述一集的开头钩子、目标、冲突、关键事件和结尾钩子。
    """

    model_config = {"extra": "forbid"}

    episode_number: int = Field(..., description="集号，从 1 开始", ge=1)
    title: str = Field(..., description="本集标题", min_length=1)
    opening_hook: str = Field(..., description="开头钩子", min_length=1)
    objective: str = Field(..., description="本集目标", min_length=1)
    core_conflict: str = Field(..., description="核心冲突", min_length=1)
    key_events: list[str] = Field(..., description="关键事件列表")
    payoff: str = Field(..., description="爽点或看点", min_length=1)
    ending_hook: str = Field(..., description="结尾钩子", min_length=1)
    next_bridge: str = Field(
        default="", description="与下一集的衔接提示"
    )
    introduced_loops: list[str] = Field(
        default_factory=list, description="本集新引入的伏笔"
    )
    resolved_loops: list[str] = Field(
        default_factory=list, description="本集回收的伏笔"
    )
    required_characters: list[str] = Field(
        default_factory=list, description="本集必须出场的角色 ID 列表"
    )

    @model_validator(mode="after")
    def _check_key_events_min_count(self) -> "EpisodeOutline":
        """每集至少需要 2 个关键事件。"""
        if len(self.key_events) < 2:
            raise ValueError(
                f"第 {self.episode_number} 集 key_events 至少需要 2 个，"
                f"当前为 {len(self.key_events)}"
            )
        return self


class EpisodeOutlineSet(BaseModel):
    """分集大纲集合。

    包含全部 N 集大纲和整体弧线说明。
    校验器确保至少 1 集、编号 1..N 连续不重复。
    """

    model_config = {"extra": "forbid"}

    episodes: list[EpisodeOutline] = Field(..., description="分集大纲列表")
    arc_summary: str = Field(..., description="整体故事弧线概述", min_length=1)
    validation_notes: list[str] = Field(
        default_factory=list, description="结构性校验备注"
    )

    @model_validator(mode="after")
    def _check_episode_count(self) -> "EpisodeOutlineSet":
        """至少需要 1 集（精确集数由 OutlineSkill 按 outline_count 校验）。"""
        if len(self.episodes) < 1:
            raise ValueError(
                f"大纲集数至少为 1，当前为 {len(self.episodes)}"
            )
        return self

    @model_validator(mode="after")
    def _check_episode_numbers(self) -> "EpisodeOutlineSet":
        """集号必须为 1..N（N=实际集数），连续且不重复。"""
        numbers = [ep.episode_number for ep in self.episodes]
        expected = list(range(1, len(self.episodes) + 1))
        if sorted(numbers) != expected:
            missing = set(expected) - set(numbers)
            extra = set(numbers) - set(expected)
            duplicates = {n for n in numbers if numbers.count(n) > 1}
            msg_parts = []
            if missing:
                msg_parts.append(f"缺失集号: {sorted(missing)}")
            if extra:
                msg_parts.append(f"超出范围集号: {sorted(extra)}")
            if duplicates:
                msg_parts.append(f"重复集号: {sorted(duplicates)}")
            raise ValueError("；".join(msg_parts))
        return self

    def validate_sequence(self) -> list[str]:
        """检查相邻集之间的 next_bridge 衔接。

        Returns:
            衔接问题的描述列表，空列表表示无问题。
        """
        notes: list[str] = []
        for i in range(len(self.episodes) - 1):
            current = self.episodes[i]
            next_ep = self.episodes[i + 1]
            if not current.next_bridge or not current.next_bridge.strip():
                notes.append(
                    f"第 {current.episode_number} 集缺少 next_bridge，"
                    f"与第 {next_ep.episode_number} 集的衔接不明确"
                )
            # 最后一集不需要 next_bridge (这是最后一集)
        # 检查最后一集是否为小阶段高潮而非强制大结局
        if self.episodes:
            last = self.episodes[-1]
            finale_keywords = ["大结局", "全剧终", "剧终", "完结"]
            for kw in finale_keywords:
                if kw in last.title or kw in last.ending_hook:
                    notes.append(
                        f"第 {last.episode_number} 集包含 '{kw}'，"
                        f"暗示强制大结局而非小阶段高潮"
                    )
        return notes

    def validate_characters(self, story_bible: dict[str, Any]) -> list[str]:
        """检查所有 required_characters 均在 StoryBible 中存在。

        Args:
            story_bible: StoryBible 的 dict 表示 (含 protagonist/antagonist/supporting_characters)

        Returns:
            引用不存在角色的错误列表，空列表表示全部存在。
        """
        # 收集所有已知的角色 ID
        known_ids: set[str] = set()
        protag = story_bible.get("protagonist", {})
        antag = story_bible.get("antagonist", {})
        supporting = story_bible.get("supporting_characters", [])

        if protag and isinstance(protag, dict):
            known_ids.add(protag.get("character_id", ""))
        if antag and isinstance(antag, dict):
            known_ids.add(antag.get("character_id", ""))
        for char in (supporting or []):
            if isinstance(char, dict):
                known_ids.add(char.get("character_id", ""))

        # 丢弃空 ID
        known_ids.discard("")

        errors: list[str] = []
        for ep in self.episodes:
            for cid in ep.required_characters:
                if cid not in known_ids:
                    errors.append(
                        f"第 {ep.episode_number} 集引用了不存在的角色 '{cid}'"
                    )
        return errors
