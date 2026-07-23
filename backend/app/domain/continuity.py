"""连续性状态模型 — ContinuityState 与子结构（§5.9）。

ContinuityState 追踪跨集的人物状态、伏笔、关系变化和时间线，
是修订后连续性检查的核心数据源。
"""

from pydantic import BaseModel, Field, model_validator

from app.domain.enums import LoopStatus


class EpisodeSummary(BaseModel):
    """单集摘要。

    由 Summarizer 在每集完成后生成，供后续集和连续性检查使用。
    """

    model_config = {"extra": "forbid"}

    episode_number: int = Field(..., description="集号", ge=1)
    summary: str = Field(..., description="剧情摘要", min_length=1)
    key_events: list[str] = Field(
        default_factory=list, description="本集关键事件"
    )
    ending_state: str = Field(
        default="", description="本集结束时的状态描述"
    )


class StoryLoop(BaseModel):
    """故事伏笔/线索。

    追踪一个叙事线索从引入到回收的完整生命周期。
    """

    model_config = {"extra": "forbid"}

    loop_id: str = Field(..., description="伏笔唯一标识", min_length=1)
    description: str = Field(..., description="伏笔描述", min_length=1)
    introduced_episode: int = Field(..., description="引入集号", ge=1)
    resolved_episode: int | None = Field(
        default=None, description="回收集号，null 表示尚未回收", ge=1
    )
    status: LoopStatus = Field(..., description="伏笔状态: open 或 resolved")


class CharacterState(BaseModel):
    """人物状态快照。

    记录角色在某一时刻的身体、情感状态、目标和已知信息。
    """

    model_config = {"extra": "forbid"}

    character_id: str = Field(..., description="角色 ID", min_length=1)
    physical_state: str | None = Field(
        default=None, description="身体状态"
    )
    emotional_state: str | None = Field(
        default=None, description="情感状态"
    )
    current_goal: str = Field(default="", description="当前目标")
    known_information: list[str] = Field(
        default_factory=list, description="已知信息列表"
    )
    last_updated_episode: int = Field(
        ..., description="最后更新集号", ge=1
    )


class RelationshipChange(BaseModel):
    """人物关系变化记录。

    追踪两两角色之间关系的演变。
    """

    model_config = {"extra": "forbid"}

    from_character_id: str = Field(..., description="角色 A ID", min_length=1)
    to_character_id: str = Field(..., description="角色 B ID", min_length=1)
    episode_number: int = Field(..., description="发生变化的集号", ge=1)
    before: str = Field(default="", description="变化前的关系")
    after: str = Field(default="", description="变化后的关系")


class TimelineEvent(BaseModel):
    """时间线事件。

    按集内顺序记录故事的关键时间节点。
    """

    model_config = {"extra": "forbid"}

    event_id: str = Field(..., description="事件唯一标识", min_length=1)
    episode_number: int = Field(..., description="所属集号", ge=1)
    order_in_episode: int = Field(..., description="集内顺序号", ge=1)
    description: str = Field(..., description="事件描述", min_length=1)


class ContinuityState(BaseModel):
    """连续性状态。

    每集完成后更新，记录已完成的集数、人物状态、伏笔状态、
    关系变化和时间线，供后续集写作和修订连续性检查使用。
    """

    model_config = {"extra": "forbid"}

    through_episode: int = Field(
        ..., description="已覆盖到第几集（含）", ge=1
    )
    episode_summaries: list[EpisodeSummary] = Field(
        default_factory=list, description="各集摘要"
    )
    open_loops: list[StoryLoop] = Field(
        default_factory=list, description="尚未回收的伏笔"
    )
    resolved_loops: list[StoryLoop] = Field(
        default_factory=list, description="已回收的伏笔"
    )
    locked_facts: list[str] = Field(
        default_factory=list, description="锁定事实列表"
    )
    character_states: dict[str, CharacterState] = Field(
        default_factory=dict, description="角色 ID → 当前状态"
    )
    relationship_changes: list[RelationshipChange] = Field(
        default_factory=list, description="关系变化记录"
    )
    timeline_events: list[TimelineEvent] = Field(
        default_factory=list, description="时间线事件"
    )

    @model_validator(mode="after")
    def _check_summaries_within_range(self) -> "ContinuityState":
        """单集摘要的集数不能超过 through_episode。"""
        for summary in self.episode_summaries:
            if summary.episode_number > self.through_episode:
                raise ValueError(
                    f"EpisodeSummary 集号 {summary.episode_number} "
                    f"超过 through_episode={self.through_episode}"
                )
        return self

    @model_validator(mode="after")
    def _check_no_duplicate_loop_ids(self) -> "ContinuityState":
        """open_loops 与 resolved_loops 的 loop_id 不可重复。"""
        all_ids = [loop.loop_id for loop in self.open_loops + self.resolved_loops]
        duplicates = {lid for lid in all_ids if all_ids.count(lid) > 1}
        if duplicates:
            raise ValueError(f"发现重复的 loop_id: {sorted(duplicates)}")
        return self
