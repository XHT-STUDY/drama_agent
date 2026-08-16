"""检索领域模型（D-04）。

定义检索的结果 / 轨迹与降级检索器：
- RetrievedChunk：单条命中（含短 ID `slug-<n>` 与来源标题，供 Prompt 引用）；
- RetrievalResult：一次检索的完整结果（查询 + 命中 + 配置元数据）；
- RetrievalTrace：检索轨迹（query / chunk IDs / scores / filters / corpus_version），
  持久化为 Artifact 供追溯——不含全文，只记录"检索了什么、命中了什么"；
- NullRetriever：降级检索器，始终返回空结果（删除 RAG 后主流程仍可运行）。

模块边界：纯领域类型，不依赖 rag/ 实现（rag/retriever.py 依赖本模块）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetrievedChunk:
    """一次检索命中的知识块。

    id 为短引用 ID（如 slug-1），用于在 Prompt 中引用片段；
    chunk_id 为库内唯一 ID，用于 RetrievalTrace 追溯。
    """

    id: str
    chunk_id: uuid.UUID
    content: str
    score: float
    title: str
    category: str


@dataclass(frozen=True)
class RetrievalResult:
    """一次检索的结果。

    filters 记录实际生效的过滤条件（category/genre/stage），
    corpus_version 记录命中所属的语料版本（追溯检索依据）。
    """

    query: str
    chunks: list[RetrievedChunk] = field(default_factory=list)
    top_k: int = 5
    min_score: float = 0.0
    filters: dict[str, str] = field(default_factory=dict)
    corpus_version: str = ""

    def to_trace(self) -> RetrievalTrace:
        """派生检索轨迹（不含全文，只含 ID 与分数）。"""
        return RetrievalTrace(
            query=self.query,
            chunk_ids=[str(c.chunk_id) for c in self.chunks],
            scores=[c.score for c in self.chunks],
            filters=self.filters,
            corpus_version=self.corpus_version,
            top_k=self.top_k,
        )


@dataclass(frozen=True)
class RetrievalTrace:
    """检索轨迹（持久化为 Artifact）。

    记录查询、命中的 chunk ID 与分数、过滤条件、语料版本，
    用于 Exit Gate 4 的"可追溯 corpus_version + chunk IDs"验收。
    """

    query: str
    chunk_ids: list[str]
    scores: list[float]
    filters: dict[str, str] = field(default_factory=dict)
    corpus_version: str = ""
    top_k: int = 5

    def model_dump(self) -> dict[str, object]:
        """转换为可 JSON 序列化 dict（写入 Artifact content）。"""
        return {
            "query": self.query,
            "chunk_ids": self.chunk_ids,
            "scores": self.scores,
            "filters": self.filters,
            "corpus_version": self.corpus_version,
            "top_k": self.top_k,
        }


class NullRetriever:
    """降级检索器：始终返回空结果，不访问数据库与网络。

    保证"删除 RAG 后主流程仍可运行"——创作节点拿到空 rag_context 回退到原有兜底。
    """

    def __init__(self, corpus_version: str = "") -> None:
        """初始化降级检索器。

        Args:
            corpus_version: 语料版本标识（降级模式下无实际检索，默认空）。
        """
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
        """返回空检索结果（签名与 Retriever 一致，保证可替换）。"""
        filters: dict[str, str] = {}
        if category:
            filters["category"] = category
        if genre:
            filters["genre"] = genre
        if stage:
            filters["stage"] = stage
        return RetrievalResult(
            query=query,
            chunks=[],
            top_k=top_k,
            min_score=min_score,
            filters=filters,
            corpus_version=self._corpus_version,
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
        """返回空检索结果（签名与 Retriever.retrieve_for_stage 一致，保证可替换）。

        降级模式下不访问数据库与网络，filters 仅记录阶段。
        """
        return RetrievalResult(
            query=query,
            chunks=[],
            top_k=top_k,
            min_score=min_score,
            filters={"stage": stage},
            corpus_version=self._corpus_version,
        )
