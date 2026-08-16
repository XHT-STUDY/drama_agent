"""Upload API 路由 — 安全上传与解析 (G-03)。

端点：
- POST /projects/{project_id}/uploads   上传 TXT/DOCX（校验 → 解析 → 落盘 + 元数据行）
- GET  /projects/{project_id}/uploads   列出项目上传记录

安全约束（G-03 验收）：
- 原始文件名仅存 original_name（展示用），磁盘路径用服务端 UUID 存储键；
- 文件内容不写日志；
- 大小 / 扩展名 / 内容签名联合校验（不信客户端 Content-Type）；
- 上传文件归属项目，读取必须走项目维度（跨项目天然隔离）。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Request
from fastapi import UploadFile as FastAPIUploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db, get_settings
from app.application.project_service import ProjectService
from app.core.config import Settings
from app.core.errors import FileParseFailedError, FileTooLargeError
from app.db.models.upload import Upload
from app.db.repositories.uploads import UploadRepository
from app.storage.local import LocalFileStore
from app.storage.protocol import FileStore
from app.tools.file_parser import FileParserTool

router = APIRouter(tags=["uploads"])
_project_svc = ProjectService()


# ---- Response Schema ----


class UploadResponse(BaseModel):
    """上传成功的响应（含解析元数据）。"""

    model_config = {"extra": "forbid"}

    id: uuid.UUID
    project_id: uuid.UUID
    path: str = Field(..., description="服务端存储键（UUID 文件名，非客户端名）")
    sha256: str
    mime_type: str
    size_bytes: int
    original_name: str
    parse_status: str
    char_count: int
    warnings: list[str]
    created_at: datetime


def _to_response(u: Upload) -> UploadResponse:
    """ORM → 响应模型。"""
    return UploadResponse(
        id=u.id,
        project_id=u.project_id,
        path=u.path,
        sha256=u.sha256,
        mime_type=u.mime_type,
        size_bytes=u.size_bytes,
        original_name=u.original_name,
        parse_status=u.parse_status,
        char_count=u.char_count,
        warnings=u.warnings or [],
        created_at=u.created_at,
    )


# ---- 辅助 ----


async def _read_with_limit(file: FastAPIUploadFile, max_bytes: int) -> bytes:
    """分块读取上传内容，超限立即抛错（避免整文件载入内存）。"""
    data = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)  # 1 MB 分块
        if not chunk:
            break
        data += chunk
        if len(data) > max_bytes:
            raise FileTooLargeError(
                detail=f"文件大小超过上限 {max_bytes} 字节",
            )
    return bytes(data)


# ---- 端点 ----


@router.post(
    "/projects/{project_id}/uploads",
    response_model=UploadResponse,
    status_code=201,
    responses={
        201: {"description": "上传成功，返回解析后的上传记录"},
        404: {"description": "项目不存在"},
        413: {"description": "文件超过大小上限"},
        415: {"description": "文件类型不被接受"},
        422: {"description": "文件解析失败（损坏/伪装/编码无法识别）"},
    },
)
async def create_upload(
    project_id: uuid.UUID,
    request: Request,
    file: Annotated[FastAPIUploadFile, File(description="TXT/DOCX 文件，≤10MB")],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UploadResponse:
    """上传并解析 TXT/DOCX，落盘 + 写元数据行，返回解析结果。

    校验顺序（同步拒绝，不排队）：
    1. 项目存在（404）;
    2. 文件名缺失 / 扩展名不受支持（422）;
    3. 大小超限（413，分块读取提前止损）;
    4. 内容签名 / 编码 / DOCX 结构校验（422）。
    """
    # 1. 项目存在校验
    await _project_svc.get(db, project_id)

    # 2. 文件名缺失校验
    filename = file.filename or ""
    if not filename:
        raise FileParseFailedError(detail="缺少文件名，无法识别文件类型")

    # 3. 分块读取 + 大小止损
    settings: Settings = get_settings(request)
    data = await _read_with_limit(file, settings.upload_max_bytes)

    # 4. 解析（扩展名 / MIME 签名 / 编码 / DOCX 结构联合校验）
    parsed = await FileParserTool(upload_max_bytes=settings.upload_max_bytes).execute(
        filename=filename, data=data,
    )

    # 5. FileStore 落盘（服务端 UUID 键，扩展名取检测结果）
    store: FileStore = LocalFileStore(root=settings.upload_file_root)
    key = await store.save(data, suffix=f".{parsed.detected_format}")

    # 6. 元数据行入库（original_name 仅展示用；内容不入库不写日志）
    sha256 = hashlib.sha256(data).hexdigest()
    repo = UploadRepository(db)
    row = await repo.add(
        Upload(
            project_id=project_id,
            path=key,
            sha256=sha256,
            mime_type=parsed.mime_type,
            size_bytes=len(data),
            original_name=filename[:255],
            parse_status="parsed",
            char_count=parsed.char_count,
            warnings=parsed.warnings,
        )
    )
    return _to_response(row)


@router.get("/projects/{project_id}/uploads")
async def list_uploads(
    project_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """列出项目上传记录（按创建时间倒序）。"""
    await _project_svc.get(db, project_id)
    repo = UploadRepository(db)
    items = await repo.list_by_project(project_id, offset=offset, limit=limit)
    return {
        "items": [_to_response(u) for u in items],
        "total": len(items),
        "offset": offset,
        "limit": limit,
    }
