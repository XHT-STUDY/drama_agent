"""Artifact API 路由 — 不可变资产查询。

端点：
- GET  /projects/{project_id}/artifacts/latest?type=&episode=1  获取最新版本
- GET  /projects/{project_id}/artifacts                         按项目列表
- GET  /artifacts/{artifact_id}                                 获取指定版本
- GET  /artifacts/{artifact_id}/versions                        版本历史
- GET  /artifacts/{artifact_id}/links                           源依赖查询
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.application.artifact_service import ArtifactService

router = APIRouter(tags=["artifacts"])
_service = ArtifactService()


@router.get("/projects/{project_id}/artifacts/latest")
async def get_latest_artifact(
    project_id: uuid.UUID,
    type: Annotated[str, Query(alias="type", description="Artifact 类型")],
    db: Annotated[AsyncSession, Depends(get_db)],
    episode: Annotated[int, Query(ge=1)] = 1,
) -> dict[str, Any]:
    """获取指定项目/类型/集数的最新 valid Artifact。"""
    result = await _service.get_latest(db, project_id, type, episode_number=episode)
    return result.to_dict()


@router.get("/projects/{project_id}/artifacts")
async def list_artifacts(
    project_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    type: Annotated[str | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """按项目分页查询 Artifact 列表（可选按类型过滤）。"""
    return await _service.list_by_project(db, project_id, type, offset=offset, limit=limit)


@router.get("/artifacts/{artifact_id}")
async def get_artifact(
    artifact_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """获取指定版本的 Artifact。"""
    result = await _service.get_version(db, artifact_id)
    return result.to_dict()


@router.get("/artifacts/{artifact_id}/versions")
async def list_artifact_versions(
    artifact_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict[str, Any]]:
    """获取指定 Artifact 的所有版本历史。"""
    artifact = await _service.get_version(db, artifact_id)
    versions = await _service.list_versions(
        db, artifact.project_id, artifact.type, artifact.episode_number
    )
    return [v.to_dict() for v in versions]


@router.get("/artifacts/{artifact_id}/links")
async def get_artifact_links(
    artifact_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict[str, Any]]:
    """查询 Artifact 的源依赖关系。"""
    return await _service.get_source_links(db, artifact_id)
