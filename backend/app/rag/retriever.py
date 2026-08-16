"""知识检索器（D-04）。

Retriever 是检索业务逻辑的唯一入口：
- 把 query 向量化（复用 Embedder）；
- 调 KnowledgeRepository 的 pgvector 相似度检索；
- 应用检索后处理：去重（防御性）、稳定排序、每文档最大块数、短 ID 分配。

过滤条件（category / genre / stage / min_score）与 D-05 的三阶段映射配合，
同一知识库按创作阶段检索不同分类，而非把全部资料塞进 Prompt。

模块边界：依赖 rag/embedder.py 与 db/repositories/knowledge.py，
结果类型来自 domain/retrieval.py。
"""

from __future__ import annotations

import logging
import time

from pydantic import BaseModel, Field

from app.db.repositories.knowledge import (
    KnowledgeRepository,
    KnowledgeSearchHit,
)
from app.domain.retrieval import RetrievalResult, RetrievedChunk
from app.observability.metrics import rag_retrieval_duration_seconds
from app.rag.embedder import Embedder
from app.rag.models import load_corpus_version

logger = logging.getLogger(__name__)

# 创作阶段 → 检索分类映射 (D-05)
# 每个创作节点只检索与其任务相关的知识分类，避免把全部资料塞进 Prompt：
# - StoryBible → 题材模板 + 人物原型（世界观 / 角色设定参考）；
# - 分集大纲 → 题材模板 + 开篇钩子（集间节奏 / 开场设计参考）；
# - 剧本写作 → 爽点 + 人物原型（对白节奏 / 爽感爆发参考）。
_CREATION_STAGE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "story_bible": ("genre_template", "character_archetype"),
    "outline": ("genre_template", "opening_hook"),
    "writer": ("payoff", "character_archetype"),
}


class RetrieveConfig(BaseModel):
    """一次检索的配置（全部可选，提供默认值）。"""

    top_k: int = Field(5, description="返回的最大命中数", ge=1)
    min_score: float = Field(0.0, description="最低相似度阈值", ge=-1.0, le=1.0)
    max_chunks_per_document: int | None = Field(
        None, description="每文档最大块数（None 不限制）"
    )
    category: str | None = Field(None, description="限定文档分类")
    genre: str | None = Field(None, description="限定题材")
    stage: str | None = Field(None, description="限定适用创作阶段")


