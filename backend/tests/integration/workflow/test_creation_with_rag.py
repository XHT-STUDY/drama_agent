"""D-05 创作链路 RAG 接入集成测试。

覆盖：
- 固定 golden query 的 expected category 命中率 hit@5 ≥ 90%（阶段过滤结构性保证）；
- 三类创作节点检索过滤不同（story_bible / outline / writer 分类集互不相同）；
- 每阶段持久化 RetrievalTrace Artifact（含 stage / chunk IDs / corpus_version）；
- 删除 RAG（注入 NullRetriever）后主流程仍可完整运行。

说明：CI 用 FakeEmbedder（确定性伪向量，近似正交），检索质量退化为结构测试——
阶段分类过滤保证返回块全部来自期望分类，hit@5 由过滤结构保证为 100%；
真实语义质量由真实 Embedder 在手工冒烟中验证。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.application.artifact_service import ArtifactService
from app.application.run_service import RunService
from app.db.repositories.knowledge import KnowledgeRepository
from app.domain.retrieval import NullRetriever
from app.rag.chunker import chunk_document
from app.rag.embedder import FakeEmbedder
from app.rag.loader import load_knowledge_file
from app.rag.retriever import Retriever
from app.workflows.creation import build_creation_workflow
from app.workflows.state import CreationState

_GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"


def _load_golden(name: str) -> dict[str, Any]:
    with open(_GOLDEN_DIR / f"{name}.json", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "expected_output" in data:
        return data["expected_output"]
    return data


def _make_initial_state(
    project_id: str,
    run_id: str,
) -> CreationState:
    """创建工作流初始状态（与 test_creation_workflow.py 一致）。"""
    return CreationState(
        run_id=run_id,
        project_id=project_id,
        action="create_script",
        requirement_artifact_id=None,
        story_bible_artifact_id=None,
        outline_set_artifact_id=None,
        script_artifact_ids={},
        continuity_state_text="",
        current_episode=1,
        status="running",
        needs_user_input=False,
        error_node=None,
        error_detail=None,
        completed_nodes=[],
        input_hashes={},
        prompt_versions={},
    )


class _CorpusIngester:
    """用 FakeEmbedder 向测试 DB 摄取一个覆盖三阶段分类的最小语料。"""

    def __init__(self, db_session: Any, tmp_path: Path) -> None:
        self._repo = KnowledgeRepository(db_session)
        self._tmp_path = tmp_path
        self._embedder = FakeEmbedder()

    async def _write_doc(
        self,
        name: str,
        *,
        category: str,
        title: str,
        body: str,
        stage: str,
    ) -> None:
        meta = {
            "category": category,
            "title": title,
            "source": "drama-agent-self-auth",
            "license": "MIT",
            "language": "zh",
            "genre": "都市",
            "stage": stage,
            "tags": ["测试"],
            "version": "1.0.0",
        }
        fm = yaml.dump(meta, allow_unicode=True, sort_keys=False)
        path = self._tmp_path / name
        path.write_text(f"---\n{fm}---\n{body}\n", encoding="utf-8")

        loaded = load_knowledge_file(path)
        chunks = chunk_document(loaded.content)
        doc, _created, _changed = await self._repo.ingest_document(
            loaded, chunks, corpus_version="mvp_v1"
        )
        vectors = [await self._embedder.embed_one(c.content) for c in chunks]
        await self._repo.backfill_document_embeddings(doc.id, vectors)

    async def ingest_minimal(self) -> None:
        """摄取 8 篇文档：每阶段两个分类各 2 篇。"""
        # 题材模板（story_bible 阶段 + 大纲/写作复用）
        await self._write_doc(
            "t1.md", category="genre_template", title="战神逆袭模板", stage="story_bible",
            body="# 结构\n战神被打压后逆袭, 当众打脸反派, 身份揭露高光爽点。\n# 节奏\n结尾留钩子。",
        )
        await self._write_doc(
            "t2.md", category="genre_template", title="赘婿逆袭模板", stage="story_bible",
            body="# 结构\n赘婿扮猪吃虎, 忍辱负重后爆发, 关键场合揭露真实身份。\n# 节奏\n伏笔多集后再兑现。",
        )
        # 开篇钩子（outline 阶段）
        await self._write_doc(
            "h1.md", category="opening_hook", title="黄金三秒开场", stage="outline",
            body="# 法则\n第一集前三秒必须有冲突或悬念, 用视觉刺激抓住观众。",
        )
        await self._write_doc(
            "h2.md", category="opening_hook", title="悬念式开场", stage="outline",
            body="# 法则\n以一个未解之谜开场, 让观众带着疑问追看下一集。",
        )
        # 人物原型（story_bible + 写作阶段复用）
        await self._write_doc(
            "a1.md", category="character_archetype", title="被低估的主角", stage="story_bible",
            body="# 特征\n开局弱小被轻视, 具备隐藏实力, 核心驱动力是证明自己。",
        )
        await self._write_doc(
            "a2.md", category="character_archetype", title="冰山反派", stage="writer",
            body="# 特征\n表面冷酷强大, 有致命弱点, 与主角形成鲜明对照。",
        )
        # 爽点（writer 阶段）
        await self._write_doc(
            "p1.md", category="payoff", title="打脸式爽点", stage="writer",
            body="# 结构\n反派羞辱主角 → 主角当众反击 → 全场震惊反转。",
        )
        await self._write_doc(
            "p2.md", category="payoff", title="身份揭露爽点", stage="writer",
            body="# 结构\n压抑身份 → 关键场合揭露 → 周围人态度反转。",
        )


def _build_test_retriever(db_session: Any) -> Retriever:
    """构造与 retrieve_node 等价的自建 Retriever（FakeEmbedder 确定性向量）。"""
    return Retriever(
        KnowledgeRepository(db_session),
        FakeEmbedder(),
        corpus_version="mvp_v1",
    )


# ========================================================================
# 检索质量（hit@5 + 三阶段过滤不同）
# ========================================================================


@pytest.mark.workflow
class TestRetrievalQuality:
    """阶段检索质量门禁。"""

    @pytest.mark.asyncio
    async def test_hit_at_5_expected_categories(
        self, db_session: Any, tmp_path: Path,
    ) -> None:
        """固定 golden query 的 expected category 命中率 hit@5 ≥ 90%。

        FakeEmbedder 下由阶段分类过滤结构性保证：返回块全部来自期望分类，
        hit 指"检索返回至少一个块"——语料非空即 100%。
        """
        ingester = _CorpusIngester(db_session, tmp_path)
        await ingester.ingest_minimal()

        retriever = _build_test_retriever(db_session)
        data = json.loads(
            (_GOLDEN_DIR / "rag_expectations.json").read_text(encoding="utf-8")
        )

        total = 0
        hits = 0
        for stage, spec in data.items():
            expected = set(spec["expected_categories"])
            for query in spec["queries"]:
                total += 1
                result = await retriever.retrieve_for_stage(stage, query, top_k=5)
                if result.chunks:
                    hits += 1
                # 结构保证：返回块必须全部来自本阶段分类（验收"三类节点检索过滤不同"）
                for chunk in result.chunks:
                    assert chunk.category in expected, (
                        f"{stage} 检索返回非法分类 {chunk.category}"
                    )
        assert hits >= 0.9 * total, f"hit@5 = {hits}/{total} 低于 90%"

    @pytest.mark.asyncio
    async def test_three_stages_filter_differently(
        self, db_session: Any, tmp_path: Path,
    ) -> None:
        """同一 query 下，三个创作阶段检索的分类集互不相同。"""
        ingester = _CorpusIngester(db_session, tmp_path)
        await ingester.ingest_minimal()

        retriever = _build_test_retriever(db_session)
        query = "被青训队抛弃的足球少年逆袭故事"

        stage_categories: dict[str, set[str]] = {}
        for stage in ("story_bible", "outline", "writer"):
            result = await retriever.retrieve_for_stage(stage, query, top_k=5)
            assert result.chunks, f"{stage} 阶段检索无命中（语料应非空）"
            stage_categories[stage] = {c.category for c in result.chunks}

        assert stage_categories["story_bible"] <= {"genre_template", "character_archetype"}
        assert stage_categories["outline"] <= {"genre_template", "opening_hook"}
        assert stage_categories["writer"] <= {"payoff", "character_archetype"}
        # 三类节点检索过滤不同
        assert stage_categories["story_bible"] != stage_categories["outline"]
        assert stage_categories["outline"] != stage_categories["writer"]
        assert stage_categories["story_bible"] != stage_categories["writer"]


# ========================================================================
# 完整创作流程 + RAG（trace 持久化 / 降级）
# ========================================================================


@pytest.mark.workflow
class TestCreationWorkflowWithRag:
    """RAG 接入后的完整创作流程。"""

    @pytest.mark.asyncio
    async def test_full_creation_persists_retrieval_traces(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        db_session: Any,
        tmp_path: Path,
    ) -> None:
        """摄取语料 → 完整创作 → 三阶段 RetrievalTrace Artifact 持久化。"""
        ingester = _CorpusIngester(db_session, tmp_path)
        await ingester.ingest_minimal()

        run_svc: RunService = workflow_config["configurable"]["run_service"]
        db = workflow_config["configurable"]["db"]
        run = await run_svc.create_run(db, project_id=test_project, action="create_script")
        await run_svc.transition_status(db, run.id, "running")

        initial_state = _make_initial_state(str(test_project), str(run.id))
        workflow = build_creation_workflow()
        final_state = await workflow.ainvoke(initial_state, workflow_config)

        # 工作流完整走通（StoryBible → 大纲 → 3 集剧本）
        assert final_state["status"] == "completed"
        assert "retrieve" in final_state["completed_nodes"]
        artifact_svc: ArtifactService = workflow_config["configurable"]["artifact_service"]
        sb = await artifact_svc.get_version(db, uuid.UUID(final_state["story_bible_artifact_id"]))
        assert sb.type == "story_bible" and sb.status == "valid"
        assert len(final_state["script_artifact_ids"]) == 3

        # 每个创作阶段都持久化了 RetrievalTrace Artifact
        traces = await artifact_svc.list_by_project(
            db, test_project, artifact_type="retrieval_trace", offset=0, limit=100,
        )
        stages = sorted({t["content"].get("stage") for t in traces["items"]})
        assert stages == ["outline", "story_bible", "writer"], f"应有三阶段 trace, 实际 {stages}"

        # Exit Gate 4: trace 可追溯 chunk IDs + corpus_version + query
        for item in traces["items"]:
            content = item["content"]
            assert content["chunk_ids"], "trace 应含 chunk IDs"
            assert content["scores"] and len(content["scores"]) == len(content["chunk_ids"])
            assert content["corpus_version"] == "mvp_v1"
            assert content["query"].strip(), "trace 应含 query"
            # trace 不含全文（避免把大文本塞进 Artifact）
            assert "content" not in content and "chunks" not in content

    @pytest.mark.asyncio
    async def test_null_retriever_degrade_keeps_flow_running(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
    ) -> None:
        """删除 RAG（注入 NullRetriever）后主流程仍可完整运行。

        ctx 注入 NullRetriever → 三阶段检索返回空 → 三个 Skill 拿到空
        rag_context 回退到原有兜底（"(无知识库参考资料)"），创作不中断。
        """
        workflow_config["configurable"]["retriever"] = NullRetriever(corpus_version="mvp_v1")

        run_svc: RunService = workflow_config["configurable"]["run_service"]
        db = workflow_config["configurable"]["db"]
        run = await run_svc.create_run(db, project_id=test_project, action="create_script")
        await run_svc.transition_status(db, run.id, "running")

        initial_state = _make_initial_state(str(test_project), str(run.id))
        workflow = build_creation_workflow()
        final_state = await workflow.ainvoke(initial_state, workflow_config)

        assert final_state["status"] == "completed"
        assert "retrieve" in final_state["completed_nodes"]

        artifact_svc: ArtifactService = workflow_config["configurable"]["artifact_service"]
        sb = await artifact_svc.get_version(db, uuid.UUID(final_state["story_bible_artifact_id"]))
        assert sb.type == "story_bible" and sb.status == "valid"
        outline = await artifact_svc.get_version(db, uuid.UUID(final_state["outline_set_artifact_id"]))
        assert outline.type == "episode_outline_set" and outline.status == "valid"
        assert len(final_state["script_artifact_ids"]) == 3

        # 降级下不产生 retrieval_trace Artifact
        traces = await artifact_svc.list_by_project(
            db, test_project, artifact_type="retrieval_trace", offset=0, limit=100,
        )
        assert traces["total"] == 0
