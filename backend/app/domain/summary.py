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