class Retriever:
    """把 query 编码为向量并从知识库检索相似块。"""

    def __init__(
        self,
        repository: KnowledgeRepository,
        embedder: Embedder,
        *,
        corpus_version: str | None = None,
    ) -> None:
        """初始化 Retriever。

        Args:
            repository: 知识库 Repository（提供 search_similar）。
            embedder: 向量化器（query 编码）。
            corpus_version: 语料版本；None 时在首次检索时读取 knowledge/VERSION。
        """
        self._repo = repository
        self._embedder = embedder
        self._corpus_version = corpus_version

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        category: str | None = None,
        genre: str | None = None,
        stage: str | None = None,
        min_score: float = 0.0,
        max_chunks_per_document: int | None = None,
    ) -> RetrievalResult:
        """执行一次检索。

        Args:
            query: 检索文本（如归一化后的创作需求 / 阶段意图）。
            top_k / category / genre / stage / min_score / max_chunks_per_document:
                见 RetrieveConfig。

        Returns:
            RetrievalResult（含按相似度降序、稳定排序、短 ID 的命中）。
        """
        # I-02：检索耗时直方图（无论成败都记录）
        _start = time.monotonic()
        try:
            return await self._retrieve_impl(query, top_k=top_k, category=category,
                                             genre=genre, stage=stage, min_score=min_score,
                                             max_chunks_per_document=max_chunks_per_document)
        finally:
            rag_retrieval_duration_seconds.observe(time.monotonic() - _start)

    async def _retrieve_impl(
        self,
        query: str,
        *,
        top_k: int = 5,
        category: str | None = None,
        genre: str | None = None,
        stage: str | None = None,
        min_score: float = 0.0,
        max_chunks_per_document: int | None = None,
    ) -> RetrievalResult:
        """检索实现（被 retrieve 计时包装）。"""
        config = RetrieveConfig(
            top_k=top_k,
            min_score=min_score,
            max_chunks_per_document=max_chunks_per_document,
            category=category,
            genre=genre,
            stage=stage,
        )
        filters = _build_filters(config)

        query_vector = await self._embedder.embed_one(query)
        hits = await self._repo.search_similar(
            query_vector,
            config.top_k,
            category=config.category,
            genre=config.genre,
            stage=config.stage,
            min_score=config.min_score,
        )

        chunks = self._post_process(hits, config)
        corpus_version = self._corpus_version or load_corpus_version()
        return RetrievalResult(
            query=query,
            chunks=chunks,
            top_k=config.top_k,
            min_score=config.min_score,
            filters=filters,
            corpus_version=corpus_version,
        )

    async def retrieve_for_stage(
        self,
        stage: str,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = -1.0,
        max_chunks_per_document: int | None = None,
    ) -> RetrievalResult:
        """按创作阶段检索知识：该阶段对应的每个分类各检索一次后合并。

        阶段 → 分类映射见 _CREATION_STAGE_CATEGORIES。每个分类检索
        top_k 条，合并后按相似度降序、去重、截断到 top_k，并重新分配
        连续短 ID（各分类 slug 编号可能有重叠）。只按 category 过滤，
        不按 stage 元数据过滤——跨阶段语料（如题材模板）可被多个节点复用。

        min_score 默认 -1.0（不做相似度门槛）：阶段检索的语义是"收集
        参考材料"——宁可给 LLM 略不相关的片段，也不因阈值随机丢弃；
        排序本身由相似度决定，LLM 自会忽略无关内容。

        Args:
            stage: 创作阶段（story_bible / outline / writer）。
            query: 检索文本（归一化需求摘要）。
            top_k / min_score / max_chunks_per_document: 见 RetrieveConfig。

        Returns:
            合并后的 RetrievalResult（filters 记录阶段与涉及分类）。

        Raises:
            ValueError: stage 不在 _CREATION_STAGE_CATEGORIES 中。
        """
        categories = _CREATION_STAGE_CATEGORIES.get(stage)
        if categories is None:
            raise ValueError(
                f"未知创作阶段: {stage!r}, 可用: {sorted(_CREATION_STAGE_CATEGORIES)}"
            )

        merged: list[RetrievedChunk] = []
        seen: set[str] = set()
        for category in categories:
            result = await self.retrieve(
                query,
                top_k=top_k,
                category=category,
                min_score=min_score,
                max_chunks_per_document=max_chunks_per_document,
            )
            for chunk in result.chunks:
                key = str(chunk.chunk_id)
                if key in seen:
                    continue  # 防御性去重（同 chunk 可能命中两个分类）
                seen.add(key)
                merged.append(chunk)

        merged.sort(key=lambda c: c.score, reverse=True)
        top = merged[:top_k]
        renumbered = [
            RetrievedChunk(
                id=f"slug-{i + 1}",
                chunk_id=c.chunk_id,
                content=c.content,
                score=c.score,
                title=c.title,
                category=c.category,
            )
            for i, c in enumerate(top)
        ]
        return RetrievalResult(
            query=query,
            chunks=renumbered,
            top_k=top_k,
            min_score=min_score,
            filters={"stage": stage, "categories": ",".join(categories)},
            corpus_version=self._corpus_version or load_corpus_version(),
        )

    def _post_process(
        self, hits: list[KnowledgeSearchHit], config: RetrieveConfig
    ) -> list[RetrievedChunk]:
        """检索后处理：去重 → 每文档最大块数 → 分配短 ID。

        hits 已按相似度降序、块序号升序稳定排序（repo 侧保证），
        这里做防御性去重并应用 per-document 上限。
        """
        result: list[RetrievedChunk] = []
        seen_chunk_ids: set[str] = set()
        doc_counts: dict[str, int] = {}
        for hit in hits:
            chunk_key = str(hit.chunk_id)
            if chunk_key in seen_chunk_ids:
                continue  # 防御性去重（repo 返回理论上唯一）
            seen_chunk_ids.add(chunk_key)

            title_key = hit.title
            if (
                config.max_chunks_per_document is not None
                and doc_counts.get(title_key, 0) >= config.max_chunks_per_document
            ):
                continue  # 超出每文档上限，跳过
            doc_counts[title_key] = doc_counts.get(title_key, 0) + 1

            result.append(
                RetrievedChunk(
                    id=f"slug-{len(result) + 1}",
                    chunk_id=hit.chunk_id,
                    content=hit.content,
                    score=hit.score,
                    title=hit.title,
                    category=hit.category,
                )
            )
        return result


def _build_filters(config: RetrieveConfig) -> dict[str, str]:
    """从配置构造实际生效的过滤条件（用于 RetrievalResult / Trace）。"""
    filters: dict[str, str] = {}
    if config.category:
        filters["category"] = config.category
    if config.genre:
        filters["genre"] = config.genre
    if config.stage:
        filters["stage"] = config.stage
    return filters
