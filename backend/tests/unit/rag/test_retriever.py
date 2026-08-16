"""D-04 Retriever / RetrievalTrace 单元测试。

用 FakeEmbedder + 脚本化假 Repository 测试检索后处理逻辑，
不访问数据库与网络。golden query 夹具同时校验结构合法。

覆盖：
- category 过滤传递（StoryBible 不取 rubric 类 / Evaluator 可限定 rubric）；
- 无结果返回空列表而非异常；
- RetrievalTrace 不含全文（无 content 字段）；
- 去重与稳定排序；
- 每文档最大块数上限与短 ID 序号。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import cast

import pytest

from app.db.repositories.knowledge import KnowledgeRepository, KnowledgeSearchHit
from app.domain.retrieval import NullRetriever, RetrievalResult
from app.rag.embedder import FakeEmbedder
from app.rag.retriever import Retriever

_GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"


def _hit(
    *,
    title: str,
    category: str,
    score: float,
    content: str = "内容",
    chunk_id: str | None = None,
) -> KnowledgeSearchHit:
    """构造一个脚本化命中。"""
    return KnowledgeSearchHit(
        chunk_id=uuid.uuid4() if chunk_id is None else uuid.UUID(chunk_id),
        content=content,
        score=score,
        title=title,
        category=category,
        chunk_index=0,
    )


class _FakeRepo:
    """脚本化假 Repository：实现 search_similar（按 category/min_score 过滤）。"""

    def __init__(self, hits: list[KnowledgeSearchHit]) -> None:
        self._hits = hits

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
        result = list(self._hits)
        if category:
            result = [h for h in result if h.category == category]
        if min_score is not None:
            result = [h for h in result if h.score >= min_score]
        return result[:top_k]


def _make_retriever(hits: list[KnowledgeSearchHit]) -> Retriever:
    return Retriever(
        cast(KnowledgeRepository, _FakeRepo(hits)),
        FakeEmbedder(),
        corpus_version="mvp_v1",
    )


class TestRetrieverFiltering:
    """过滤条件传递。"""

    @pytest.mark.asyncio
    async def test_category_filter_only_requested(self) -> None:
        """category 过滤只返回指定分类（StoryBible 场景）。"""
        retriever = _make_retriever(
            [
                _hit(title="爽点", category="payoff", score=0.9),
                _hit(title="战神模板", category="genre_template", score=0.8),
            ]
        )
        result = await retriever.retrieve("战神题材", category="genre_template")
        assert [c.category for c in result.chunks] == ["genre_template"]
        assert result.filters == {"category": "genre_template"}

    @pytest.mark.asyncio
    async def test_rubric_category_returns_empty_when_not_in_corpus(self) -> None:
        """限定 rubric 类但语料无 rubric 文档时返回空（Evaluator 可安全限定）。"""
        retriever = _make_retriever(
            [
                _hit(title="爽点", category="payoff", score=0.9),
                _hit(title="钩子", category="opening_hook", score=0.8),
            ]
        )
        result = await retriever.retrieve("评分标准", category="rubric")
        assert result.chunks == []

    @pytest.mark.asyncio
    async def test_no_results_returns_empty_not_exception(self) -> None:
        """无命中返回空列表而非抛异常。"""
        retriever = _make_retriever([])
        result = await retriever.retrieve("不存在的题材")
        assert isinstance(result, RetrievalResult)
        assert result.chunks == []


class TestRetrieverPostProcessing:
    """检索后处理：去重 / 排序 / 上限 / 短 ID。"""

    @pytest.mark.asyncio
    async def test_dedup_removes_duplicate_chunk_ids(self) -> None:
        """重复 chunk_id 去重（防御性）。"""
        dup_id = str(uuid.uuid4())
        retriever = _make_retriever(
            [
                _hit(title="A", category="payoff", score=0.9, chunk_id=dup_id),
                _hit(title="A", category="payoff", score=0.8, chunk_id=dup_id),
                _hit(title="B", category="payoff", score=0.7),
            ]
        )
        result = await retriever.retrieve("检索")
        assert len(result.chunks) == 2

    @pytest.mark.asyncio
    async def test_preserves_repo_order_stable(self) -> None:
        """按 repo 返回顺序稳定生成结果（repo 已按相似度降序）。"""
        retriever = _make_retriever(
            [
                _hit(title="A", category="payoff", score=0.9),
                _hit(title="B", category="payoff", score=0.5),
                _hit(title="C", category="payoff", score=0.3),
            ]
        )
        result = await retriever.retrieve("检索")
        assert [c.title for c in result.chunks] == ["A", "B", "C"]
        assert [c.score for c in result.chunks] == [0.9, 0.5, 0.3]

    @pytest.mark.asyncio
    async def test_slug_ids_sequential(self) -> None:
        """短 ID 为 slug-1, slug-2, ... 连续编号。"""
        retriever = _make_retriever(
            [
                _hit(title="A", category="payoff", score=0.9),
                _hit(title="B", category="payoff", score=0.5),
                _hit(title="C", category="payoff", score=0.3),
            ]
        )
        result = await retriever.retrieve("检索")
        assert [c.id for c in result.chunks] == ["slug-1", "slug-2", "slug-3"]

    @pytest.mark.asyncio
    async def test_max_chunks_per_document_caps(self) -> None:
        """每文档最大块数生效。"""
        retriever = _make_retriever(
            [
                _hit(title="A", category="payoff", score=0.9),
                _hit(title="A", category="payoff", score=0.8),
                _hit(title="A", category="payoff", score=0.7),
                _hit(title="B", category="payoff", score=0.6),
            ]
        )
        result = await retriever.retrieve("检索", max_chunks_per_document=2)
        titles = [c.title for c in result.chunks]
        assert titles.count("A") == 2
        assert titles.count("B") == 1


class TestRetrievalTrace:
    """轨迹派生。"""

    @pytest.mark.asyncio
    async def test_trace_excludes_content(self) -> None:
        """RetrievalTrace 不含全文（无 content 字段），含 chunk IDs/scores。"""
        retriever = _make_retriever(
            [
                _hit(title="A", category="payoff", score=0.9),
                _hit(title="B", category="payoff", score=0.5),
            ]
        )
        result = await retriever.retrieve("检索")
        trace = result.to_trace()
        dumped = trace.model_dump()
        assert "content" not in dumped, "Trace 不应包含全文"
        assert len(trace.chunk_ids) == 2
        assert trace.scores == [0.9, 0.5]
        assert trace.corpus_version == "mvp_v1"

    @pytest.mark.asyncio
    async def test_trace_chunk_ids_match_chunks(self) -> None:
        """Trace 的 chunk IDs 与结果命中一一对应。"""
        retriever = _make_retriever(
            [_hit(title="A", category="payoff", score=0.9)]
        )
        result = await retriever.retrieve("检索")
        trace = result.to_trace()
        assert trace.chunk_ids == [str(c.chunk_id) for c in result.chunks]


class TestRetrieverForStage:
    """按创作阶段检索（D-05 阶段 → 分类映射）。"""

    def _make_corpus(self) -> list[KnowledgeSearchHit]:
        """构造覆盖多分类的语料（每个分类多篇文档）。"""
        hits: list[KnowledgeSearchHit] = []
        for category, score in [
            ("genre_template", 0.95),
            ("genre_template", 0.80),
            ("character_archetype", 0.90),
            ("character_archetype", 0.70),
            ("opening_hook", 0.85),
            ("payoff", 0.75),
            ("compliance", 0.60),
        ]:
            hits.append(
                _hit(title=f"{category}-文档", category=category, score=score)
            )
        return hits

    @pytest.mark.asyncio
    async def test_stage_returns_only_mapped_categories(self) -> None:
        """story_bible 阶段只返回 genre_template + character_archetype 命中。"""
        retriever = _make_retriever(self._make_corpus())
        result = await retriever.retrieve_for_stage("story_bible", "战神题材")
        categories = sorted({c.category for c in result.chunks})
        assert categories == ["character_archetype", "genre_template"]
        # slug ID 连续编号
        assert [c.id for c in result.chunks] == [
            f"slug-{i}" for i in range(1, len(result.chunks) + 1)
        ]

    @pytest.mark.asyncio
    async def test_outline_and_writer_stages_filter_differently(self) -> None:
        """三阶段过滤不同：outline ≠ story_bible ≠ writer（验收"三类节点检索过滤不同"）。"""
        retriever = _make_retriever(self._make_corpus())
        results = {}
        for stage in ("story_bible", "outline", "writer"):
            r = await retriever.retrieve_for_stage(stage, "查询")
            results[stage] = sorted({c.category for c in r.chunks})
        assert results["outline"] == ["genre_template", "opening_hook"]
        assert results["writer"] == ["character_archetype", "payoff"]
        assert results["story_bible"] != results["outline"]
        assert results["outline"] != results["writer"]

    @pytest.mark.asyncio
    async def test_merges_dedups_and_caps_top_k(self) -> None:
        """跨分类合并后去重、按 top_k 截断、按相似度降序。"""
        # 同 chunk_id 同时命中两个分类（防御性去重）
        dup_id = str(uuid.uuid4())
        retriever = _make_retriever(
            [
                _hit(title="模板", category="genre_template", score=0.95, chunk_id=dup_id),
                _hit(title="模板2", category="genre_template", score=0.9),
                _hit(title="模板3", category="genre_template", score=0.85),
                _hit(title="人物", category="character_archetype", score=0.80, chunk_id=dup_id),
                _hit(title="人物2", category="character_archetype", score=0.75),
            ]
        )
        result = await retriever.retrieve_for_stage("story_bible", "查询", top_k=3)
        assert len(result.chunks) == 3  # 去重 + 截断
        scores = [c.score for c in result.chunks]
        assert scores == sorted(scores, reverse=True)
        assert result.filters["stage"] == "story_bible"
        assert "genre_template" in result.filters["categories"]

    @pytest.mark.asyncio
    async def test_unknown_stage_raises(self) -> None:
        """未知阶段抛 ValueError。"""
        retriever = _make_retriever([])
        with pytest.raises(ValueError):
            await retriever.retrieve_for_stage("evaluate", "查询")

    @pytest.mark.asyncio
    async def test_null_retriever_for_stage_returns_empty(self) -> None:
        """NullRetriever.retrieve_for_stage 与 Retriever 可替换（空结果）。"""
        nr = NullRetriever(corpus_version="mvp_v1")
        result = await nr.retrieve_for_stage("writer", "查询", top_k=5)
        assert result.chunks == []
        assert result.top_k == 5
        assert result.filters == {"stage": "writer"}
        assert result.corpus_version == "mvp_v1"


class TestNullRetriever:
    """降级检索器。"""

    @pytest.mark.asyncio
    async def test_returns_empty_result(self) -> None:
        """NullRetriever 返回空结果，不抛异常。"""
        nr = NullRetriever(corpus_version="mvp_v1")
        result = await nr.retrieve("任意查询", category="payoff")
        assert result.chunks == []
        assert result.filters == {"category": "payoff"}

    @pytest.mark.asyncio
    async def test_signature_matches_retriever(self) -> None:
        """NullRetriever 与 Retriever 可替换（同签名）。"""
        nr = NullRetriever()
        result = await nr.retrieve(
            "查询",
            top_k=3,
            category="payoff",
            genre="都市",
            stage="writer",
            min_score=0.2,
            max_chunks_per_document=1,
        )
        assert result.top_k == 3
        assert result.filters == {"category": "payoff", "genre": "都市", "stage": "writer"}


class TestGoldenQueries:
    """golden query 夹具结构。"""

    def test_queries_load_and_have_2_to_3_each(self) -> None:
        """rag_queries.json 可加载，每类 2-3 条，query 非空。"""
        path = _GOLDEN_DIR / "rag_queries.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        for category, queries in data.items():
            assert 2 <= len(queries) <= 3, f"{category} 应有 2-3 条 query"
            assert all(isinstance(q, str) and q.strip() for q in queries)

    def test_expectations_load_with_stages(self) -> None:
        """rag_expectations.json 可加载：三阶段，每阶段有 expected_categories + queries。"""
        path = _GOLDEN_DIR / "rag_expectations.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert set(data.keys()) == {"story_bible", "outline", "writer"}
        for _stage, spec in data.items():
            assert "expected_categories" in spec
            assert "queries" in spec
            assert isinstance(spec["expected_categories"], list)
            assert 1 <= len(spec["expected_categories"]) <= 2
            assert 2 <= len(spec["queries"]) <= 3
            assert all(isinstance(q, str) and q.strip() for q in spec["queries"])
