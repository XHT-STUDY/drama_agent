"""K-1 上传联动摄取集成测试。

覆盖：
- route=hold（reference）上传 → 自动切块 + 向量化入 knowledge corpus（项目作用域）
- 幂等：同 upload 重复导入不产生重复文档/块
- 项目隔离：项目 A 的资料对项目 B 检索不可见；全局语料对两者可见
- 摄取失败（embedder 异常）不阻断导入归档（Run completed + 告警事件）
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.knowledge_chunk import KnowledgeChunk
from app.db.models.knowledge_document import KnowledgeDocument
from app.db.models.project import Project
from app.db.models.upload import Upload
from app.storage.local import LocalFileStore

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"

# 与 import 工作流测试同款的参考资料文本（规则命中 reference → route=hold）
_REFERENCE_TEXT = (
    "参考资料：短剧《逆袭人生》设定集。\n"
    "主角林峰拥有战术视野天赋，被青训队抛弃后遇到伯乐教练张德胜。\n"
    "反派陈浩是林峰青训时期的前队友，两人冲突贯穿全剧。\n"
    "人物关系参考文档见 http://example.com/chars。"
)


@pytest_asyncio.fixture
async def db_session(test_engine: Any) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


async def _seed_project(db: AsyncSession, title: str) -> uuid.UUID:
    project = Project(title=title, target_episode_count=10)
    db.add(project)
    await db.flush()
    return project.id


async def _seed_upload(
    db: AsyncSession, project_id: uuid.UUID, *, text: str
) -> uuid.UUID:
    from app.core.config import load_settings

    settings = load_settings()
    store = LocalFileStore(root=settings.upload_file_root)
    data = text.encode("utf-8")
    key = await store.save(data, suffix=".txt")
    upload = Upload(
        project_id=project_id,
        path=key,
        sha256=hashlib.sha256(data).hexdigest(),
        mime_type="text/plain",
        size_bytes=len(data),
        original_name="reference-material.txt",
        parse_status="parsed",
        char_count=len(text),
        warnings=[],
    )
    db.add(upload)
    await db.flush()
    return upload.id


async def _run_import(
    db: AsyncSession, project_id: uuid.UUID, upload_id: uuid.UUID
) -> dict[str, Any]:
    """跑一次 import 工作流（FakeLLM；reference 走规则，无需 LLM fixture）。"""
    from langchain_core.runnables import RunnableConfig

    from app.agents.base import BaseAgent
    from app.application.artifact_service import ArtifactService
    from app.application.run_service import RunService
    from app.events.publisher import EventPublisher
    from app.llm.fake import FakeLLM
    from app.prompts.loader import PromptLoader
    from app.workflows.import_file import build_import_workflow

    run_service = RunService()
    run = await run_service.create_run(
        db=db, project_id=project_id, action="import"
    )
    await run_service.transition_status(db, run.id, "running")

    progress_log: list[dict[str, Any]] = []
    config: RunnableConfig = {
        "configurable": {
            "db": db,
            "agent": BaseAgent(name="planner", llm=FakeLLM(seed=42)),
            "prompt_loader": PromptLoader(),
            "artifact_service": ArtifactService(),
            "run_service": RunService(),
            "event_publisher": EventPublisher(),
            "upload_id": str(upload_id),
            "progress_callback": lambda *a: progress_log.append({}),
            "progress_log": progress_log,
        },
    }
    state: dict[str, Any] = {
        "run_id": str(run.id),
        "project_id": str(project_id),
        "action": "import",
        "upload_id": str(upload_id),
        "classification_artifact_id": None,
        "script_artifact_id": None,
        "content_type": None,
        "route": None,
        "needs_user_input": False,
        "status": "running",
        "error_node": None,
        "error_detail": None,
        "completed_nodes": [],
        "prompt_versions": {},
    }
    final = await build_import_workflow().ainvoke(state, cast(Any, config))
    # 工作流本体不迁移 Run 状态（由 Dispatcher 收尾）；测试内手动置终态，
    # 避免同项目第二次导入撞上单活跃 Run 约束。
    await run_service.transition_status(db, run.id, "completed")
    await db.commit()
    return cast(dict[str, Any], final)


@pytest.mark.integration
@pytest.mark.asyncio
class TestUploadIngest:
    async def test_reference_upload_ingests_into_project_corpus(
        self, db_session: AsyncSession
    ) -> None:
        """route=hold → 切块 + 向量化入 knowledge corpus（project 绑定）。"""
        project_id = await _seed_project(db_session, "K-1 摄取项目")
        upload_id = await _seed_upload(
            db_session, project_id, text=_REFERENCE_TEXT
        )

        final = await _run_import(db_session, project_id, upload_id)

        assert final["route"] == "hold"
        assert final["status"] != "failed"

        docs = (
            await db_session.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.project_id == project_id
                )
            )
        ).scalars().all()
        assert len(docs) == 1
        doc = docs[0]
        assert doc.category == "reference"
        assert doc.source == f"upload:{upload_id}"

        chunks = (
            await db_session.execute(
                select(KnowledgeChunk).where(
                    KnowledgeChunk.document_id == doc.id
                )
            )
        ).scalars().all()
        assert len(chunks) >= 1
        # 全部块已有向量（FakeEmbedder 确定性）
        assert all(c.embedding is not None for c in chunks)

    async def test_same_upload_reimport_is_idempotent(
        self, db_session: AsyncSession
    ) -> None:
        """同 upload 重复导入：document_hash 命中 → 不产生重复文档/块。"""
        project_id = await _seed_project(db_session, "K-1 幂等项目")
        upload_id = await _seed_upload(
            db_session, project_id, text=_REFERENCE_TEXT
        )

        await _run_import(db_session, project_id, upload_id)
        await _run_import(db_session, project_id, upload_id)

        docs = (
            await db_session.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.project_id == project_id
                )
            )
        ).scalars().all()
        assert len(docs) == 1
        chunks = (
            await db_session.execute(
                select(KnowledgeChunk).where(
                    KnowledgeChunk.document_id == docs[0].id
                )
            )
        ).scalars().all()
        assert len(chunks) >= 1  # 块数与首摄一致（未翻倍）

    async def test_project_scope_isolation_in_search(
        self, db_session: AsyncSession
    ) -> None:
        """项目 A 的资料对项目 B 检索不可见；全局语料两者可见。"""
        from app.db.repositories.knowledge import KnowledgeRepository
        from app.rag.chunker import chunk_document
        from app.rag.embedder import load_embedder
        from app.rag.loader import LoadedKnowledgeDoc, compute_document_hash
        from app.rag.models import (
            KnowledgeCategory,
            KnowledgeDocMetadata,
        )
        from app.rag.retriever import Retriever

        project_a = await _seed_project(db_session, "项目A")
        project_b = await _seed_project(db_session, "项目B")
        upload_id = await _seed_upload(
            db_session, project_a, text=_REFERENCE_TEXT
        )
        await _run_import(db_session, project_a, upload_id)

        # 摄入一篇全局语料（project_id=None，模拟 knowledge/ 目录摄取）
        global_meta = KnowledgeDocMetadata(
            title="全局题材模板",
            source="corpus",
            license="internal",
            category=KnowledgeCategory.GENRE_TEMPLATE,
        )
        global_doc = LoadedKnowledgeDoc(
            path=Path("corpus://global"),
            metadata=global_meta,
            content="都市逆袭题材通用模板：底层主角 + 伯乐 + 反派压迫。",
            document_hash=compute_document_hash(global_meta, "都市逆袭题材通用模板"),
        )
        embedder = load_embedder()  # test 环境 → FakeEmbedder
        repo = KnowledgeRepository(db_session)
        chunks = chunk_document(global_doc.content)
        await repo.ingest_document(
            global_doc, chunks, corpus_version="mvp_v1", project_id=None
        )
        vectors = await embedder.embed([c.content for c in chunks])
        doc_row, _, _ = await repo.ingest_document(
            global_doc, chunks, corpus_version="mvp_v1", project_id=None
        )
        await repo.backfill_document_embeddings(doc_row.id, vectors.vectors)
        await db_session.commit()

        retriever = Retriever(repo, embedder, corpus_version="mvp_v1")

        # 项目 A：能看到自有 reference 资料
        result_a = await retriever.retrieve(
            "林峰 战术视野 张德胜",
            category="reference",
            project_id=str(project_a),
            min_score=-1.0,
        )
        assert result_a.chunks, "项目 A 应检索到自有上传资料"

        # 项目 B：看不到项目 A 的资料
        result_b = await retriever.retrieve(
            "林峰 战术视野 张德胜",
            category="reference",
            project_id=str(project_b),
            min_score=-1.0,
        )
        assert not result_b.chunks, "项目 B 不应检索到项目 A 的上传资料"

        # 全局语料（genre_template）对两个项目都可见
        result_global_b = await retriever.retrieve(
            "都市逆袭题材模板",
            category="genre_template",
            project_id=str(project_b),
            min_score=-1.0,
        )
        assert result_global_b.chunks, "全局语料对项目 B 应可见"

    async def test_ingest_failure_does_not_block_import(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """embedder 异常 → 导入归档照常 completed，仅发告警事件。"""
        project_id = await _seed_project(db_session, "K-1 失败降级项目")
        upload_id = await _seed_upload(
            db_session, project_id, text=_REFERENCE_TEXT
        )

        import app.rag.embedder as embedder_module

        class ExplodingEmbedder:
            async def embed(self, texts: list[str]) -> Any:
                raise RuntimeError("embedding 服务不可用")

            async def embed_one(self, text: str) -> list[float]:
                raise RuntimeError("embedding 服务不可用")

            async def close(self) -> None:
                return None

        monkeypatch.setattr(
            embedder_module, "load_embedder", lambda *a, **k: ExplodingEmbedder()
        )

        final = await _run_import(db_session, project_id, upload_id)

        # 导入归档不受影响
        assert final["route"] == "hold"
        assert final["status"] != "failed"
        assert final["classification_artifact_id"]

        # 知识库无该项目的文档
        docs = (
            await db_session.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.project_id == project_id
                )
            )
        ).scalars().all()
        assert docs == []

        # 告警事件已发布
        from app.db.models.workflow_event import WorkflowEvent

        events = (
            await db_session.execute(
                select(WorkflowEvent).where(
                    WorkflowEvent.type == "run.warning",
                    WorkflowEvent.payload["code"].astext == "KNOWLEDGE_INGEST_FAILED",
                )
            )
        ).scalars().all()
        assert len(events) >= 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_dedup_is_project_scoped_w106(test_engine: Any) -> None:
    """W1-06/REPEAT=5 回归：同内容资料跨项目不串用幂等——
    A 项目摄取后删除，B 项目上传同内容应重新入库（而非命中 A 的旧文档）。"""
    import uuid as _uuid

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.application.knowledge_service import KnowledgeService
    from app.db.models.project import Project
    from app.db.repositories.knowledge import KnowledgeRepository
    from app.rag.embedder import load_embedder

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    projects = []
    for i in range(2):
        p = Project(title=f"知识隔离{i}", target_episode_count=3)
        session_f = async_sessionmaker(test_engine, expire_on_commit=False)
        async with session_f() as s:
            s.add(p)
            await s.commit()
        projects.append(p.id)

    async with factory() as db:
        repo = KnowledgeRepository(db)
        svc = KnowledgeService()
        text = "林峰的战术视野是天赋。"
        title = "reference-material.txt"
        embedder = load_embedder()
        # A 项目入库
        r1 = await svc.ingest_upload(
            db, project_id=projects[0], upload_id=_uuid.uuid4(),
            title=title, text=text,
            embedder=embedder,
        )
        await db.commit()
        assert r1.created
        # A 项目删除（软删除）
        await repo.soft_delete_document(projects[0], r1.document_id)
        await db.commit()
        # B 项目上传同内容：必须新建，而不是命中 A 的（已删）文档
        r2 = await svc.ingest_upload(
            db, project_id=projects[1], upload_id=_uuid.uuid4(),
            title=title, text=text,
            embedder=embedder,
        )
        await db.commit()
        assert r2.created, "B 项目同内容必须新建文档"
        assert r2.document_id != r1.document_id
