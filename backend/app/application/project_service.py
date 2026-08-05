"""ProjectService — 项目用例编排。

负责项目 CRUD 的业务校验和事务边界，
不包含数据库实现细节（细节在 Repository 中）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import func
from sqlalchemy import select as _select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.models.project import Project
from app.db.repositories.base import BaseRepository
from app.domain.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
)


def _to_response(p: Project) -> ProjectResponse:
    """将 ORM 模型转换为 API 响应模型。"""
    return ProjectResponse(
        id=p.id,
        title=p.title,
        status=p.status,
        target_episode_count=p.target_episode_count,
        current_episode_count=p.current_episode_count,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


class ProjectService:
    """项目应用服务。

    提供项目的创建、查询、列表、更新业务操作，
    所有方法接收 AsyncSession 以支持调用方控制事务边界。
    """

    def __init__(self) -> None:
        self._repo: BaseRepository | None = None

    def _get_repo(self, db: AsyncSession) -> BaseRepository:
        """懒初始化 Repository（复用同一会话的实例）。"""
        if self._repo is None or self._repo.session is not db:
            self._repo = BaseRepository(db, Project)
        return self._repo

    async def create(self, db: AsyncSession, data: ProjectCreate) -> ProjectResponse:
        """创建新项目。

        Args:
            db: 数据库异步会话
            data: 创建请求体

        Returns:
            创建成功的项目响应
        """
        project = Project(
            title=data.title,
            target_episode_count=data.target_episode_count,
            status="draft",
        )
        repo = self._get_repo(db)
        saved = await repo.add(project)
        return _to_response(saved)

    async def get(self, db: AsyncSession, project_id: uuid.UUID) -> ProjectResponse:
        """按 ID 查询项目。

        Raises:
            NotFoundError: 项目不存在或已被软删除
        """
        repo = self._get_repo(db)
        project = await repo.get(project_id)
        if project is None or project.deleted_at is not None:
            raise NotFoundError(detail=f"项目不存在: {project_id}", code="PROJECT_NOT_FOUND")
        return _to_response(project)

    async def list(
        self,
        db: AsyncSession,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> ProjectListResponse:
        """分页查询项目列表（不含已软删除），按创建时间倒序。"""
        repo = self._get_repo(db)

        # 获取真实总数（不含软删除）
        count_stmt = (
            _select(func.count())
            .select_from(Project)
            .where(Project.deleted_at.is_(None))
        )
        count_result = await db.execute(count_stmt)
        real_total: int = count_result.scalar_one()

        items = await repo.list(
            offset=offset,
            limit=limit,
            order_by=Project.created_at.desc(),
        )
        # 过滤软删除的项目
        active = [p for p in items if p.deleted_at is None]
        return ProjectListResponse(
            items=[_to_response(p) for p in active],
            total=real_total,
            offset=offset,
            limit=limit,
        )

    async def update(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        data: ProjectUpdate,
    ) -> ProjectResponse:
        """更新项目字段（部分更新）。

        只更新请求中传入的非 None 字段。
        不存在或已软删除的项目抛出 NotFoundError。
        """
        repo = self._get_repo(db)
        project = await repo.get(project_id)
        if project is None or project.deleted_at is not None:
            raise NotFoundError(detail=f"项目不存在: {project_id}", code="PROJECT_NOT_FOUND")

        if data.title is not None:
            project.title = data.title
        if data.target_episode_count is not None:
            project.target_episode_count = data.target_episode_count
        if data.status is not None:
            project.status = data.status

        saved = await repo.update(project)
        return _to_response(saved)
