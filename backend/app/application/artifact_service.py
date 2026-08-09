"""ArtifactService — Artifact 应用服务。

负责：
- Schema 校验：写入前用 Pydantic 模型验证 content
- 存储编排：调用 ArtifactStore 完成持久化
- 响应转换：ORM 模型 → API Response

Schema 校验失败时仍保存记录（status="invalid"），
但 get_latest 只返回 status="valid" 的版本。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.artifacts.store import ArtifactStore
from app.core.errors import NotFoundError
from app.domain.continuity import ContinuityState
from app.domain.enums import ArtifactType
from app.domain.evaluation import EvaluationReport
from app.domain.outline import EpisodeOutlineSet
from app.domain.revision import ContinuityCheckResult, RevisionPlan
from app.domain.script import ScriptDraft
from app.domain.story_bible import StoryBible

# ArtifactType → Pydantic Schema 映射
_SCHEMA_MAP: dict[str, Any] = {
    ArtifactType.STORY_BIBLE: StoryBible,
    ArtifactType.EPISODE_OUTLINE_SET: EpisodeOutlineSet,
    ArtifactType.SCRIPT_DRAFT: ScriptDraft,
    ArtifactType.EVALUATION_REPORT: EvaluationReport,
    ArtifactType.REVISION_PLAN: RevisionPlan,
    ArtifactType.CONTINUITY_CHECK: ContinuityCheckResult,
    ArtifactType.CONTINUITY_STATE: ContinuityState,
}


# ---- API Response Schemas（复用 domain 层的模式） ----

class ArtifactResponse:
    """Artifact API 响应（简化版 — 只序列化需要字段）。"""

    def __init__(self, a: Any) -> None:
        self.id: uuid.UUID = a.id
        self.project_id: uuid.UUID = a.project_id
        self.type: str = a.type
        self.version: int = a.version
        self.episode_number: int = a.episode_number
        self.status: str = a.status
        self.content: dict[str, Any] = a.content
        self.content_schema_version: str = a.content_schema_version
        self.prompt_version: str = a.prompt_version
        self.input_hash: str | None = a.input_hash
        self.checksum: str | None = a.checksum
        self.source_artifact_ids: list[dict[str, Any]] | None = a.source_artifact_ids
        self.created_at: Any = a.created_at
        self.updated_at: Any = a.updated_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "project_id": str(self.project_id),
            "type": self.type,
            "version": self.version,
            "episode_number": self.episode_number,
            "status": self.status,
            "content": self.content,
            "content_schema_version": self.content_schema_version,
            "prompt_version": self.prompt_version,
            "input_hash": self.input_hash,
            "checksum": self.checksum,
            "source_artifact_ids": self.source_artifact_ids,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ---- Service ----

class ArtifactService:
    """Artifact 应用服务。

    封装 Schema 校验 + 存储操作，
    API 层通过此服务间接访问 ArtifactStore。
    """

    def __init__(self) -> None:
        self._store = ArtifactStore()

    def _validate_content(self, artifact_type: str, content: dict[str, Any]) -> str:
        """校验 content 是否通过对应 Pydantic Schema。

        Returns:
            status: "valid" 或 "invalid"
        """
        schema_class = _SCHEMA_MAP.get(artifact_type)
        if schema_class is None:
            # 未知类型（如 normalized_requirement）不做深度校验
            return "valid"
        try:
            schema_class.model_validate(content)
            return "valid"
        except Exception:
            return "invalid"

    # ---- 写入 ----

    async def create_validated_artifact(
        self,
        db: AsyncSession,
        *,
        project_id: uuid.UUID,
        artifact_type: str,
        episode_number: int = 1,
        content: dict[str, Any],
        content_schema_version: str = "1.0",
        prompt_version: str = "",
        source_artifact_ids: list[dict[str, Any]] | None = None,
        dedup_extra: str = "",
    ) -> ArtifactResponse:
        """创建经过 Schema 校验的 Artifact。

        流程：
        1. 校验 content → 确定 status
        2. 调用 ArtifactStore.create 持久化
        3. 返回 ArtifactResponse

        校验失败时 status="invalid"，记录仍保存（诊断用途）。
        """
        status = self._validate_content(artifact_type, content)

        artifact = await self._store.create(
            db,
            project_id=project_id,
            artifact_type=artifact_type,
            episode_number=episode_number,
            content=content,
            status=status,
            content_schema_version=content_schema_version,
            prompt_version=prompt_version,
            source_artifact_ids=source_artifact_ids,
            dedup_extra=dedup_extra,
        )
        return ArtifactResponse(artifact)

    # ---- 查询 ----

    async def get_latest(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        artifact_type: str,
        episode_number: int = 1,
    ) -> ArtifactResponse:
        """获取最新 valid 版本。"""
        artifact = await self._store.get_latest(db, project_id, artifact_type, episode_number)
        if artifact is None:
            raise NotFoundError(
                detail=f"Artifact 不存在: project={project_id} type={artifact_type} ep={episode_number}",
                code="ARTIFACT_NOT_FOUND",
            )
        return ArtifactResponse(artifact)

    async def get_version(self, db: AsyncSession, artifact_id: uuid.UUID) -> ArtifactResponse:
        """获取指定版本。"""
        artifact = await self._store.get_version(db, artifact_id)
        return ArtifactResponse(artifact)

    async def list_versions(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        artifact_type: str,
        episode_number: int = 1,
    ) -> list[ArtifactResponse]:
        """获取版本历史。"""
        artifacts = await self._store.list_versions(db, project_id, artifact_type, episode_number)
        return [ArtifactResponse(a) for a in artifacts]

    async def list_by_project(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        artifact_type: str | None = None,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        """按项目分页查询。"""
        artifacts = await self._store.list_by_project(
            db, project_id, artifact_type, offset=offset, limit=limit
        )
        return {
            "items": [ArtifactResponse(a).to_dict() for a in artifacts],
            "total": len(artifacts),
            "offset": offset,
            "limit": limit,
        }

    async def get_source_links(self, db: AsyncSession, artifact_id: uuid.UUID) -> list[dict[str, Any]]:
        """查询 Artifact 的源依赖。"""
        links = await self._store.get_source_links(db, artifact_id)
        return [
            {
                "id": str(link.id),
                "source_id": str(link.source_id),
                "target_id": str(link.target_id),
                "relation": link.relation,
            }
            for link in links
        ]

    async def find_referencing_artifacts(
        self,
        db: AsyncSession,
        target_id: uuid.UUID,
        *,
        relation: str | None = None,
        artifact_type: str | None = None,
    ) -> list[ArtifactResponse]:
        """查询反向引用指定 Artifact 的所有 Artifact (F-06)。

        沿 ArtifactLink.target_id 反查下游产物，供修订结果链解析。
        """
        artifacts = await self._store.find_referencing_artifacts(
            db, target_id, relation=relation, artifact_type=artifact_type
        )
        return [ArtifactResponse(a) for a in artifacts]
