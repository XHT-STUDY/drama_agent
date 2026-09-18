"""摘要模型 — SummaryInput 与 SummaryOutput (C-06)。

Summarizer Skill 的输入/输出模型，用于在每集完成后
生成结构化摘要并更新连续性状态。

M-03 新增 v2 typed delta:LLM 只输出受 Pydantic 校验的类型化增量
(设定事实/正文事件/角色知识/关系/伏笔/道具/时间线/作者未来计划),
不含任何用户确认或来源 Artifact 字段——ID 与来源由服务端分配回填。
"""

from typing import Any

from pydantic import BaseModel, Field, model_validator


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


# ========================================================================
# v2 typed delta(M-03,Agent Native W3-01/W3-02)
# ========================================================================


class FactDelta(BaseModel):
    """本集新增的作者事实(正文已发生事件)。"""

    model_config = {"extra": "forbid"}

    fact_id: str | None = Field(
        default=None,
        description="既有事实 ID(仅引用时填);新事实留空,由服务端分配",
    )
    text: str = Field(..., description="事实描述", min_length=1)
    source_scene: int = Field(..., description="发生场景号", ge=1)


class KnowledgeDelta(BaseModel):
    """角色知识变化:某角色在本集得知(或仍未得知)某事实。"""

    model_config = {"extra": "forbid"}

    character_id: str = Field(..., description="角色 ID", min_length=1)
    new_fact_index: int | None = Field(
        default=None,
        description="指向本 delta.facts 的下标(0 起)——本集新事实", ge=0,
    )
    fact_id: str | None = Field(
        default=None, description="既有事实 ID(与 new_fact_index 二选一)"
    )
    learned: bool = Field(default=True, description="True=得知;False=仍未得知")
    source_scene: int = Field(..., description="得知场景号", ge=1)

    @model_validator(mode="after")
    def _exactly_one_ref(self) -> "KnowledgeDelta":
        if (self.new_fact_index is None) == (self.fact_id is None):
            raise ValueError(
                "knowledge 必须且只能提供 new_fact_index 或 fact_id 之一"
            )
        return self


class LoopIntroductionDelta(BaseModel):
    """本集引入(或重开)的伏笔。"""

    model_config = {"extra": "forbid"}

    loop_id: str | None = Field(
        default=None,
        description="重开既有伏笔时填其 ID;新伏笔留空,由服务端分配",
    )
    description: str = Field(..., description="伏笔描述", min_length=1)


class RelationshipDelta(BaseModel):
    """本集关系变化。"""

    model_config = {"extra": "forbid"}

    from_character_id: str = Field(..., min_length=1)
    to_character_id: str = Field(..., min_length=1)
    before: str = Field(default="", description="变化前的关系")
    after: str = Field(default="", description="变化后的关系")


class TimelineDelta(BaseModel):
    """本集时间线事件。"""

    model_config = {"extra": "forbid"}

    event_id: str | None = Field(
        default=None, description="留空由服务端分配稳定 ID"
    )
    order_in_episode: int = Field(..., ge=1)
    description: str = Field(..., min_length=1)


class PropDelta(BaseModel):
    """本集道具归属变化。"""

    model_config = {"extra": "forbid"}

    prop_id: str | None = Field(
        default=None, description="新道具留空,由服务端分配;转移既有道具时填 ID"
    )
    holder_character_id: str = Field(..., min_length=1)
    from_character_id: str | None = Field(
        default=None, description="上一任持有者(首现可为空)"
    )
    source_scene: int = Field(..., ge=1)


class FuturePlanDelta(BaseModel):
    """作者未来计划——尚未发生,不得进入已发生事实。"""

    model_config = {"extra": "forbid"}

    text: str = Field(..., min_length=1)
    reveal_episode: int = Field(..., description="计划揭示/发生集号", ge=1)


class EpisodeDelta(BaseModel):
    """单集 typed delta(v2)——Summarizer 的受校验输出。

    设计约束(MEMORY_DESIGN §7.4):
    - 不含用户确认、来源 Artifact、生效集数等字段——全部由服务端回填;
    - 只描述本集增量;引用既有实体用稳定 ID,新实体留空 ID 由服务端分配;
    - 未来计划只出现在 future_plans,不得同时写进 facts。
    """

    model_config = {"extra": "forbid"}

    episode_number: int = Field(..., ge=1)
    summary: str = Field(..., min_length=1)
    key_events: list[str] = Field(default_factory=list)
    ending_state: str = Field(default="", description="本集结束状态描述")
    facts: list[FactDelta] = Field(default_factory=list)
    knowledge: list[KnowledgeDelta] = Field(default_factory=list)
    loops_introduced: list[LoopIntroductionDelta] = Field(default_factory=list)
    loops_resolved: list[str] = Field(default_factory=list)
    relationships: list[RelationshipDelta] = Field(default_factory=list)
    timeline_events: list[TimelineDelta] = Field(default_factory=list)
    props: list[PropDelta] = Field(default_factory=list)
    future_plans: list[FuturePlanDelta] = Field(default_factory=list)

    @model_validator(mode="after")
    def _future_not_in_facts(self) -> "EpisodeDelta":
        plan_texts = {p.text.strip() for p in self.future_plans}
        for fact in self.facts:
            if fact.text.strip() in plan_texts:
                raise ValueError(
                    f"未来计划「{fact.text[:20]}」不得同时写成已发生事实"
                )
        return self


