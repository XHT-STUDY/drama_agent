"""WorkflowRun ORM 模型 — 长任务运行记录。

对应 DEV_PLAN §6.1 workflow_runs 表。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.db.models.workflow_event import WorkflowEvent


class WorkflowRun(Base, UUIDMixin):
    """Workflow 执行记录。

    每次用户发起创作/评估/修订等动作时创建一条 Run，
    记录 action、状态和配置快照。
    """

    __tablename__ = "workflow_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"),
        index=True,
        comment="所属项目 ID",
    )
    action: Mapped[str] = mapped_column(
        String(50),
        comment="执行动作：create_script / evaluate / revise / import / export",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="queued",
        comment="运行状态：queued / running / completed / failed / cancelled",
    )
    state_summary: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        default=None,
        comment="LangGraph State 摘要（仅存 ID 和轻量字段，不含大文本）",
    )
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        default=None,
        comment="Run 创建时的配置快照（所有可配置的参数）",
    )

    # 关系
    events: Mapped[list[WorkflowEvent]] = relationship(  # noqa: F821
        back_populates="run",
        lazy="raise",
        order_by="WorkflowEvent.sequence",
    )

    def __repr__(self) -> str:
        return f"<WorkflowRun id={self.id!s} action={self.action!r} status={self.status!r}>"
