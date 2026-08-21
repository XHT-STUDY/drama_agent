"""Message ORM 模型 — 对话消息表。

对应 DEV_PLAN §6.1 messages 表。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.db.models.conversation import Conversation


class Message(Base, UUIDMixin):
    """对话消息。

    记录会话中的每条用户/AI 消息，
    按 conversation_id + sequence 排序。
    """

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_messages_conversation_sequence"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        comment="所属会话 ID",
    )
    role: Mapped[str] = mapped_column(
        String(20),
        comment="消息角色：user / assistant / system",
    )
    content: Mapped[str] = mapped_column(
        Text,
        default="",
        comment="消息正文（Markdown）",
    )
    kind: Mapped[str] = mapped_column(
        String(30),
        default="text",
        comment="消息类型：text / clarification / action_plan / action_result / error",
    )
    message_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        comment="AgentTurn、Action、Run 与 Artifact 展示引用",
    )
    sequence: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="消息在会话内的序号",
    )

    # 关系
    conversation: Mapped[Conversation] = relationship(  # noqa: F821
        back_populates="messages",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<Message id={self.id!s} role={self.role!r} seq={self.sequence}>"
