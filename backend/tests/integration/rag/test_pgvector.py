"""D-03 pgvector 相似度检索集成测试。

覆盖：
- 摄取 → 回填向量 → cosine 相似度 top-k 检索（pgvector <=>，走 HNSW 索引）；
- 结果排序 / 元数据（标题、分类）/ 阈值过滤 / top_k 截断；
- 未回填向量的块不进入检索结果；
- 向量回填数量不一致时报错。

依赖真实 PostgreSQL + pgvector（CI 环境提供）。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import yaml

from app.db.repositories.knowledge import KnowledgeRepository
from app.rag.chunker import chunk_document
from app.rag.embedder import FakeEmbedder
from app.rag.loader import load_knowledge_file


def _write_doc(
    tmp_path: Path,
    name: str,
    *,
    category: str,
    title: str,
    body: str,
    genre: str = "都市",
    stage: str = "writer",
) -> Path:
    """写一篇带 frontmatter 的知识文档。"""
    meta = {
        "category": category,
        "title": title,
        "source": "drama-agent-self-auth",
        "license": "MIT",
        "language": "zh",
        "genre": genre,
        "stage": stage,
        "tags": ["测试"],
        "version": "1.0.0",
    }
    fm = yaml.dump(meta, allow_unicode=True, sort_keys=False)
    path = tmp_path / name
    path.write_text(f"---\n{fm}---\n{body}\n", encoding="utf-8")
    return path


async def _ingest_with_embeddings(
    test_session,
    tmp_path: Path,
    *,
    category: str,
    title: str,
    body: str,
    genre: str = "都市",
    stage: str = "writer",
) -> tuple[KnowledgeRepository, uuid.UUID]:
    """摄取一篇文档并回填向量，返回 (repo, document_id)。"""
    repo = KnowledgeRepository(test_session)
    loaded = load_knowledge_file(
        _write_doc(
            tmp_path,
            f"{title}.md",
            category=category,
            title=title,
            body=body,
            genre=genre,
            stage=stage,
        )
    )
    chunks = chunk_document(loaded.content)
    doc, _created, _changed = await repo.ingest_document(
        loaded, chunks, corpus_version="mvp_v1"
    )
    emb = FakeEmbedder()
    vectors = [await emb.embed_one(c.content) for c in chunks]
    await repo.backfill_document_embeddings(doc.id, vectors)
    return repo, doc.id


@pytest.mark.integration
class TestPgvectorSearch:
    """相似度检索行为。"""

    @pytest.mark.asyncio
    async def test_search_returns_ranked_hits_with_metadata(
        self, test_session, tmp_path: Path
    ) -> None:
        """检索返回 top-k 命中，含相似度与来源元数据，按相似度降序。"""
        repo, _doc_id = await _ingest_with_embeddings(
            test_session,
            tmp_path,
            category="payoff",
            title="爽点",
            body="# 主线\n主角逆袭的打脸爽点结构。\n# 支线\n配角成长线。",
        )

        emb = FakeEmbedder()
        query = await emb.embed_one("打脸爽点")
        hits = await repo.search_similar(query, top_k=5)

        assert len(hits) <= 5
        assert hits, "至少命中一个块"
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)
        for hit in hits:
            assert -1.0 <= hit.score <= 1.0
            assert hit.title == "爽点"
            assert hit.category == "payoff"
            assert hit.content
            assert hit.chunk_id

    @pytest.mark.asyncio
    async def test_category_filter_limits_documents(
        self, test_session, tmp_path: Path
    ) -> None:
        """category 过滤只返回指定分类的命中。"""
        repo, _doc_id = await _ingest_with_embeddings(
            test_session, tmp_path, category="payoff", title="爽点", body="# A\n爽点正文。"
        )
        await _ingest_with_embeddings(
            test_session,
            tmp_path,
            category="compliance",
            title="合规",
            body="# B\n合规红线说明。",
        )

        emb = FakeEmbedder()
        query = await emb.embed_one("检索")
        hits = await repo.search_similar(query, top_k=10, category="payoff")
        assert hits
        assert all(h.category == "payoff" for h in hits)

    @pytest.mark.asyncio
    async def test_min_score_filters_low_similarity(
        self, test_session, tmp_path: Path
    ) -> None:
        """min_score 过滤低相似度命中。"""
        repo, _doc_id = await _ingest_with_embeddings(
            test_session,
            tmp_path,
            category="payoff",
            title="爽点",
            body="# 主线\n打脸逆袭结构。",
        )
        emb = FakeEmbedder()
        query = await emb.embed_one("检索")
        # 伪随机单位向量近似正交，相似度极低 → 高阈值必然为空
        strict = await repo.search_similar(query, top_k=5, min_score=0.99)
        assert strict == []

    @pytest.mark.asyncio
    async def test_top_k_respected(self, test_session, tmp_path: Path) -> None:
        """top_k 截断命中数。"""
        repo, _doc_id = await _ingest_with_embeddings(
            test_session,
            tmp_path,
            category="payoff",
            title="爽点",
            body="# 一\n第一段。\n# 二\n第二段。\n# 三\n第三段。\n# 四\n第四段。",
        )
        emb = FakeEmbedder()
        query = await emb.embed_one("检索")
        hits = await repo.search_similar(query, top_k=2)
        assert len(hits) == 2

    @pytest.mark.asyncio
    async def test_genre_filter_limits_documents(
        self, test_session, tmp_path: Path
    ) -> None:
        """genre 过滤只返回指定题材的命中。"""
        repo, _doc_id = await _ingest_with_embeddings(
            test_session,
            tmp_path,
            category="payoff",
            title="都市爽点",
            body="# A\n都市爽点正文。",
            genre="都市",
        )
        await _ingest_with_embeddings(
            test_session,
            tmp_path,
            category="payoff",
            title="战神爽点",
            body="# B\n战神逆袭正文。",
            genre="战神",
        )

        emb = FakeEmbedder()
        query = await emb.embed_one("检索")
        hits = await repo.search_similar(query, top_k=10, genre="战神")
        assert hits
        assert all(h.title == "战神爽点" for h in hits)

    @pytest.mark.asyncio
    async def test_stage_filter_limits_documents(
        self, test_session, tmp_path: Path
    ) -> None:
        """stage 过滤只返回指定创作阶段的命中。"""
        repo, _doc_id = await _ingest_with_embeddings(
            test_session,
            tmp_path,
            category="payoff",
            title="写作向",
            body="# A\n写作阶段内容。",
            stage="writer",
        )
        await _ingest_with_embeddings(
            test_session,
            tmp_path,
            category="payoff",
            title="大纲向",
            body="# B\n大纲阶段内容。",
            stage="outline",
        )

        emb = FakeEmbedder()
        query = await emb.embed_one("检索")
        hits = await repo.search_similar(query, top_k=10, stage="outline")
        assert hits
        assert all(h.title == "大纲向" for h in hits)

    @pytest.mark.asyncio
    async def test_unembedded_chunks_excluded(
        self, test_session, tmp_path: Path
    ) -> None:
        """未回填向量的块不进入检索结果。"""
        repo = KnowledgeRepository(test_session)
        loaded = load_knowledge_file(
            _write_doc(
                tmp_path, "未向量化.md", category="payoff", title="未向量化", body="# 甲\n内容。"
            )
        )
        chunks = chunk_document(loaded.content)
        doc, _c, _ch = await repo.ingest_document(
            loaded, chunks, corpus_version="mvp_v1"
        )
        # 故意不回填 embedding
        emb = FakeEmbedder()
        query = await emb.embed_one("检索")
        hits = await repo.search_similar(query, top_k=5)
        assert all(h.title != "未向量化" for h in hits)


@pytest.mark.integration
class TestEmbeddingBackfill:
    """向量回填行为。"""

    @pytest.mark.asyncio
    async def test_backfill_updates_all_chunks(self, test_session, tmp_path: Path) -> None:
        """回填后所有 chunk 都有向量。"""
        repo, doc_id = await _ingest_with_embeddings(
            test_session, tmp_path, category="payoff", title="爽点", body="# 一\n第一段。\n# 二\n第二段。"
        )
        chunks = await repo.list_chunks_by_document(doc_id)
        assert len(chunks) == 2
        assert all(c.embedding is not None for c in chunks)

    @pytest.mark.asyncio
    async def test_backfill_count_mismatch_raises(self, test_session, tmp_path: Path) -> None:
        """向量数与块数不一致时报错。"""
        repo = KnowledgeRepository(test_session)
        loaded = load_knowledge_file(
            _write_doc(tmp_path, "错配.md", category="payoff", title="错配", body="# 甲\n内容。")
        )
        chunks = chunk_document(loaded.content)
        doc, _c, _ch = await repo.ingest_document(
            loaded, chunks, corpus_version="mvp_v1"
        )
        with pytest.raises(ValueError):
            await repo.backfill_document_embeddings(doc.id, [[0.0] * 1536] * 3)

    @pytest.mark.asyncio
    async def test_update_chunk_embedding(self, test_session, tmp_path: Path) -> None:
        """单个 chunk 向量可更新。"""
        repo, doc_id = await _ingest_with_embeddings(
            test_session, tmp_path, category="payoff", title="爽点", body="# 甲\n内容。"
        )
        chunk = (await repo.list_chunks_by_document(doc_id))[0]
        new_vec = [1.0] * 1536
        assert await repo.update_chunk_embedding(chunk.id, new_vec)
        await test_session.flush()

        refreshed = (await repo.list_chunks_by_document(doc_id))[0]
        assert refreshed.embedding is not None
        assert refreshed.embedding[0] == 1.0
