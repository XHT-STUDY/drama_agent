"""KnowledgeChunk ORM 模型 — 知识检索块。

对应 DEV_PLAN §6.1 knowledge_chunks 表。
使用 pgvector 向量类型存储 embedding。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.db.models.knowledge_document import KnowledgeDocument


class KnowledgeChunk(Base, UUIDMixin):
    """知识文档的检索块。

    每个文档被切分为多个 chunk，
    每个 chunk 对应一个 embedding 向量用于相似性检索。
    """

    __tablename__ = "knowledge_chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        index=True,
        comment="所属知识文档 ID",
    )
    content: Mapped[str] = mapped_column(
        Text,
        default="",
        comment="Chunk 文本内容",
    )
    embedding: Mapped[Any] = mapped_column(
        Vector(1536),  # 维度默认为 OpenAI text-embedding-3-small 的 1536
        nullable=True,  # 与 0001 迁移一致：摄取先置空，向量化阶段（D-03）再回填
        comment="向量嵌入（pgvector 类型）",
    )
    chunk_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        default=None,
        name="metadata",
        comment="Chunk 元数据（页码、段落、来源等）",
    )
    chunk_index: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="Chunk 在文档内的序号",
    )

    # 关系
    document: Mapped[KnowledgeDocument] = relationship(  # noqa: F821
        back_populates="chunks",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<KnowledgeChunk id={self.id!s} doc_id={self.document_id!s} idx={self.chunk_index}>"
