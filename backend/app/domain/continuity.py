"""连续性状态模型 — ContinuityState 与子结构(§5.9;M-03 v2)。

ContinuityState 追踪跨集的人物状态、伏笔、关系变化和时间线,
是修订后连续性检查的核心数据源。

v2(M-03,对应 Agent Native W3-01/W3-02):
- 状态 envelope 绑定确切工作集(basis:StoryBible/大纲/各集剧本引用);
- 作者事实 / 角色知识 / 道具归属 / 作者未来计划带来源引用
  (source_artifact_id + scene + episode),可回答"从哪一稿哪一场得到";
- v1 字段全部保留且可选——旧 Artifact 用同一模型可读
  (content_schema_version 缺省 "1.0");
- LLM 只产出 typed delta(见 domain.summary.EpisodeDelta),
  状态更新由纯 reducer 完成,服务端控制 ID 与来源。
"""

from pydantic import BaseModel, Field, model_validator

from app.domain.enums import LoopStatus


class EpisodeSummary(BaseModel):
    """单集摘要。

    由 Summarizer 在每集完成后生成,供后续集和连续性检查使用。
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
    introduced_episode: int = Field(..., description="引入集号（0 表示 StoryBible 阶段）", ge=0)
    resolved_episode: int | None = Field(
        default=None, description="回收集号，null 表示尚未回收", ge=1
    )
    status: LoopStatus = Field(..., description="伏笔状态: open 或 resolved")
    source_artifact_id: str | None = Field(
        default=None, description="引入/最近变化来源的剧本 Artifact ID(v2)"
    )


class CharacterKnowledge(BaseModel):
    """角色已知的一条事实(v2)。

    与作者事实(fact)分离:作者知道 ≠ 角色知道。
    """

    model_config = {"extra": "forbid"}

    fact_id: str = Field(..., description="事实稳定 ID", min_length=1)
    learned_episode: int = Field(..., description="得知集号", ge=1)
    source_scene: int = Field(..., description="得知场景号", ge=1)
    source_artifact_id: str = Field(
        default="", description="来源剧本 Artifact ID(服务端回填)"
    )


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
        default_factory=list, description="已知信息列表(v1 文本;v2 见 known_facts)"
    )
    known_facts: list[CharacterKnowledge] = Field(
        default_factory=list,
        description="角色已知事实(v2,带来源;learned 集数之前不得越界)",
    )
    last_updated_episode: int = Field(
        ..., description="最后更新集号（0 表示 StoryBible 初始状态）", ge=0
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
    source_artifact_id: str | None = Field(
        default=None, description="来源剧本 Artifact ID(v2)"
    )


class FactRecord(BaseModel):
    """作者事实记录(v2)——已在正文发生的事,带来源引用。"""

    model_config = {"extra": "forbid"}

    text: str = Field(..., description="事实描述", min_length=1)
    source_episode: int = Field(..., description="发生集号", ge=1)
    source_scene: int = Field(..., description="发生场景号", ge=1)
    source_artifact_id: str = Field(
        default="", description="来源剧本 Artifact ID(服务端回填)"
    )


class PropRecord(BaseModel):
    """道具归属记录(v2)——当前持有者与最近一次转移来源。"""

    model_config = {"extra": "forbid"}

    holder_character_id: str = Field(..., description="当前持有角色 ID", min_length=1)
    from_character_id: str | None = Field(
        default=None, description="上一任持有者(首现可为空)"
    )
    source_episode: int = Field(..., description="最近转移集号", ge=1)
    source_scene: int = Field(..., description="最近转移场景号", ge=1)
    source_artifact_id: str = Field(
        default="", description="来源剧本 Artifact ID(服务端回填)"
    )


class FuturePlan(BaseModel):
    """作者未来计划(v2)——尚未在正文发生,不得当作已发生事实。"""

    model_config = {"extra": "forbid"}

    text: str = Field(..., description="计划内容", min_length=1)
    reveal_episode: int = Field(..., description="计划揭示/发生集号", ge=1)
    revealed: bool = Field(default=False, description="是否已在正文发生")
    source_episode: int = Field(..., description="计划记录时的集号", ge=0)


class StateBasis(BaseModel):
    """状态依据的确切工作集(v2)。

    记录本状态链使用的 StoryBible / 大纲 / 各集剧本 Artifact 引用,
    以及上一状态的 Artifact ID——回答"使用了哪一版"。
    """

    model_config = {"extra": "forbid"}

    story_bible_artifact_id: str = Field(..., min_length=1)
    outline_artifact_id: str = Field(..., min_length=1)
    script_artifact_ids: dict[str, str] = Field(
        default_factory=dict, description="集号(str) → 剧本 Artifact ID"
    )
    episode_summary_artifact_ids: dict[str, str] = Field(
        default_factory=dict, description="集号(str) → 单集证据 Artifact ID"
    )
    previous_state_artifact_id: str | None = Field(
        default=None, description="上一状态 Artifact ID(首态为空)"
    )


class ContinuityState(BaseModel):
    """连续性状态。

    每集完成后更新,记录已完成的集数、人物状态、伏笔状态、
    关系变化和时间线,供后续集写作和修订连续性检查使用。

    v2 字段(facts/props/future_plans/basis/known_facts)全部可选,
    v1 内容用本模型可直接读取(content_schema_version 缺省 "1.0")。
    """

    model_config = {"extra": "forbid"}

    content_schema_version: str = Field(
        default="1.0", description="状态内容版本:v1=初始,v2=typed delta(M-03)"
    )
    basis: StateBasis | None = Field(
        default=None, description="本状态绑定的确切工作集(v2)"
    )
    through_episode: int = Field(
        ..., description="已覆盖到第几集（含，0 表示未完成任何集）", ge=0
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
    facts: dict[str, FactRecord] = Field(
        default_factory=dict,
        description="作者事实(v2):fact_id → 带来源的事实记录",
    )
    props: dict[str, PropRecord] = Field(
        default_factory=dict,
        description="道具归属(v2):prop_id → 当前持有与最近转移",
    )
    future_plans: list[FuturePlan] = Field(
        default_factory=list, description="作者未来计划(v2,未发生)"
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

    @model_validator(mode="after")
    def _check_v2_intervals_and_refs(self) -> "ContinuityState":
        """v2 区间与引用校验(仅 v2 状态;v1 无这些字段)。

        - 事实/道具/时间线的来源集数不得超过 through_episode;
        - 角色知识引用的 fact_id 必须存在于 facts(作者事实与角色知识
          分账,但知识必须指向某条事实);
        - 未来计划在 reveal 集数之前不得标记 revealed。
        """
        if self.content_schema_version != "2.0":
            return self
        for fid, fact in self.facts.items():
            if fact.source_episode > self.through_episode:
                raise ValueError(
                    f"事实 {fid} 来源集数 {fact.source_episode} "
                    f"超过 through_episode={self.through_episode}"
                )
        for pid, prop in self.props.items():
            if prop.source_episode > self.through_episode:
                raise ValueError(
                    f"道具 {pid} 来源集数 {prop.source_episode} "
                    f"超过 through_episode={self.through_episode}"
                )
        for cid, cstate in self.character_states.items():
            for knowledge in cstate.known_facts:
                if knowledge.fact_id not in self.facts:
                    raise ValueError(
                        f"角色 {cid} 的知识引用了不存在的事实 "
                        f"{knowledge.fact_id}"
                    )
                if knowledge.learned_episode > self.through_episode:
                    raise ValueError(
                        f"角色 {cid} 在第 {knowledge.learned_episode} 集得知 "
                        f"{knowledge.fact_id},超过 through_episode="
                        f"{self.through_episode}"
                    )
        for plan in self.future_plans:
            if plan.revealed and plan.reveal_episode > self.through_episode:
                raise ValueError(
                    f"未来计划「{plan.text[:20]}」在第 "
                    f"{self.through_episode} 集前被标记已发生"
                )
        return self
