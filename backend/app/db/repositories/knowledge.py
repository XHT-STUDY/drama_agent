"""知识库 Repository（D-02/D-03）。

知识库是全局数据（无 project_id），按 category/genre/stage 过滤。
提供：
- 幂等摄取（相同 document_hash 跳过、变更只重建变化 chunk、删除源文件不物理删除线上记录）；
- 文档/块计数（CLI status）；
- 向量写入（D-03 回填 embedding）；
- 相似度检索（D-03 扩展，走 pgvector HNSW 索引）。

模块边界：只做数据持久化，不做检索业务逻辑（Retriever 在 app/rag/retriever.py）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge_chunk import KnowledgeChunk
from app.db.models.knowledge_document import KnowledgeDocument
from app.db.repositories.base import BaseRepository
from app.rag.chunker import KnowledgeChunk as ChunkInput
from app.rag.loader import LoadedKnowledgeDoc


@dataclass(frozen=True)
class KnowledgeSearchHit:
    """一次相似度检索的命中（chunk + 相似度 + 来源文档标题）。"""

    chunk_id: uuid.UUID
    content: str
    score: float
    title: str
    category: str
    chunk_index: int


class KnowledgeRepository(BaseRepository):
    """知识文档/块的存取与幂等摄取。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository（绑定 KnowledgeDocument 模型）。"""
        super().__init__(session, KnowledgeDocument)

    # ---- 查询 ----

    async def get_by_hash(self, document_hash: str) -> KnowledgeDocument | None:
        """按 document_hash 查找（幂等判定）。"""
        stmt = select(KnowledgeDocument).where(
            KnowledgeDocument.document_hash == document_hash
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_category_title(
        self, category: str, title: str
    ) -> KnowledgeDocument | None:
        """按 (category, title) 查找（变更重建的定位依据）。"""
        stmt = select(KnowledgeDocument).where(
            KnowledgeDocument.category == category,
            KnowledgeDocument.title == title,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_chunks_by_document(
        self, document_id: uuid.UUID
    ) -> list[KnowledgeChunk]:
        """按文档列出全部 chunk（按序号排序）。"""
        stmt = (
            select(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == document_id)
            .order_by(KnowledgeChunk.chunk_index)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_documents(self, corpus_version: str | None = None) -> int:
        """统计文档数（可按语料版本过滤）。"""
        if corpus_version:
            return await self.count(corpus_version=corpus_version)
        return await self.count()

    async def count_chunks(self) -> int:
        """统计 chunk 总数。"""
        from sqlalchemy.sql import func

        stmt = select(func.count()).select_from(KnowledgeChunk)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ---- 摄取 ----

    async def ingest_document(
        self,
        loaded: LoadedKnowledgeDoc,
        chunks: list[ChunkInput],
        *,
        corpus_version: str,
    ) -> tuple[KnowledgeDocument, bool, bool]:
        """幂等摄取一篇文档。

        Returns:
            (document, created, changed)：
            - created=True, changed=True：新建文档；
            - created=False, changed=True：更新元数据并重建变化的块；
            - created=False, changed=False：跳过（内容未变化）。

        规则：
        - document_hash 相同 → 跳过（内容未变化）；
        - (category, title) 相同但 hash 不同 → 更新元数据，按 chunk_hash 只重建变化的块；
        - 否则 → 新建文档 + 全部块。
        """
        existing_by_hash = await self.get_by_hash(loaded.document_hash)
        if existing_by_hash is not None:
            return existing_by_hash, False, False

        existing = await self.get_by_category_title(
            loaded.metadata.category.value, loaded.metadata.title
        )
        if existing is not None:
            self._apply_metadata(existing, loaded, corpus_version)
            await self._rebuild_changed_chunks(existing, chunks)
            await self.session.flush()
            return existing, False, True

        doc = KnowledgeDocument(
            category=loaded.metadata.category.value,
            title=loaded.metadata.title,
            license=loaded.metadata.license,
            content=loaded.content,
        )
        self._apply_metadata(doc, loaded, corpus_version)
        await self.add(doc)
        await self._insert_chunks(doc.id, chunks)
        return doc, True, True

    def _apply_metadata(
        self,
        doc: KnowledgeDocument,
        loaded: LoadedKnowledgeDoc,
        corpus_version: str,
    ) -> None:
        """把加载文档的元数据写入 ORM 文档行。"""
        meta = loaded.metadata
        doc.source = meta.source
        doc.language = meta.language
        doc.genre = meta.genre
        doc.stage = meta.stage
        doc.tags = list(meta.tags)
        doc.version = meta.version
        doc.corpus_version = corpus_version
        doc.document_hash = loaded.document_hash
        doc.content = loaded.content

    async def _insert_chunks(
        self, document_id: uuid.UUID, chunks: list[ChunkInput]
    ) -> None:
        """为文档插入全部 chunk（embedding 置空，由 D-03 向量化阶段回填）。"""
        for chunk in chunks:
            self.session.add(
                KnowledgeChunk(
                    document_id=document_id,
                    content=chunk.content,
                    chunk_metadata=chunk.to_metadata(),
                    chunk_index=chunk.index,
                )
            )

    async def _rebuild_changed_chunks(
        self, doc: KnowledgeDocument, chunks: list[ChunkInput]
    ) -> None:
        """按 chunk_hash 只重建变化的块，保留未变化块的 embedding。

        规则：保留 hash 相同的旧块（embedding 不丢），
        删除已消失的块，插入新增/变化的块。
        """
        existing = await self.list_chunks_by_document(doc.id)
        old_by_hash: dict[str, KnowledgeChunk] = {}
        for chunk in existing:
            meta = chunk.chunk_metadata or {}
            hash_value = meta.get("chunk_hash")
            if hash_value:
                old_by_hash[hash_value] = chunk

        new_hashes = {c.chunk_hash for c in chunks}
        for hash_value, chunk in old_by_hash.items():
            if hash_value not in new_hashes:
                await self.session.execute(
                    delete(KnowledgeChunk).where(KnowledgeChunk.id == chunk.id)
                )

        for new_chunk in chunks:
            if new_chunk.chunk_hash in old_by_hash:
                continue  # 未变化，保留（含已回填的 embedding）
            self.session.add(
                KnowledgeChunk(
                    document_id=doc.id,
                    content=new_chunk.content,
                    chunk_metadata=new_chunk.to_metadata(),
                    chunk_index=new_chunk.index,
                )
            )

    # ---- 向量写入与相似度检索（D-03） ----

    async def update_chunk_embedding(
        self, chunk_id: uuid.UUID, embedding: list[float]
    ) -> bool:
        """更新单个 chunk 的向量（D-03 回填），返回该块是否存在。"""
        chunk = await self.session.get(KnowledgeChunk, chunk_id)
        if chunk is None:
            return False
        chunk.embedding = embedding
        return True

    async def backfill_document_embeddings(
        self, document_id: uuid.UUID, vectors: list[list[float]]
    ) -> int:
        """按 chunk 序号顺序回填文档全部块的向量，返回更新的块数。

        向量顺序与 list_chunks_by_document 的返回顺序一致（按 chunk_index 升序）。
        """
        chunks = await self.list_chunks_by_document(document_id)
        if len(chunks) != len(vectors):
            raise ValueError(
                f"向量数 {len(vectors)} 与块数 {len(chunks)} 不一致"
            )
        for chunk, vec in zip(chunks, vectors, strict=True):
            chunk.embedding = vec
        await self.session.flush()
        return len(chunks)

    async def search_similar(
        self,
        query_vector: list[float],
        top_k: int,
        *,
        category: str | None = None,
        genre: str | None = None,
        stage: str | None = None,
        min_score: float | None = None,
    ) -> list[KnowledgeSearchHit]:
        """按 cosine 相似度检索 chunk（pgvector <=>，走 0002 的 HNSW 索引）。

        Args:
            query_vector: 查询向量。
            top_k: 返回的最大命中数。
            category: 限定文档分类（None 表示全库）。
            genre: 限定题材（None 表示全题材）。
            stage: 限定适用创作阶段（None 表示全部）。
            min_score: 最低相似度阈值（None 表示不限制）。

        Returns:
            按相似度降序、同分按块序号升序的稳定命中列表（含来源文档标题/分类）。
        """
        distance = KnowledgeChunk.embedding.cosine_distance(query_vector)
        score_expr = (1 - distance).label("score")
        stmt = (
            select(
                KnowledgeChunk.id,
                KnowledgeChunk.content,
                KnowledgeChunk.chunk_index,
                KnowledgeDocument.title,
                KnowledgeDocument.category,
                score_expr,
            )
            .join(
                KnowledgeDocument,
                KnowledgeDocument.id == KnowledgeChunk.document_id,
            )
            .where(KnowledgeChunk.embedding.is_not(None))
        )
        if category:
            stmt = stmt.where(KnowledgeDocument.category == category)
        if genre:
            stmt = stmt.where(KnowledgeDocument.genre == genre)
        if stage:
            stmt = stmt.where(KnowledgeDocument.stage == stage)
        if min_score is not None:
            stmt = stmt.where(score_expr >= min_score)
        stmt = stmt.order_by(score_expr.desc(), KnowledgeChunk.chunk_index).limit(
            top_k
        )

        result = await self.session.execute(stmt)
        return [
            KnowledgeSearchHit(
                chunk_id=row[0],
                content=row[1],
                chunk_index=row[2],
                title=row[3],
                category=row[4],
                score=float(row[5]),
            )
            for row in result.all()
        ]
