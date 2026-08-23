"""知识库管理 API（K-2）。

端点：
- GET    /projects/{id}/knowledge                 项目可见知识文档列表（自有 + 全局语料，含块数与向量化状态）
- DELETE /projects/{id}/knowledge/{document_id}   软删除项目自有文档（全局语料不可删）
- POST   /projects/{id}/knowledge/search          检索试算（项目作用域：自有 + 全局）
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db, get_settings
from app.core.errors import NotFoundError
from app.db.repositories.knowledge import KnowledgeRepository
from app.rag.embedder import Embedder, load_embedder
from app.rag.models import load_corpus_version

router = APIRouter(tags=["knowledge"])


# ---- 请求/响应模型 ----


class KnowledgeSearchRequest(BaseModel):
    """检索试算请求。"""

    model_config = {"extra": "forbid"}

    query: str = Field(..., min_length=1, max_length=2000, description="检索文本")
    top_k: int = Field(default=5, ge=1, le=20, description="返回命中数")
    category: str | None = Field(default=None, description="限定文档分类")
    min_score: float | None = Field(default=None, ge=-1.0, le=1.0, description="最低相似度阈值")


class KnowledgeSearchHit(BaseModel):
    """单条检索命中。"""

    model_config = {"extra": "forbid"}

    chunk_id: str
    content: str = Field(..., description="块内容摘要（前 400 字）")
    score: float
    document_id: str
    document_title: str
    category: str
    source: str
    scope: Literal["project", "global"]


class KnowledgeSearchResponse(BaseModel):
    """检索试算响应（含检索 trace 元数据，不落 Artifact）。"""

    model_config = {"extra": "forbid"}

    query: str
    corpus_version: str
    filters: dict[str, Any]
    elapsed_ms: int
    hits: list[KnowledgeSearchHit]


class KnowledgeDocumentItem(BaseModel):
    """知识文档列表项。"""

    model_config = {"extra": "forbid"}

    id: str
    scope: Literal["project", "global"]
    title: str
    category: str
    source: str
    chunk_count: int
    embedded_chunk_count: int
    fully_embedded: bool
    corpus_version: str | None
    created_at: str
    deleted: bool = False


class KnowledgeListResponse(BaseModel):
    model_config = {"extra": "forbid"}

    items: list[KnowledgeDocumentItem]
    total: int


# ---- 端点 ----


async def _load_project_or_404(db: AsyncSession, project_id: uuid.UUID) -> None:
    """项目存在性校验（软删项目视作不存在）。"""
    from sqlalchemy import select

    from app.db.models.project import Project

    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None or project.deleted_at is not None:
        raise NotFoundError(detail=f"项目不存在: {project_id}", code="PROJECT_NOT_FOUND")


@router.get(
    "/projects/{project_id}/knowledge",
    response_model=KnowledgeListResponse,
)
async def list_knowledge(
    project_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    scope: Annotated[
        Literal["project", "global", "all"] | None, Query(description="作用域过滤")
    ] = "all",
) -> KnowledgeListResponse:
    """列出项目可见的知识文档（自有 + 全局语料），含块数与向量化状态。"""
    await _load_project_or_404(db, project_id)
    rows = await KnowledgeRepository(db).list_documents_for_project(project_id)

    items: list[KnowledgeDocumentItem] = []
    for doc, chunk_count, embedded in rows:
        doc_scope = "project" if doc.project_id is not None else "global"
        if scope != "all" and doc_scope != scope:
            continue
        items.append(
            KnowledgeDocumentItem(
                id=str(doc.id),
                scope=doc_scope,  # type: ignore[arg-type]
                title=doc.title,
                category=doc.category,
                source=doc.source,
                chunk_count=chunk_count,
                embedded_chunk_count=embedded,
                fully_embedded=chunk_count == embedded,
                corpus_version=getattr(doc, "corpus_version", None),
                created_at=doc.created_at.isoformat() if doc.created_at else "",
            )
        )
    return KnowledgeListResponse(items=items, total=len(items))


@router.delete(
    "/projects/{project_id}/knowledge/{document_id}",
    response_model=dict[str, Any],
)
async def delete_knowledge_document(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """软删除项目自有知识文档（幂等；全局语料不可经项目端点删除）。"""
    await _load_project_or_404(db, project_id)
    doc = await KnowledgeRepository(db).soft_delete_document(document_id, project_id)
    if doc is None:
        raise NotFoundError(
            detail=(
                f"知识文档不存在或不属于该项目: {document_id}"
                "（全局语料不可经项目端点删除）"
            ),
            code="KNOWLEDGE_DOC_NOT_FOUND",
        )
    await db.commit()
    return {"deleted": True, "document_id": str(document_id)}


@router.post(
    "/projects/{project_id}/knowledge/search",
    response_model=KnowledgeSearchResponse,
)
async def search_knowledge(
    project_id: uuid.UUID,
    body: KnowledgeSearchRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> KnowledgeSearchResponse:
    """检索试算：项目作用域（自有 + 全局语料）向量检索，返回命中与 trace 元数据。"""
    await _load_project_or_404(db, project_id)

    min_score: float | None = body.min_score
    if min_score is None:
        min_score = -1.0  # 试算语义：不设阈值，按相似度排序展示

    embedder: Embedder = load_embedder(get_settings(request))
    repo = KnowledgeRepository(db)
    start = time.monotonic()
    try:
        query_vector = await embedder.embed_one(body.query)
        hits = await repo.search_similar(
            query_vector,
            body.top_k,
            category=body.category,
            min_score=min_score,
            project_id=project_id,
        )
    finally:
        await embedder.close()
    elapsed_ms = int((time.monotonic() - start) * 1000)

    def _hit_scope(source_project_id: uuid.UUID | None) -> str:
        return "project" if source_project_id is not None else "global"

    return KnowledgeSearchResponse(
        query=body.query,
        corpus_version=load_corpus_version(),
        filters={"category": body.category, "top_k": body.top_k, "min_score": min_score},
        elapsed_ms=elapsed_ms,
        hits=[
            KnowledgeSearchHit(
                chunk_id=str(h.chunk_id),
                content=h.content[:400],
                score=round(h.score, 4),
                document_id=str(h.document_id),
                document_title=h.title,
                category=h.category,
                source=h.source,
                scope=_hit_scope(h.project_id),  # type: ignore[arg-type]
            )
            for h in hits
        ],
    )
