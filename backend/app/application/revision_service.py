"""RevisionService — 修订计划应用服务 (F-01).

负责:
- 从评估报告中确定性选出待修订集（select_revision_candidate，不调用 LLM）
- 追溯原稿剧本与 StoryBible 锁定事实
- 调用 RevisionPlanSkill 生成有据可依的修订计划
- 持久化 revision_plan Artifact（绑定原稿/评估/设定版本）

模块边界:
- Service 只做用例编排与事务边界，不直接操作 LLM 细节
- LLM 通过注入的 BaseAgent 调用（Skill 内部负责）
- 跨项目防护:不允许对其它项目的 Artifact 生成修订计划
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactResponse, ArtifactService
from app.artifacts.store import ArtifactStore
from app.core.errors import AppError
from app.domain.evaluation import EvaluationReport
from app.domain.revision import (
    RevisionPlan,
    RevisionPlanInput,
    select_revision_candidate,
)
from app.domain.script import ScriptDraft
from app.prompts.loader import PromptLoader
from app.skills.revision_plan import RevisionPlanSkill

logger = logging.getLogger(__name__)

_REL_DERIVED = "derived_from"
_REL_REFERENCE = "references"


class RevisionService:
    """修订计划应用服务。

    build_revision_plan 由工作流节点或 API 调用方提供
    BaseAgent 与 PromptLoader（保持无 LLM 依赖）。
    """

    def __init__(self) -> None:
        self._artifact_svc = ArtifactService()
        self._store = ArtifactStore()
        self._skill = RevisionPlanSkill()

    # ---- 修订计划编排 ----

    async def build_revision_plan(
        self,
        db: AsyncSession,
        *,
        project_id: uuid.UUID,
        evaluation_reports: list[ArtifactResponse],
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> ArtifactResponse | None:
        """从评估报告选出最低分集并生成修订计划。

        流程:
        1. 解析评估报告（服务端回填的 overall/need_revision 已持久化）;
        2. 确定性选集——无 need_revision 集时直接返回 None（不修订）;
        3. 跨项目防护 + 追溯原稿与 StoryBible 锁定事实;
        4. 调用 RevisionPlanSkill 生成有据可依的计划;
        5. 持久化 revision_plan Artifact（绑定原稿/评估/设定版本）。

        Args:
            evaluation_reports: 各集最新评估报告 Artifact（可无序）;
            agent: 用于 LLM 调用的 BaseAgent;
            prompt_loader: Prompt 模板加载器。

        Returns:
            revision_plan Artifact；无 need_revision 集时返回 None。

        Raises:
            AppError(CROSS_PROJECT_ACCESS): 原稿不属于当前项目。
        """
        if not evaluation_reports:
            return None

        # 1. 解析报告
        reports = [
            EvaluationReport.model_validate(r.content) for r in evaluation_reports
        ]

        # 2. 确定性选集（纯函数，不调用 LLM）
        selected = select_revision_candidate(reports)
        if selected is None:
            logger.info("全部评估通过，无需修订")
            return None

        # 3. 定位原稿与评估 Artifact，跨项目防护
        eval_artifact = self._find_by_episode(
            evaluation_reports, selected.episode_number
        )
        script_artifact = await self._artifact_svc.get_version(
            db, selected.script_artifact_id
        )
        if script_artifact.project_id != project_id:
            raise AppError(
                detail="不允许对其它项目的 Artifact 生成修订计划",
                status_code=403,
                code="CROSS_PROJECT_ACCESS",
            )

        # StoryBible 提供权威锁定事实
        story_bible_artifact = await self._get_latest_optional(
            db, project_id, "story_bible"
        )
        locked_facts = (
            story_bible_artifact.content.get("locked_facts", [])
            if story_bible_artifact is not None
            else []
        )

        # 4. 生成有据可依的修订计划
        script = ScriptDraft.model_validate(script_artifact.content)
        plan_input = RevisionPlanInput(
            episode_number=selected.episode_number,
            source_script_artifact_id=selected.script_artifact_id,
            source_evaluation_artifact_id=eval_artifact.id,
            script_draft=script,
            evaluation_report=selected,
            locked_facts=list(locked_facts),
        )
        plan: RevisionPlan = await self._skill.execute(
            {
                "input": plan_input,
                "agent": agent,
                "prompt_loader": prompt_loader,
            }
        )

        # 5. 持久化（input_hash 幂等兜底）
        return await self._artifact_svc.create_validated_artifact(
            db,
            project_id=project_id,
            artifact_type="revision_plan",
            episode_number=selected.episode_number,
            content=plan.model_dump(mode="json"),
            prompt_version=prompt_loader.get("revision_plan").version,
            source_artifact_ids=self._build_sources(
                script_artifact, eval_artifact, story_bible_artifact
            ),
        )

    # ---- 查询 ----

    async def list_project_revision_plans(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
    ) -> list[ArtifactResponse]:
        """列出项目的全部修订计划，按集号升序、版本升序排列。"""
        artifacts = await self._store.list_by_project(
            db, project_id, "revision_plan", offset=0, limit=1000
        )
        items = [ArtifactResponse(a) for a in artifacts]
        items.sort(key=lambda a: (a.episode_number, a.version))
        return items

    # ---- 内部辅助 ----

    async def _get_latest_optional(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        artifact_type: str,
    ) -> ArtifactResponse | None:
        """获取最新 valid 版本；不存在时返回 None（不抛异常）。"""
        artifact = await self._store.get_latest(db, project_id, artifact_type)
        if artifact is None:
            return None
        return ArtifactResponse(artifact)

    @staticmethod
    def _find_by_episode(
        reports: list[ArtifactResponse],
        episode_number: int,
    ) -> ArtifactResponse:
        """在评估报告列表中定位指定集的报告。"""
        for r in reports:
            if r.episode_number == episode_number:
                return r
        raise AppError(
            detail=f"未找到第 {episode_number} 集的评估报告",
            status_code=404,
            code="EVALUATION_NOT_FOUND",
        )

    @staticmethod
    def _build_sources(
        script_artifact: ArtifactResponse,
        eval_artifact: ArtifactResponse,
        story_bible_artifact: ArtifactResponse | None,
    ) -> list[dict[str, Any]]:
        """构造修订计划的 source_artifact_ids（绑定原稿/评估/设定版本）。"""
        sources: list[dict[str, Any]] = [
            {
                "artifact_id": str(script_artifact.id),
                "version": script_artifact.version,
                "relation": _REL_DERIVED,
            },
            {
                "artifact_id": str(eval_artifact.id),
                "version": eval_artifact.version,
                "relation": _REL_DERIVED,
            },
        ]
        if story_bible_artifact is not None:
            sources.append(
                {
                    "artifact_id": str(story_bible_artifact.id),
                    "version": story_bible_artifact.version,
                    "relation": _REL_REFERENCE,
                }
            )
        return sources
