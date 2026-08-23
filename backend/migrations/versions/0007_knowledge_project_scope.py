"""知识库项目级作用域：knowledge_documents.project_id。

K-1：上传参考资料（import route=hold）摄取入 knowledge corpus 时绑定项目；
全局语料（knowledge/ 目录摄取）project_id 为 NULL，对所有项目可见。
检索按 `(project_id = :project 或 IS NULL)` 过滤，项目间互不可见。

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "project_id",
            postgresql.UUID(),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=True,
            comment="所属项目（NULL = 全局语料，对所有项目可见）",
        ),
    )
    op.create_index(
        "ix_knowledge_documents_project_id",
        "knowledge_documents",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_documents_project_id", table_name="knowledge_documents"
    )
    op.drop_column("knowledge_documents", "project_id")
