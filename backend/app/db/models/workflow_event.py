"""WorkflowEvent ORM 模型 — SSE 事件事实记录。

对应 DEV_PLAN §6.1 workflow_events 表。
约束：(run_id, sequence) 唯一。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.db.models.workflow_run import WorkflowRun


class WorkflowEvent(Base, UUIDMixin):
    """Workflow 执行过程中产生的事件。

    每条事件是 SSE 推送过的持久化事实记录；
    Redis 丢失后可从此表补发历史事件。
    """

    __tablename__ = "workflow_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_workflow_events_run_sequence"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        index=True,
        comment="所属 Run ID",
    )
    sequence: Mapped[int] = mapped_column(
        Integer,
        comment="事件在 Run 内的递增序号",
    )
    type: Mapped[str] = mapped_column(
        String(50),
        comment="事件类型：node.started / node.completed / artifact.created / run.completed 等",
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        default=None,
        comment="事件负载（含 progress、message、artifact_id 等）",
    )

    # 关系
    run: Mapped[WorkflowRun] = relationship(  # noqa: F821
        back_populates="events",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<WorkflowEvent seq={self.sequence} type={self.type!r}>"
