"""Conversation ORM 模型 — 会话表。

对应 DEV_PLAN §6.1 conversations 表。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, UUIDMixin

if TYPE_CHECKING:
    from app.db.models.message import Message
    from app.db.models.project import Project


class Conversation(Base, UUIDMixin, SoftDeleteMixin):
    """对话会话。

    一个项目下可有多个会话，
    每个会话包含多条消息（messages）。
    """

    __tablename__ = "conversations"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"),
        comment="所属项目 ID",
    )
    title: Mapped[str] = mapped_column(
        String(200),
        default="",
        comment="会话标题（可由首条消息自动生成）",
    )

    # 关系
    project: Mapped[Project] = relationship(  # noqa: F821
        back_populates="conversations",
        lazy="raise",
    )
    messages: Mapped[list[Message]] = relationship(  # noqa: F821
        back_populates="conversation",
        lazy="raise",
        order_by="Message.created_at",
    )

    def __repr__(self) -> str:
        return f"<Conversation id={self.id!s} project_id={self.project_id!s}>"
