"""ArtifactRepository — Artifact 专用数据访问。

在 BaseRepository 基础上扩展：
- 按项目/类型/集数查询最新版本
- 版本历史列表
- input_hash 幂等查询
- 版本号自增
- artifact_links 管理
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.artifact import Artifact
from app.db.models.artifact_link import ArtifactLink
from app.db.repositories.base import BaseRepository


class ArtifactRepository(BaseRepository):
    """Artifact + ArtifactLink 专用 Repository。

    封装不可变版本模型的查询逻辑。
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Artifact)

    # ---- 查询 ----

    async def get_latest_valid(
        self,
        project_id: uuid.UUID,
        artifact_type: str,
        episode_number: int = 1,
    ) -> Artifact | None:
        """获取指定 (project, type, episode) 的最新 valid 版本。

        按 version DESC 取第一条 status='valid' 的记录。
        """
        stmt: Any = (
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.type == artifact_type,
                Artifact.episode_number == episode_number,
                Artifact.status == "valid",
            )
            .order_by(Artifact.version.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_versions(
        self,
        project_id: uuid.UUID,
        artifact_type: str,
        episode_number: int = 1,
    ) -> list[Artifact]:
        """列出指定 (project, type, episode) 的所有版本（按 version ASC）。"""
        stmt: Any = (
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.type == artifact_type,
                Artifact.episode_number == episode_number,
            )
            .order_by(Artifact.version.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_by_input_hash(self, input_hash: str) -> Artifact | None:
        """按 input_hash 查找已有 Artifact（幂等去重）。"""
        stmt: Any = select(Artifact).where(Artifact.input_hash == input_hash).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_project(
        self,
        project_id: uuid.UUID,
        artifact_type: str | None = None,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> list[Artifact]:
        """按项目分页查询 Artifact 列表（可选过滤类型）。"""
        stmt: Any = select(Artifact).where(Artifact.project_id == project_id)
        if artifact_type is not None:
            stmt = stmt.where(Artifact.type == artifact_type)
        stmt = stmt.order_by(Artifact.created_at.desc()).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ---- 版本号 ----

    async def get_max_version(
        self,
        project_id: uuid.UUID,
        artifact_type: str,
        episode_number: int,
    ) -> int | None:
        """获取指定 (project, type, episode) 的当前最大版本号。"""
        stmt: Any = (
            select(func.max(Artifact.version))
            .where(
                Artifact.project_id == project_id,
                Artifact.type == artifact_type,
                Artifact.episode_number == episode_number,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ---- Artifact Links ----

    async def create_link(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        relation: str,
    ) -> ArtifactLink:
        """创建 Artifact 依赖关系。"""
        link = ArtifactLink(source_id=source_id, target_id=target_id, relation=relation)
        self.session.add(link)
        await self.session.flush()
        return link

    async def get_source_links(self, artifact_id: uuid.UUID) -> list[ArtifactLink]:
        """查询以指定 Artifact 为 source 的所有依赖关系。"""
        stmt: Any = (
            select(ArtifactLink)
            .where(ArtifactLink.source_id == artifact_id)
            .order_by(ArtifactLink.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
