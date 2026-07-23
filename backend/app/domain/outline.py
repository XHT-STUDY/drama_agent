"""分集大纲模型 — EpisodeOutline 与 EpisodeOutlineSet（§5.6）。

MVP 固定输出 10 集大纲，集号 1..10 连续不重复。
"""

from pydantic import BaseModel, Field, model_validator


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

    包含全部 10 集大纲和整体弧线说明。
    校验器确保集数、编号和连续性符合 MVP 要求。
    """

    model_config = {"extra": "forbid"}

    episodes: list[EpisodeOutline] = Field(..., description="分集大纲列表")
    arc_summary: str = Field(..., description="整体故事弧线概述", min_length=1)
    validation_notes: list[str] = Field(
        default_factory=list, description="结构性校验备注"
    )

    @model_validator(mode="after")
    def _check_episode_count(self) -> "EpisodeOutlineSet":
        """MVP 要求正好 10 集。"""
        if len(self.episodes) != 10:
            raise ValueError(
                f"大纲集数必须为 10，当前为 {len(self.episodes)}"
            )
        return self

    @model_validator(mode="after")
    def _check_episode_numbers(self) -> "EpisodeOutlineSet":
        """集号必须为 1..10，连续且不重复。"""
        numbers = [ep.episode_number for ep in self.episodes]
        expected = list(range(1, 11))
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
        return notes
