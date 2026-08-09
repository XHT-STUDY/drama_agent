"""ArtifactStore — 不可变 Artifact 存储。

核心职责：
- 创建新版本（永不 UPDATE 已有 content）
- 查询最新 valid 版本、指定版本、按项目列表
- 事务内分配版本号，利用 DB 唯一约束防并发冲突
- 管理 artifact_links 依赖关系
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.artifacts.versions import compute_checksum, compute_input_hash, compute_next_version
from app.core.errors import NotFoundError
from app.db.models.artifact import Artifact
from app.db.repositories.artifacts import ArtifactRepository


class ArtifactStore:
    """不可变 Artifact 存储。

    设计原则：
    - 所有创建操作 INSERT 新行，永不 UPDATE content 字段
    - 版本号在事务内通过 SELECT MAX(version) + 1 分配
    - 并发冲突由 DB 唯一约束 (project_id, type, episode_number, version) 兜底
    - input_hash 用于幂等去重
    """

    def __init__(self) -> None:
        self._repo: ArtifactRepository | None = None

    def _get_repo(self, db: AsyncSession) -> ArtifactRepository:
        """获取或创建当前会话的 Repository 实例。"""
        if self._repo is None or self._repo.session is not db:
            self._repo = ArtifactRepository(db)
        return self._repo

    # ---- 创建 ----

    async def create(
        self,
        db: AsyncSession,
        *,
        project_id: uuid.UUID,
        artifact_type: str,
        episode_number: int = 1,
        content: dict[str, Any],
        status: str = "draft",
        content_schema_version: str = "1.0",
        prompt_version: str = "",
        source_artifact_ids: list[dict[str, Any]] | None = None,
    ) -> Artifact:
        """创建新 Artifact 版本。

        步骤：
        1. 计算 checksum 和 input_hash
        2. 事务内分配版本号
        3. INSERT 新记录
        4. 如有 source，INSERT artifact_links

        并发冲突时（IntegrityError on unique constraint）重新查询版本号重试。

        Returns:
            已持久化的 Artifact ORM 实例
        """
        checksum = compute_checksum(content)
        input_hash = compute_input_hash(
            source_artifact_ids,
            episode_number=episode_number,
            artifact_type=artifact_type,
        )

        repo = self._get_repo(db)

        # 幂等检查：相同 input_hash 返回已有记录
        if input_hash is not None:
            existing = await repo.find_by_input_hash(input_hash)
            if existing is not None:
                return existing

        # 事务内分配版本号
        current_max = await repo.get_max_version(project_id, artifact_type, episode_number)
        version = compute_next_version(current_max)

        artifact = Artifact(
            project_id=project_id,
            type=artifact_type,
            episode_number=episode_number,
            version=version,
            content=content,
            status=status,
            content_schema_version=content_schema_version,
            prompt_version=prompt_version,
            input_hash=input_hash,
            checksum=checksum,
            source_artifact_ids=source_artifact_ids,
        )

        try:
            await repo.add(artifact)

            # 创建依赖关系
            if source_artifact_ids:
                for src in source_artifact_ids:
                    src_id = uuid.UUID(src["artifact_id"])
                    rel = src.get("relation", "derived_from")
                    await repo.create_link(
                        source_id=artifact.id,
                        target_id=src_id,
                        relation=rel,
                    )
        except IntegrityError:
            # 并发冲突：重新读取版本号并重试
            await db.rollback()
            current_max = await repo.get_max_version(project_id, artifact_type, episode_number)
            artifact.version = compute_next_version(current_max)
            await repo.add(artifact)

        return artifact

    # ---- 查询 ----

    async def get_latest(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        artifact_type: str,
        episode_number: int = 1,
    ) -> Artifact | None:
        """获取最新 valid 版本。"""
        return await self._get_repo(db).get_latest_valid(project_id, artifact_type, episode_number)

    async def get_version(self, db: AsyncSession, artifact_id: uuid.UUID) -> Artifact:
        """按 ID 获取指定版本。

        Raises:
            NotFoundError: Artifact 不存在
        """
        repo = self._get_repo(db)
        artifact: Artifact | None = await repo.get(artifact_id)
        if artifact is None:
            raise NotFoundError(detail=f"Artifact 不存在: {artifact_id}", code="ARTIFACT_NOT_FOUND")
        return artifact

    async def list_versions(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        artifact_type: str,
        episode_number: int = 1,
    ) -> list[Artifact]:
        """获取指定 (project, type, episode) 的所有版本历史。"""
        return await self._get_repo(db).list_versions(project_id, artifact_type, episode_number)

    async def list_by_project(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        artifact_type: str | None = None,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> list[Artifact]:
        """按项目分页查询 Artifact 列表。"""
        return await self._get_repo(db).list_by_project(project_id, artifact_type, offset=offset, limit=limit)

    async def find_by_input_hash(self, db: AsyncSession, input_hash: str) -> Artifact | None:
        """按 input_hash 幂等查询。"""
        return await self._get_repo(db).find_by_input_hash(input_hash)

    async def get_source_links(self, db: AsyncSession, artifact_id: uuid.UUID) -> list[Any]:
        """查询 Artifact 的来源依赖关系。"""
        return await self._get_repo(db).get_source_links(artifact_id)
