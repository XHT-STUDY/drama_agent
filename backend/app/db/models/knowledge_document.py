"""KnowledgeDocument ORM 模型 — 知识文档。

对应 DEV_PLAN §6.1 knowledge_documents 表。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.db.models.knowledge_chunk import KnowledgeChunk


class KnowledgeDocument(Base, UUIDMixin):
    """知识库文档。

    存储创作参考材料、规则文档等知识内容，
    每个文档可被切分为多个 chunk 进行向量检索。
    """

    __tablename__ = "knowledge_documents"

    category: Mapped[str] = mapped_column(
        String(100),
        default="",
        comment="文档分类：rule / example / reference / template",
    )
    title: Mapped[str] = mapped_column(
        String(300),
        default="",
        comment="文档标题",
    )
    license: Mapped[str] = mapped_column(
        String(100),
        default="",
        comment="文档许可证",
    )
    content: Mapped[str] = mapped_column(
        Text,
        default="",
        comment="文档原始全文",
    )

    # ---- D-01 元数据列（与 app/rag/models.py KnowledgeDocMetadata 对齐） ----
    source: Mapped[str] = mapped_column(
        String(500),
        default="",
        comment="内容来源（合规必填）",
    )
    language: Mapped[str] = mapped_column(
        String(16),
        default="zh",
        comment="语言代码",
    )
    genre: Mapped[str] = mapped_column(
        String(100),
        default="",
        comment="题材（如 都市/战神/赘婿）",
    )
    stage: Mapped[str] = mapped_column(
        String(100),
        default="",
        comment="适用创作阶段（story_bible/outline/writer）",
    )
    tags: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        comment="检索标签",
    )
    version: Mapped[str] = mapped_column(
        String(20),
        default="1.0.0",
        comment="文档版本",
    )
    corpus_version: Mapped[str] = mapped_column(
        String(50),
        default="",
        comment="所属语料版本",
    )
    document_hash: Mapped[str] = mapped_column(
        String(64),
        default="",
        comment="文档内容 SHA256（幂等摄取依据）",
    )

    # 关系
    chunks: Mapped[list[KnowledgeChunk]] = relationship(  # noqa: F821
        back_populates="document",
        lazy="raise",
        order_by="KnowledgeChunk.chunk_index",
    )

    def __repr__(self) -> str:
        return f"<KnowledgeDocument id={self.id!s} title={self.title!r}>"
