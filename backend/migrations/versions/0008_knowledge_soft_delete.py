"""知识库软删除：knowledge_documents.deleted_at。

K-2：知识库管理 API 的 DELETE 采用软删（与 projects/conversations 一致），
检索与列表自动排除已删除文档；全局语料不可经项目端点删除。

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="软删除时间（NULL = 未删除）",
        ),
    )
    op.create_index(
        "ix_knowledge_documents_deleted_at",
        "knowledge_documents",
        ["deleted_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_documents_deleted_at", table_name="knowledge_documents"
    )
    op.drop_column("knowledge_documents", "deleted_at")
