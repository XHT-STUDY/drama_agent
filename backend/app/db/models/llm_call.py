"""LLMCall ORM 模型 — 模型调用追踪。

对应 DEV_PLAN §6.1 llm_calls 表。
记录每次 LLM 调用的模型、参数、用量和状态。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin


class LLMCall(Base, UUIDMixin):
    """LLM 调用追踪记录。

    每次向 LLM 发起请求时创建一条记录，
    记录模型名、尝试次数、token 用量和最终状态。
    """

    __tablename__ = "llm_calls"

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        index=True,
        default=None,
        comment="所属 Run ID（独立调用可为空）",
    )
    node_name: Mapped[str] = mapped_column(
        String(100),
        comment="调用节点：normalize / story_bible / outline / write / evaluate / summarize",
    )
    model: Mapped[str] = mapped_column(
        String(100),
        comment="模型名标识",
    )
    attempt: Mapped[int] = mapped_column(
        Integer,
        default=1,
        comment="第几次尝试（含重试）",
    )
    prompt_version: Mapped[str] = mapped_column(
        String(20),
        default="",
        comment="使用的 Prompt 版本号",
    )
    input_artifact_ids: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        default=None,
        comment="输入 Artifact ID 列表：[{artifact_id, version}]",
    )
    usage: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        default=None,
        comment="Token 用量：{prompt_tokens, completion_tokens, total_tokens}",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        comment="调用状态：pending / success / retry / failed",
    )
    duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        default=None,
        comment="调用耗时（毫秒）",
    )

    def __repr__(self) -> str:
        return (
            f"<LLMCall id={self.id!s} node={self.node_name!r} "
            f"model={self.model!r} attempt={self.attempt}>"
        )
