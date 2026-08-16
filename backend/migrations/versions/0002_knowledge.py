"""0002 知识库元数据列与向量索引。

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-16

为 knowledge_documents 增加 D-01 治理元数据列（source/language/genre/stage/tags/
version/corpus_version/document_hash），并为 knowledge_chunks.embedding 建立
pgvector HNSW（cosine）向量索引（0001 只有普通 document_id 索引）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加知识库元数据列与向量索引。"""
    # ---- knowledge_documents 元数据列（与 app/rag/models.py KnowledgeDocMetadata 对齐） ----
    op.add_column(
        "knowledge_documents",
        sa.Column("source", sa.String(500), server_default="", nullable=False, comment="内容来源（合规必填）"),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("language", sa.String(16), server_default="zh", nullable=False, comment="语言代码"),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("genre", sa.String(100), server_default="", nullable=False, comment="题材"),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("stage", sa.String(100), server_default="", nullable=False, comment="适用创作阶段"),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("tags", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False, comment="检索标签"),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("version", sa.String(20), server_default="1.0.0", nullable=False, comment="文档版本"),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("corpus_version", sa.String(50), server_default="", nullable=False, comment="所属语料版本"),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("document_hash", sa.String(64), server_default="", nullable=False, comment="文档内容 SHA256（幂等摄取依据）"),
    )

    # ---- 检索常用过滤列索引 ----
    op.create_index("ix_knowledge_documents_category", "knowledge_documents", ["category"])

    # ---- pgvector HNSW（cosine）向量索引：支撑相似度检索 ----
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding_hnsw "
        "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    """撤销元数据列与向量索引。"""
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw")
    op.drop_index("ix_knowledge_documents_category", table_name="knowledge_documents")
    op.drop_column("knowledge_documents", "document_hash")
    op.drop_column("knowledge_documents", "corpus_version")
    op.drop_column("knowledge_documents", "version")
    op.drop_column("knowledge_documents", "tags")
    op.drop_column("knowledge_documents", "stage")
    op.drop_column("knowledge_documents", "genre")
    op.drop_column("knowledge_documents", "language")
    op.drop_column("knowledge_documents", "source")
