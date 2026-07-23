"""ArtifactLink ORM 模型 — 资产依赖关系图。

对应 DEV_PLAN §6.1 artifact_links 表。
约束：source_id != target_id（禁止自引用）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin


class ArtifactLink(Base, UUIDMixin):
    """Artifact 之间的依赖关系。

    记录 source → target 的有向边，
    例如：EvaluationReport 依赖 ScriptDraft，
    或 RevisedScript 依赖原始 ScriptDraft。
    """

    __tablename__ = "artifact_links"
    __table_args__ = (
        CheckConstraint(
            "source_id != target_id",
            name="ck_artifact_links_no_self_ref",
        ),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"),
        index=True,
        comment="依赖源 Artifact ID",
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"),
        index=True,
        comment="被依赖 Artifact ID",
    )
    relation: Mapped[str] = mapped_column(
        String(50),
        comment="关系类型：derived_from / evaluates / revises / continues",
    )

    def __repr__(self) -> str:
        return (
            f"<ArtifactLink {self.relation!r} "
            f"source={self.source_id!s} → target={self.target_id!s}>"
        )