class EpisodeSummaryV2Input(BaseModel):
    """episode_summary_v2 Prompt 输入模型——供 manifest Schema 校验。"""

    model_config = {"extra": "forbid"}

    episode_number: str = Field(..., description="目标集号(字符串,供模板渲染)")
    script_draft: str = Field(..., description="本集剧本 JSON")
    continuity_context: str = Field(
        default="{}", description="前态投影 JSON(角色/已知事实/开放伏笔/道具)"
    )


class EpisodeSummaryV2(BaseModel):
    """episode_summary Artifact 的 v2 内容 envelope。

    单集派生证据:摘要 + 服务端校验并分配 ID 后的 typed delta,
    绑定确切源稿(source_script_artifact_id)与幂等输入(input_hash)。
    """

    model_config = {"extra": "forbid"}

    content_schema_version: str = Field(default="2.0", description="内容版本")
    episode_number: int = Field(..., ge=1)
    summary: str = Field(..., min_length=1)
    key_events: list[str] = Field(default_factory=list)
    ending_state: str = Field(default="")
    delta: EpisodeDelta = Field(..., description="服务端校验后的 typed delta")
    source_script_artifact_id: str = Field(..., min_length=1)
    input_hash: str = Field(default="", description="派生幂等输入哈希")


# ========================================================================
# 会话摘要模型（G-01 中期记忆）
# ========================================================================


class ConversationSummaryBody(BaseModel):
    """会话摘要 LLM 输出体（G-01）。

    只含 LLM 生成的字段；covered_from/to 等确定性字段由
    ConversationSummaryManager 服务端回填（与 EvaluationReport 的
    overall/need_revision 服务端回填模式一致）。
    """

    model_config = {"extra": "forbid"}

    summary: str = Field(..., description="会话摘要文本（150 字以内）", min_length=1)
    topics: list[str] = Field(
        default_factory=list, description="本次摘要覆盖内容的主题标签列表"
    )


class ConversationSummary(BaseModel):
    """会话摘要 Artifact 内容(G-01;M-02 升级为累计语义 v2)。

    v1(分段):covered_from/to 描述本段覆盖区间,summary 只含本段增量。
    v2(累计):covered_from 通常为 1,summary 含截至 covered_to 的全部
    历史意图;新增 content_schema_version / previous_summary_artifact_id /
    source_message_digest 三个 v2 字段(v1 内容缺省,保持可读)。
    确定性字段(conversation_id/覆盖范围/来源链)由服务端回填。
    """

    model_config = {"extra": "forbid"}

    conversation_id: str = Field(..., description="所属会话 ID")
    summary: str = Field(..., description="累计会话摘要文本", min_length=1)
    topics: list[str] = Field(
        default_factory=list, description="累计主题标签列表"
    )
    covered_from_sequence: int = Field(
        ..., description="累计覆盖起始消息序号(v2 通常为 1)", ge=1
    )
    covered_to_sequence: int = Field(
        ..., description="已压缩到的最后消息序号", ge=1
    )
    message_count: int = Field(..., description="累计覆盖消息条数", ge=0)
    content_schema_version: str = Field(
        default="1.0",
        description="摘要内容版本:v1=分段语义,v2=累计语义(M-02)",
    )
    previous_summary_artifact_id: str | None = Field(
        default=None,
        description="上一版累计摘要 Artifact ID(v2 链;迁移场景为最新 v1)",
    )
    source_message_digest: str | None = Field(
        default=None,
        description="本次新增消息区间的确定性摘要哈希(幂等输入之一)",
    )


class ConversationSummaryInput(BaseModel):
    """会话摘要 Prompt 输入模型(G-01)—— 供 manifest Schema 校验。"""

    model_config = {"extra": "forbid"}

    conversation_transcript: str = Field(
        ..., description="会话消息逐条转录文本(含序号与角色)"
    )
    message_count: str = Field(
        default="", description="转录消息条数(字符串,供模板渲染)"
    )


class ConversationSummaryV2Input(ConversationSummaryInput):
    """累计会话摘要 Prompt 输入模型(M-02)。

    v2 渲染把上一版累计摘要与新增区间一起交给模型:
    summary_k = summarize(summary_{k-1}, messages[new_from:new_to])。
    """

    previous_summary: str = Field(
        default="", description="上一版累计摘要文本(首版为空)"
    )
