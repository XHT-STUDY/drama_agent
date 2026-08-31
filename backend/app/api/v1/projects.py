"""Project API 路由 — 项目 CRUD。

端点：
- POST   /projects         创建项目
- GET    /projects         分页查询项目列表
- GET    /projects/{id}    查询单个项目
- PATCH  /projects/{id}    更新项目
- DELETE /projects/{id}    删除项目（软删除，幂等）
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.application.project_service import ProjectService
from app.domain.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["projects"])
_service = ProjectService()


@router.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_project(
    body: ProjectCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectResponse:
    """创建新项目。"""
    project = await _service.create(db, body)
    logger.info("创建项目: project=%s title=%.60s", project.id, project.title)
    return project


@router.get("/projects", response_model=ProjectListResponse)
async def list_projects(
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ProjectListResponse:
    """分页查询项目列表。"""
    return await _service.list(db, offset=offset, limit=limit)


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectResponse:
    """按 ID 查询项目详情。"""
    return await _service.get(db, project_id)


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectResponse:
    """部分更新项目字段。"""
    project = await _service.update(db, project_id, body)
    logger.info("更新项目: project=%s 字段=%s", project_id, list(body.model_dump(exclude_unset=True)))
    return project


@router.delete("/projects/{project_id}", response_model=dict[str, bool | str])
async def delete_project(
    project_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, bool | str]:
    """软删除项目（幂等；已删除的项目再次删除仍返回 200）。"""
    deleted = await _service.delete(db, project_id)
    await db.commit()
    logger.info("删除项目: project=%s deleted=%s", project_id, deleted)
    return {"deleted": True, "project_id": str(project_id)}
