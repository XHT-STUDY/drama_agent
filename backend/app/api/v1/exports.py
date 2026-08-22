"""Export API 路由 — 项目内容导出与下载 (G-06)。

端点：
- POST /projects/{project_id}/exports                   发起导出（202，返回 Run）
- GET  /exports/{artifact_id}/download?project_id=...   下载导出文件

POST 返回 Run（action="export"），Worker 异步执行 G-05 ExportService：
组装各 kind 的 latest valid（或显式指定版本）→ 序列化（Markdown/DOCX）
→ FileStore 原子落盘 → 持久化 ExportFile Artifact。
任一步失败时 Run 标记 failed，不生成 valid ExportFile（G-05 验收闭环）。

下载安全约束（G-06 验收）：
- 文件名是导出时生成的安全名（已清洗路径分隔符 / 控制字符），
  Content-Disposition 用 `filename*`（RFC 5987）编码中文，ASCII 名兜底；
- 必须携带 project_id，导出文件不属于该项目 → 403 CROSS_PROJECT_ACCESS；
- Artifact 不是 export_file / 存储文件丢失 → 404 EXPORT_FILE_MISSING；
- 客户端原始文件名永不入盘（存储键是服务端 UUID）。
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db, get_settings
from app.api.v1.runs import RunResponse
from app.application.artifact_service import ArtifactService
from app.application.project_service import ProjectService
from app.application.run_service import RunService
from app.application.workflow_dispatcher import schedule_worker
from app.core.config import Settings
from app.core.errors import AppError, ExportFileMissingError, NotFoundError
from app.domain.enums import ExportFormat
from app.domain.export import ExportContentKind, ExportFileContent
from app.storage.local import LocalFileStore
from app.storage.protocol import FileStore

router = APIRouter(tags=["exports"])
_project_svc = ProjectService()
_artifact_svc = ArtifactService()
_run_svc = RunService()

# 下载媒体类型（按导出格式映射）
_MEDIA_TYPES: dict[str, str] = {
    "markdown": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
# ASCII 兜底名（纯中文文件名在 Content-Disposition filename= 无法表达时用）
_ASCII_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


# ---- Request Schema ----


class CreateExportRequest(BaseModel):
    """发起导出请求体。

    kinds 必填（可导出 StoryBible / 大纲 / 剧本 / 评估 / 修订说明的组合）；
    artifact_ids 缺省为各 kind 取 latest valid，提供时显式指定 Artifact ID
    （服务端会校验类型 / status / 项目归属，见 ExportService._fetch_explicit）。
    """

    model_config = {"extra": "forbid"}

    kinds: list[ExportContentKind] = Field(
        ..., min_length=1, description="要导出的内容类型列表"
    )
    format: ExportFormat = Field(
        default="markdown", description="导出格式: markdown / docx"
    )
    artifact_ids: dict[str, list[str]] | None = Field(
        default=None,
        description="显式指定要导出的 Artifact ID（kind → ID 列表）；None 表示取 latest valid",
    )
    idempotency_key: str | None = Field(
        default=None, max_length=128, description="幂等键（相同键返回已有 Run）"
    )


# ---- 端点 ----


@router.post(
    "/projects/{project_id}/exports",
    response_model=RunResponse,
    status_code=202,
    responses={
        202: {"description": "导出 Run 已创建并进入队列"},
        404: {"description": "项目不存在"},
    },
)
async def create_export(
    project_id: uuid.UUID,
    body: CreateExportRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RunResponse:
    """发起导出，返回 202 + Run（客户端经 /runs/{id} 轮询状态、SSE 订阅进度）。

    导出选择（kinds / format / 显式版本）随 Run 的 config_snapshot 传给 Worker，
    由 ExportService 在后台完成组装 → 序列化 → 落盘 → ExportFile Artifact。
    """
    # 项目存在校验（404 PROJECT_NOT_FOUND）
    await _project_svc.get(db, project_id)

    config_snapshot: dict[str, Any] = {
        "options": {
            "kinds": body.kinds,
            "format": body.format,
            "artifact_ids": body.artifact_ids,
        }
    }
    run = await _run_svc.create_run(
        db,
        project_id=project_id,
        action="export",
        config=config_snapshot,
        idempotency_key=body.idempotency_key,
    )

    await db.commit()
    # 异步启动后台 Worker（best effort，不阻塞响应）
    schedule_worker(run.id, "export", config_snapshot)

    return RunResponse.from_orm(run)


@router.get(
    "/exports/{artifact_id}/download",
    responses={
        200: {"description": "文件内容（Content-Disposition 指定下载文件名）"},
        403: {"description": "导出文件不属于指定项目（CROSS_PROJECT_ACCESS）"},
        404: {"description": "导出文件不存在或已丢失（EXPORT_FILE_MISSING）"},
    },
)
async def download_export(
    artifact_id: uuid.UUID,
    project_id: Annotated[
        uuid.UUID, Query(description="导出文件所属项目（归属校验）")
    ],
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """下载导出文件。

    校验顺序（同步拒绝）：
    1. Artifact 存在且为 export_file（404 EXPORT_FILE_MISSING）;
    2. Artifact 属于指定 project_id（403 CROSS_PROJECT_ACCESS）;
    3. FileStore 中文件仍存在（404 EXPORT_FILE_MISSING，孤儿/被清理场景）。
    """
    # 1. Artifact 存在 + 类型校验
    try:
        artifact = await _artifact_svc.get_version(db, artifact_id)
    except NotFoundError:
        raise ExportFileMissingError(
            detail=f"导出文件不存在: {artifact_id}",
        ) from None
    if artifact.type != "export_file":
        raise ExportFileMissingError(
            detail=f"Artifact 不是导出文件: {artifact_id}",
        )

    # 2. 项目归属校验（跨项目 403）
    if artifact.project_id != project_id:
        raise AppError(
            detail="不允许访问其它项目的导出文件",
            status_code=403,
            code="CROSS_PROJECT_ACCESS",
        )

    # 3. 从 FileStore 读取（content 已通过 ExportFileContent 校验才能入库）
    content = ExportFileContent.model_validate(artifact.content)
    settings: Settings = get_settings(request)
    store: FileStore = LocalFileStore(root=settings.export_file_root)
    try:
        data = await store.open(content.storage_key)
    except FileNotFoundError:
        raise ExportFileMissingError(
            detail=f"导出文件已丢失: {artifact_id}",
        ) from None

    # 安全 Content-Disposition：filename 已清洗，中文走 filename*（RFC 5987）
    filename = content.filename
    ascii_name = _ASCII_SAFE_RE.sub("_", filename) or "export"
    content_disposition = (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )
    return Response(
        content=data,
        media_type=_MEDIA_TYPES.get(content.format, "application/octet-stream"),
        headers={"Content-Disposition": content_disposition},
    )
