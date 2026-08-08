"""DiffService — F-04 Diff 编排服务。

取两版本 Artifact → 校验（跨项目 / 类型 / 集数）→ 解析 ScriptDraft → 场景 diff；
content 无法解析为 ScriptDraft 时回退全文行 diff（mode="line"）。
纯确定性，不调用 LLM；diff 结果不持久化（计算型查询）。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.artifacts.store import ArtifactStore
from app.core.errors import AppError
from app.domain.diff import ScriptDiff
from app.domain.script import ScriptDraft
from app.tools.diff import diff_script_drafts, diff_texts

_SCRIPT_DRAFT_TYPE = "script_draft"


class DiffService:
    """Diff 应用服务。

    依赖：ArtifactStore（版本查询）。API 层经模块级单例使用。
    """

    def __init__(self) -> None:
        self._store = ArtifactStore()

    async def diff_artifacts(
        self,
        db: AsyncSession,
        *,
        from_artifact_id: uuid.UUID,
        to_artifact_id: uuid.UUID,
    ) -> ScriptDiff:
        """计算 from → to 两版本的 Diff。

        Args:
            db: 事务会话
            from_artifact_id: 旧版本（from）Artifact ID
            to_artifact_id: 新版本（to）Artifact ID

        Returns:
            ScriptDiff（mode="scene"；content 无法解析时回退 mode="line"）

        Raises:
            NotFoundError: Artifact 不存在（404 ARTIFACT_NOT_FOUND）
            AppError: 跨项目 / 非 script_draft / 不同集（400）
        """
        from_artifact = await self._store.get_version(db, from_artifact_id)
        to_artifact = await self._store.get_version(db, to_artifact_id)
        self._validate_pair(from_artifact, to_artifact)

        try:
            from_draft = ScriptDraft.model_validate(from_artifact.content)
            to_draft = ScriptDraft.model_validate(to_artifact.content)
            result = diff_script_drafts(from_draft, to_draft)
        except Exception:
            # content 非合法 ScriptDraft（status=invalid 或结构残缺）→ 全文行 diff 回退
            result = diff_texts(
                self._plain_text_or_str(from_artifact.content),
                self._plain_text_or_str(to_artifact.content),
            )

        return result.model_copy(
            update={
                "from_artifact_id": from_artifact.id,
                "to_artifact_id": to_artifact.id,
                "from_version": from_artifact.version,
                "to_version": to_artifact.version,
                "project_id": from_artifact.project_id,
                "episode_number": from_artifact.episode_number,
            }
        )

    def _validate_pair(self, from_artifact: Any, to_artifact: Any) -> None:
        """跨项目 / 类型 / 集数校验（不符即拒绝）。"""
        if from_artifact.project_id != to_artifact.project_id:
            raise AppError(
                status_code=400,
                code="CROSS_PROJECT_DIFF_FORBIDDEN",
                detail="跨项目 Diff 不允许",
            )
        if (
            from_artifact.type != _SCRIPT_DRAFT_TYPE
            or to_artifact.type != _SCRIPT_DRAFT_TYPE
        ):
            raise AppError(
                status_code=400,
                code="DIFF_UNSUPPORTED_TYPE",
                detail="Diff 仅支持 script_draft 类型",
            )
        if from_artifact.episode_number != to_artifact.episode_number:
            raise AppError(
                status_code=400,
                code="DIFF_EPISODE_MISMATCH",
                detail="Diff 的 from/to 必须是同一集",
            )

    @staticmethod
    def _plain_text_or_str(content: dict[str, Any]) -> str:
        """提取 plain_text；缺失时退化为整个 content 的 JSON 文本。"""
        plain_text = content.get("plain_text")
        if isinstance(plain_text, str) and plain_text:
            return plain_text
        return json.dumps(content, ensure_ascii=False)
