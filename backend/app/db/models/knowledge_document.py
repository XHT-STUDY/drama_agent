"""KnowledgeDocument ORM 模型 — 知识文档。

对应 DEV_PLAN §6.1 knowledge_documents 表。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Text
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

    # 关系
    chunks: Mapped[list[KnowledgeChunk]] = relationship(  # noqa: F821
        back_populates="document",
        lazy="raise",
        order_by="KnowledgeChunk.chunk_index",
    )

    def __repr__(self) -> str:
        return f"<KnowledgeDocument id={self.id!s} title={self.title!r}>"
