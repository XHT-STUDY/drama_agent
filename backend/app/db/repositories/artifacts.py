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

    async def find_evaluation_for_script(
        self,
        project_id: uuid.UUID,
        script_artifact_id: uuid.UUID,
    ) -> Artifact | None:
        """查找绑定到指定 Script 版本的评估报告 (E-03)。

        评估报告 content.script_artifact_id 记录被评估的剧本版本，
        通过 JSONB 字段匹配返回最新 valid 版本。修订后产生新剧本版本
        时会指向新的 script_artifact_id，因此原稿评估不会被覆盖。
        """
        stmt: Any = (
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.type == "evaluation_report",
                Artifact.status == "valid",
                Artifact.content["script_artifact_id"].astext == str(script_artifact_id),
            )
            .order_by(Artifact.version.desc())
            .limit(1)
        )
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

    async def find_referencing_artifacts(
        self,
        target_id: uuid.UUID,
        *,
        relation: str | None = None,
        artifact_type: str | None = None,
    ) -> list[Artifact]:
        """反向依赖查询：以指定 Artifact 为 target 的所有 Artifact (F-06)。

        用于从修订计划沿 ArtifactLink 链解析候选稿 / 连续性检查结果等下游产物。
        JOIN Artifact 过滤类型，按 target 去重（同一 Artifact 可能有多条链接），
        再按 version 升序排列。

        Args:
            target_id: 被引用的 Artifact ID（ArtifactLink.target_id）。
            relation: 可选——只返回指定 relation（如 "revises" / "derived_from"）。
            artifact_type: 可选——只返回指定类型的 Artifact（如 "script_draft"）。

        Returns:
            反向引用该 Artifact 的 Artifact 列表（按 version 升序、去重）。
        """
        stmt: Any = (
            select(Artifact)
            .join(ArtifactLink, ArtifactLink.source_id == Artifact.id)
            .where(ArtifactLink.target_id == target_id)
        )
        if relation is not None:
            stmt = stmt.where(ArtifactLink.relation == relation)
        if artifact_type is not None:
            stmt = stmt.where(Artifact.type == artifact_type)
        stmt = stmt.order_by(Artifact.version.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().unique().all())
