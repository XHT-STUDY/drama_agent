"""Artifact API 路由 — 不可变资产查询。

端点：
- GET  /projects/{project_id}/artifacts/latest?type=&episode=1  获取最新版本
- GET  /projects/{project_id}/artifacts                         按项目列表
- GET  /artifacts/diff?from_artifact_id=&to_artifact_id=        两版本 Diff
- GET  /artifacts/{artifact_id}                                 获取指定版本
- GET  /artifacts/{artifact_id}/versions                        版本历史
- GET  /artifacts/{artifact_id}/links                           源依赖查询
- GET  /artifacts/{artifact_id}/references                      反向引用查询（J-08）
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.application.artifact_service import ArtifactService
from app.artifacts.diff_service import DiffService

router = APIRouter(tags=["artifacts"])
_service = ArtifactService()
_diff_service = DiffService()


@router.get("/artifacts/diff")
async def diff_artifacts(
    from_artifact_id: Annotated[uuid.UUID, Query(description="旧版本（from）Artifact ID")],
    to_artifact_id: Annotated[uuid.UUID, Query(description="新版本（to）Artifact ID")],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """两版本 Diff：from → to 的变化（场景感知，无法解析时回退全文行 diff）。"""
    result = await _diff_service.diff_artifacts(
        db, from_artifact_id=from_artifact_id, to_artifact_id=to_artifact_id
    )
    return result.model_dump(mode="json")


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


@router.get("/artifacts/{artifact_id}/references")
async def get_artifact_references(
    artifact_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    relation: Annotated[str | None, Query(description="来源关系过滤，如 derived_from")] = None,
    type: Annotated[str | None, Query(alias="type", description="引用方类型过滤")] = None,
) -> list[dict[str, Any]]:
    """查询反向引用指定 Artifact 的产物（J-08）。

    例：GET /artifacts/{outline_id}/references?relation=derived_from&type=script_draft
    → 仍引用旧大纲的剧本列表（大纲修订后判断哪些剧本需要跟进）。
    """
    await _service.get_version(db, artifact_id)  # 404 语义与其它端点一致
    artifacts = await _service.find_referencing_artifacts(
        db, artifact_id, relation=relation, artifact_type=type
    )
    return [a.to_dict() for a in artifacts]
