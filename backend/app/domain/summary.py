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
    """会话摘要 Artifact 内容（G-01）。

    记录摘要覆盖的消息序号范围（covered_from/to），供后续创作读取
    —— 项目记忆以"摘要 Artifact 指针"形式存在，不复制全文。
    """

    model_config = {"extra": "forbid"}

    conversation_id: str = Field(..., description="所属会话 ID")
    summary: str = Field(..., description="会话摘要文本", min_length=1)
    topics: list[str] = Field(
        default_factory=list, description="本次摘要覆盖内容的主题标签列表"
    )
    covered_from_sequence: int = Field(
        ..., description="本段摘要覆盖的起始消息序号", ge=1
    )
    covered_to_sequence: int = Field(
        ..., description="本段摘要覆盖的结束消息序号", ge=1
    )
    message_count: int = Field(..., description="本段摘要覆盖的消息条数", ge=0)


class ConversationSummaryInput(BaseModel):
    """会话摘要 Prompt 输入模型（G-01）—— 供 manifest Schema 校验。"""

    model_config = {"extra": "forbid"}

    conversation_transcript: str = Field(
        ..., description="会话消息逐条转录文本（含序号与角色）"
    )
    message_count: str = Field(
        default="", description="转录消息条数（字符串，供模板渲染）"
    )
