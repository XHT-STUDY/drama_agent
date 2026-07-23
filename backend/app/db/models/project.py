"""Project ORM 模型 — 项目表。

对应 DEV_PLAN §6.1 projects 表。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, UUIDMixin

if TYPE_CHECKING:
    from app.db.models.conversation import Conversation


class Project(Base, UUIDMixin, SoftDeleteMixin):
    """创作项目。

    一个项目代表一次完整的短剧创作任务，
    包含标题、状态和集数统计。
    """

    __tablename__ = "projects"

    title: Mapped[str] = mapped_column(
        String(200),
        default="",
        comment="项目标题",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="draft",
        comment="项目状态：draft/planning/writing/evaluating/revising/completed/archived",
    )
    target_episode_count: Mapped[int] = mapped_column(
        default=10,
        comment="目标总集数（MVP 默认 10）",
    )
    current_episode_count: Mapped[int] = mapped_column(
        default=0,
        comment="当前已完成集数",
    )

    # 关系（后续阶段连接）
    conversations: Mapped[list[Conversation]] = relationship(  # noqa: F821
        back_populates="project",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<Project id={self.id!s} title={self.title!r}>"
