"""EvaluationService — 评估应用服务 (E-03).

负责:
- evaluate_script / evaluate_many:编排单集或多集评估
- 跨项目防护:不允许评估其他项目的 Artifact
- 版本绑定:评估报告绑定被评估的 Script 版本,修订不覆盖原稿评估
- 幂等复用:同一剧本版本重复评估返回已有报告(store input_hash 兜底)
- 查询:项目评估列表(按集/版本排序)、指定剧本版本的评估

模块边界:
- Service 只做用例编排与事务边界,不直接操作 LLM 细节
- LLM 通过注入的 EvaluationAgent 调用(E-04 workflow 提供)
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.evaluation import EvaluationAgent
from app.application.artifact_service import ArtifactResponse, ArtifactService
from app.artifacts.store import ArtifactStore
from app.core.errors import AppError
from app.db.repositories.artifacts import ArtifactRepository
from app.domain.script import ScriptDraft
from app.prompts.loader import PromptLoader

# source_artifact_ids 中的 relation 约定
_REL_DERIVED = "derived_from"
_REL_REFERENCE = "references"


class EvaluationService:
    """评估应用服务。

    evaluate_script 由工作流节点或 API 调用方提供
    EvaluationAgent 与 PromptLoader(保持无 LLM 依赖)。
    """

    def __init__(self) -> None:
        self._artifact_svc = ArtifactService()
        self._store = ArtifactStore()

    # ---- 评估编排 ----

    async def evaluate_script(
        self,
        db: AsyncSession,
        *,
        project_id: uuid.UUID,
        script_artifact_id: uuid.UUID,
        evaluator: EvaluationAgent,
        prompt_loader: PromptLoader,
    ) -> ArtifactResponse:
        """评估单个剧本版本并持久化评估报告。

        流程:
        1. 加载剧本并校验属于当前项目(跨项目防护);
        2. 幂等复用:同一剧本版本已有评估则直接返回;
        3. 追溯 outline / story_bible 作为评估上下文;
        4. 调用 EvaluationAgent 生成报告;
        5. 持久化 evaluation_report Artifact(绑定剧本版本)。

        Returns:
            评估报告 Artifact。

        Raises:
            AppError(CROSS_PROJECT_ACCESS): 剧本不属于当前项目。
            NotFoundError: 剧本不存在。
        """
        script_artifact = await self._artifact_svc.get_version(db, script_artifact_id)

        if script_artifact.project_id != project_id:
            raise AppError(
                detail="不允许评估其他项目的 Artifact",
                status_code=403,
                code="CROSS_PROJECT_ACCESS",
            )

        # 幂等复用:同一剧本版本已有有效评估直接返回
        existing = await self.get_evaluation_for_script(db, project_id, script_artifact_id)
        if existing is not None:
            return existing

        script = ScriptDraft.model_validate(script_artifact.content)
        outline_artifact, story_bible_artifact = await self._resolve_context(
            db, script_artifact
        )
        episode_outline = self._get_episode_outline(
            outline_artifact.content if outline_artifact else {}, script.episode_number
        )

        report = await evaluator.evaluate_episode(
            episode_number=script.episode_number,
            script_draft=script,
            episode_outline=episode_outline,
            story_bible=story_bible_artifact.content if story_bible_artifact else {},
            prompt_loader=prompt_loader,
            script_artifact_id=script_artifact_id,
        )

        return await self._artifact_svc.create_validated_artifact(
            db,
            project_id=project_id,
            artifact_type="evaluation_report",
            episode_number=script.episode_number,
            content=report.model_dump(mode="json"),
            prompt_version=prompt_loader.get("evaluate_episode").version,
            source_artifact_ids=self._build_sources(
                script_artifact, outline_artifact, story_bible_artifact
            ),
        )

    async def evaluate_many(
        self,
        db: AsyncSession,
        *,
        project_id: uuid.UUID,
        script_artifact_ids: list[uuid.UUID],
        evaluator: EvaluationAgent,
        prompt_loader: PromptLoader,
    ) -> list[ArtifactResponse]:
        """批量评估多个剧本版本，结果按集号升序返回。

        每集独立评估与持久化；单集失败不会伪装为完整评估
        (由调用方决定重试策略,本方法不吞异常)。

        Args:
            script_artifact_ids: 待评估的剧本 Artifact ID 列表(可无序)

        Returns:
            按 episode_number 升序的评估报告 Artifact 列表。
        """
        if not script_artifact_ids:
            return []

        # 保持调用方传入的顺序评估与返回（调用方负责按集号排序）
        results: list[ArtifactResponse] = []
        for sid in script_artifact_ids:
            results.append(
                await self.evaluate_script(
                    db,
                    project_id=project_id,
                    script_artifact_id=sid,
                    evaluator=evaluator,
                    prompt_loader=prompt_loader,
                )
            )
        return results

    # ---- 查询 ----

    async def list_project_evaluations(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
    ) -> list[ArtifactResponse]:
        """列出项目的全部评估报告，按集号升序、版本升序排列。"""
        artifacts = await self._store.list_by_project(
            db, project_id, "evaluation_report", offset=0, limit=1000
        )
        items = [ArtifactResponse(a) for a in artifacts]
        items.sort(key=lambda a: (a.episode_number, a.version))
        return items

    async def get_evaluation_for_script(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        script_artifact_id: uuid.UUID,
    ) -> ArtifactResponse | None:
        """查询绑定到指定剧本版本的评估报告。"""
        repo = ArtifactRepository(db)
        artifact = await repo.find_evaluation_for_script(project_id, script_artifact_id)
        if artifact is None:
            return None
        return ArtifactResponse(artifact)

    # ---- 内部辅助 ----

    async def _resolve_context(
        self,
        db: AsyncSession,
        script_artifact: ArtifactResponse,
    ) -> tuple[ArtifactResponse | None, ArtifactResponse | None]:
        """从剧本的 source 依赖追溯本集大纲与 StoryBible。"""
        outline_id: uuid.UUID | None = None
        story_bible_id: uuid.UUID | None = None
        for src in script_artifact.source_artifact_ids or []:
            relation = src.get("relation", "")
            if relation == _REL_DERIVED:
                outline_id = uuid.UUID(src["artifact_id"])
            elif relation == _REL_REFERENCE and story_bible_id is None:
                story_bible_id = uuid.UUID(src["artifact_id"])

        outline_artifact = (
            await self._artifact_svc.get_version(db, outline_id) if outline_id else None
        )
        story_bible_artifact = (
            await self._artifact_svc.get_version(db, story_bible_id)
            if story_bible_id
            else None
        )
        return outline_artifact, story_bible_artifact

    @staticmethod
    def _get_episode_outline(
        outline_content: dict[str, Any],
        episode_number: int,
    ) -> dict[str, Any]:
        """从大纲集合中取出目标集的大纲。"""
        for ep in outline_content.get("episodes", []):
            if isinstance(ep, dict) and ep.get("episode_number") == episode_number:
                return ep
        return {}

    @staticmethod
    def _build_sources(
        script_artifact: ArtifactResponse,
        outline_artifact: ArtifactResponse | None,
        story_bible_artifact: ArtifactResponse | None,
    ) -> list[dict[str, Any]]:
        """构造评估报告的 source_artifact_ids(绑定剧本/大纲/设定版本)。"""
        sources: list[dict[str, Any]] = [
            {
                "artifact_id": str(script_artifact.id),
                "version": script_artifact.version,
                "relation": _REL_DERIVED,
            }
        ]
        if outline_artifact is not None:
            sources.append(
                {
                    "artifact_id": str(outline_artifact.id),
                    "version": outline_artifact.version,
                    "relation": _REL_REFERENCE,
                }
            )
        if story_bible_artifact is not None:
            sources.append(
                {
                    "artifact_id": str(story_bible_artifact.id),
                    "version": story_bible_artifact.version,
                    "relation": _REL_REFERENCE,
                }
            )
        return sources
