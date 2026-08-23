"""K-2 知识库管理 API 集成测试。

覆盖：
- 列表：项目自有 + 全局语料（scope 标注）、块数与向量化状态、scope 过滤
- 软删：仅项目自有可删、幂等（重复删除 404）、删除后列表/检索不可见
- 全局语料不可经项目端点删除
- 检索试算：命中含 chunk/score/scope/trace 元数据；项目隔离
- 项目不存在 → 404
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.project import Project


@pytest_asyncio.fixture
async def db_session(test_engine: Any) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(app: Any) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_project(db: AsyncSession, title: str) -> uuid.UUID:
    project = Project(title=title, target_episode_count=10)
    db.add(project)
    await db.flush()
    return project.id


async def _ingest(
    db: AsyncSession,
    *,
    project_id: uuid.UUID | None,
    title: str,
    text: str,
    category: str = "reference",
) -> uuid.UUID:
    """直接经 Repository 摄取一篇文档（FakeEmbedder 向量化）。"""
    from pathlib import Path

    from app.db.repositories.knowledge import KnowledgeRepository
    from app.rag.chunker import chunk_document
    from app.rag.embedder import load_embedder
    from app.rag.loader import LoadedKnowledgeDoc, compute_document_hash
    from app.rag.models import KnowledgeCategory, KnowledgeDocMetadata

    metadata = KnowledgeDocMetadata(
        title=title,
        source=f"seed:{title}",
        license="internal",
        category=KnowledgeCategory(category),
    )
    loaded = LoadedKnowledgeDoc(
        path=Path(f"seed://{title}"),
        metadata=metadata,
        content=text,
        document_hash=compute_document_hash(metadata, text),
    )
    chunks = chunk_document(text)
    embedder = load_embedder()
    vectors = await embedder.embed([c.content for c in chunks])
    repo = KnowledgeRepository(db)
    doc, _, _ = await repo.ingest_document(
        loaded, chunks, corpus_version="seed_v1", project_id=project_id
    )
    await repo.backfill_document_embeddings(doc.id, vectors.vectors)
    await db.commit()
    return doc.id


@pytest.mark.integration
@pytest.mark.asyncio
class TestKnowledgeApi:
    async def test_list_shows_project_and_global_docs(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """列表含项目自有与全局语料，scope 标注正确，支持过滤。"""
        project_id = await _seed_project(db_session, "K-2 列表项目")
        await _ingest(
            db_session,
            project_id=project_id,
            title="项目参考资料",
            text="主角林峰的战术视野设定与人物关系。",
        )
        await _ingest(
            db_session,
            project_id=None,
            title="全局题材模板",
            text="都市逆袭题材通用模板。",
            category="genre_template",
        )

        resp = await client.get(f"/api/v1/projects/{project_id}/knowledge")
        assert resp.status_code == 200
        body = resp.json()
        scopes = {item["title"]: item["scope"] for item in body["items"]}
        assert scopes.get("项目参考资料") == "project"
        assert scopes.get("全局题材模板") == "global"
        assert body["total"] == 2

        project_only = await client.get(
            f"/api/v1/projects/{project_id}/knowledge",
            params={"scope": "project"},
        )
        titles = [i["title"] for i in project_only.json()["items"]]
        assert titles == ["项目参考资料"]

        item = body["items"][0]
        assert item["chunk_count"] >= 1
        assert item["fully_embedded"] is True

    async def test_soft_delete_only_own_docs_and_idempotent(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """软删仅限项目自有；重复删除/跨项目/全局 → 404；删除后列表与检索不可见。"""
        project_id = await _seed_project(db_session, "K-2 删除项目")
        other_project = await _seed_project(db_session, "K-2 他人项目")
        doc_id = await _ingest(
            db_session,
            project_id=project_id,
            title="待删资料",
            text="这份资料将被软删除：林峰 张德胜 陈浩。",
        )
        global_doc = await _ingest(
            db_session, project_id=None, title="全局不可删", text="全局语料。"
        )

        # 他项目删除 → 404
        resp_other = await client.delete(
            f"/api/v1/projects/{other_project}/knowledge/{doc_id}"
        )
        assert resp_other.status_code == 404
        assert resp_other.json()["code"] == "KNOWLEDGE_DOC_NOT_FOUND"

        # 全局语料删除 → 404
        resp_global = await client.delete(
            f"/api/v1/projects/{project_id}/knowledge/{global_doc}"
        )
        assert resp_global.status_code == 404

        # 本人删除 → 200；重复删除 → 404（幂等语义：视作不存在）
        resp = await client.delete(
            f"/api/v1/projects/{project_id}/knowledge/{doc_id}"
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        again = await client.delete(
            f"/api/v1/projects/{project_id}/knowledge/{doc_id}"
        )
        assert again.status_code == 404

        # 列表不再出现；检索不再命中
        listing = await client.get(f"/api/v1/projects/{project_id}/knowledge")
        titles = [i["title"] for i in listing.json()["items"]]
        assert "待删资料" not in titles
        assert "全局不可删" in titles

        search = await client.post(
            f"/api/v1/projects/{project_id}/knowledge/search",
            json={"query": "林峰 张德胜 陈浩", "top_k": 10},
        )
        assert search.status_code == 200
        hit_docs = {h["document_id"] for h in search.json()["hits"]}
        assert str(doc_id) not in hit_docs

    async def test_search_returns_hits_with_scope_and_trace(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """检索试算：命中含 score/scope/来源；trace 含 corpus_version/filters/耗时。"""
        project_id = await _seed_project(db_session, "K-2 检索项目")
        await _ingest(
            db_session,
            project_id=project_id,
            title="林峰设定资料",
            text="林峰拥有战术视野天赋，被青训队抛弃后遇伯乐张德胜。",
        )
        await _ingest(
            db_session, project_id=None, title="全局爽点库", text="打脸反转爽点模板。"
        )

        resp = await client.post(
            f"/api/v1/projects/{project_id}/knowledge/search",
            json={"query": "林峰的战术视野", "top_k": 5},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["corpus_version"]
        assert body["filters"]["top_k"] == 5
        assert body["elapsed_ms"] >= 0
        assert len(body["hits"]) >= 1
        hit = body["hits"][0]
        assert {"chunk_id", "content", "score", "document_title", "scope"} <= set(hit)
        hit_titles = {h["document_title"] for h in body["hits"]}
        assert "林峰设定资料" in hit_titles

        # 分类过滤生效
        filtered = await client.post(
            f"/api/v1/projects/{project_id}/knowledge/search",
            json={"query": "爽点", "top_k": 5, "category": "payoff"},
        )
        assert filtered.status_code == 200

    async def test_unknown_project_returns_404(self, client: AsyncClient) -> None:
        resp = await client.get(f"/api/v1/projects/{uuid.uuid4()}/knowledge")
        assert resp.status_code == 404
        assert resp.json()["code"] == "PROJECT_NOT_FOUND"
