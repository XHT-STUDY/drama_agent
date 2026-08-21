"""AgentTurn ORM 模型。

每个 Agent 请求先落一条 Turn 收据，覆盖澄清、回答、计划和失败分支的幂等恢复。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin


class AgentTurn(Base, UUIDMixin):
    """一次用户请求的持久化收据与 Planner 租约。"""

    __tablename__ = "agent_turns"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_agent_turns_project_idempotency",
        ),
        CheckConstraint(
            "status IN ('received', 'planning', 'needs_input', 'answered', 'action_proposed', 'failed')",
            name="ck_agent_turns_status",
        ),
        CheckConstraint(
            "turn_type IS NULL OR turn_type IN ('clarification', 'answer', 'plan')",
            name="ck_agent_turns_turn_type",
        ),
        Index("ix_agent_turns_project_status", "project_id", "status"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"),
        comment="所属项目 ID",
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT"),
        index=True,
        comment="所属会话 ID",
    )
    user_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="RESTRICT"),
        unique=True,
        comment="触发本 Turn 的用户消息 ID",
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(128),
        comment="项目内唯一的请求幂等键",
    )
    request_hash: Mapped[str] = mapped_column(
        String(64),
        comment="规范化请求载荷的 SHA256",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="received",
        comment="Turn 状态",
    )
    turn_type: Mapped[str | None] = mapped_column(
        String(20),
        default=None,
        comment="最终响应类型：clarification / answer / plan",
    )
    planner_output: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        default=None,
        comment="结构化 Planner 输出快照",
    )
    response_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        default=None,
        comment="最终 assistant 消息 ID",
    )
    planning_lease_owner: Mapped[str | None] = mapped_column(
        String(100),
        default=None,
        comment="当前 Planner 租约持有者",
    )
    planning_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
        comment="Planner 租约过期时间",
    )
    planning_attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="Planner 租约领取次数",
    )
    error_code: Mapped[str | None] = mapped_column(
        String(80),
        default=None,
        comment="失败时机器可读错误码",
    )
    error_detail: Mapped[str | None] = mapped_column(
        String(2000),
        default=None,
        comment="失败详情（已截断）",
    )

    def __repr__(self) -> str:
        return f"<AgentTurn id={self.id!s} status={self.status!r}>"
