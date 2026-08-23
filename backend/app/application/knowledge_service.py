"""KnowledgeService — 上传资料摄取入知识库（K-1）。

把导入分类 route=hold（reference）的上传文本切块 + 向量化后写入
knowledge corpus，绑定项目作用域（project_id）；检索时项目自有文档
与全局语料（project_id IS NULL）对该项目可见，其他项目互不可见。

幂等：document_hash 由（分类/标题/来源/正文…）确定性计算——同一
upload 重复摄取命中 get_by_hash 直接跳过，不产生重复文档/块。

模块边界：不解析文件（调用方传入已解析文本）；失败不阻断导入归档
（调用方 try/except 兜底）。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.knowledge import KnowledgeRepository
from app.rag.chunker import chunk_document
from app.rag.embedder import Embedder
from app.rag.loader import LoadedKnowledgeDoc, compute_document_hash
from app.rag.models import KnowledgeCategory, KnowledgeDocMetadata

logger = logging.getLogger(__name__)

# 上传资料的固定语料版本（区别于 knowledge/ 目录的全局 corpus 版本）
UPLOAD_CORPUS_VERSION = "uploads_v1"


@dataclass(frozen=True)
class IngestUploadResult:
    """一次上传摄取的结果（供事件与测试断言）。"""

    document_id: uuid.UUID
    created: bool
    changed: bool
    chunk_count: int


def build_upload_doc(
    *,
    upload_id: uuid.UUID,
    project_id: uuid.UUID,
    title: str,
    text: str,
) -> LoadedKnowledgeDoc:
    """把上传文本包装为知识文档（category=reference，项目作用域）。

    source 固定为 upload:{id}——document_hash 因此与 upload 内容一一对应，
    同一 upload 重复摄取幂等跳过。
    """
    metadata = KnowledgeDocMetadata(
        title=title or f"上传资料 {str(upload_id)[:8]}",
        source=f"upload:{upload_id}",
        license="user-upload",
        category=KnowledgeCategory.REFERENCE,
        language="zh",
        tags=["upload"],
    )
    return LoadedKnowledgeDoc(
        path=Path(f"upload://{upload_id}"),  # 非文件系统路径，仅溯源展示
        metadata=metadata,
        content=text,
        document_hash=compute_document_hash(metadata, text),
    )


class KnowledgeService:
    """上传资料 → 知识库摄取。"""

    async def ingest_upload(
        self,
        db: AsyncSession,
        *,
        project_id: uuid.UUID,
        upload_id: uuid.UUID,
        title: str,
        text: str,
        embedder: Embedder,
    ) -> IngestUploadResult:
        """摄取一篇上传资料（切块 → 向量化 → 幂等落库）。

        Raises:
            Exception: 切块/向量化/落库失败——由调用方决定是否阻断
            （导入流程选择不阻断，仅告警）。
        """
        loaded = build_upload_doc(
            upload_id=upload_id, project_id=project_id, title=title, text=text
        )
        chunks = chunk_document(loaded.content)
        if not chunks:
            raise ValueError(f"上传资料切块为空: upload={upload_id}")

        # 先向量化再落库：embedding 失败时不留半截文档（无 DB 写入）
        vectors = await embedder.embed([c.content for c in chunks])

        async with db.begin_nested():
            repo = KnowledgeRepository(db)
            document, created, changed = await repo.ingest_document(
                loaded,
                chunks,
                corpus_version=UPLOAD_CORPUS_VERSION,
                project_id=project_id,
            )
            if created or changed:
                await repo.backfill_document_embeddings(
                    document.id, vectors.vectors
                )
        return IngestUploadResult(
            document_id=document.id,
            created=created,
            changed=changed,
            chunk_count=len(chunks),
        )
