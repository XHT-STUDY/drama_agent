"""Story State 只读查询 API(M-04,W3-04)。

端点:
- GET /projects/{project_id}/story-state?through_episode=N&run_id=<optional>

契约(docs/MEMORY_IMPLEMENTATION_PLAN.md §6):
- 默认解析"当前采用集合"(各集最新 valid 剧本);带 run_id 时解析该 Run
  的冻结工作集;
- 只读:不调用模型、不修改状态(测试以 FakeLLM 调用计数为证);
  显式刷新复用现有 Run 机制(POST /projects/{id}/runs),不在 GET 触发派生;
- status: ready | pending | stale | gap | missing。
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.api.dependencies import get_db
from app.application.story_state_service import StoryStateService, StoryWorkset
from app.core.config import load_settings
from app.core.errors import NotFoundError
from app.db.models.project import Project
from app.db.models.workflow_run import WorkflowRun
from app.db.repositories.artifacts import ArtifactRepository
from app.domain.enums import ArtifactType
from app.llm.fake import FakeLLM
from app.llm.openai_compatible import OpenAICompatibleLLM

logger = logging.getLogger(__name__)

router = APIRouter(tags=["story-state"])


async def _resolve_workset(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    run_id: uuid.UUID | None,
    through_episode: int | None,
) -> tuple[StoryWorkset, int]:
    """解析查询用工作集与目标集数(只读)。"""
    repo = ArtifactRepository(db)
    if run_id is not None:
        run = (await db.execute(
            select(WorkflowRun).where(WorkflowRun.id == run_id)
        )).scalar_one_or_none()
        if run is None or run.project_id != project_id:
            raise NotFoundError(
                detail=f"Run 不存在或不属于项目: {run_id}", code="RUN_NOT_FOUND"
            )
        summary = run.state_summary or {}
        scripts = {
            int(ep): uuid.UUID(aid)
            for ep, aid in (summary.get("script_artifact_ids") or {}).items()
            if ep.isdigit()
        }
        through = through_episode or max(scripts, default=0)
        return StoryWorkset(
            project_id=project_id,
            story_bible_artifact_id=uuid.UUID(summary["story_bible_artifact_id"]),
            outline_artifact_id=uuid.UUID(summary["outline_set_artifact_id"]),
            scripts=scripts,
        ), through

    # 默认:当前采用集合 = 各集最新 valid 剧本
    story_bible = await repo.get_latest_valid(
        project_id, ArtifactType.STORY_BIBLE.value, 1
    )
    outline = await repo.get_latest_valid(
        project_id, ArtifactType.EPISODE_OUTLINE_SET.value, 1
    )
    if story_bible is None or outline is None:
        raise NotFoundError(
            detail="项目尚无 StoryBible 或大纲,无法解析剧情状态",
            code="ARTIFACT_NOT_FOUND",
        )
    adopted: dict[int, uuid.UUID] = {}
    for episode in range(1, (through_episode or 1) + 1):
        script = await repo.get_latest_valid(
            project_id, ArtifactType.SCRIPT_DRAFT.value, episode
        )
        if script is not None:
            adopted[episode] = script.id
    through = through_episode or max(adopted, default=0)
    return StoryWorkset(
        project_id=project_id,
        story_bible_artifact_id=story_bible.id,
        outline_artifact_id=outline.id,
        scripts=adopted,
    ), through


@router.get("/projects/{project_id}/story-state")
async def get_story_state(
    project_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    through_episode: Annotated[int | None, Query(ge=1, le=200)] = None,
    run_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """解析剧情状态(只读,不调用模型——resolve_status 纯查询)。"""
    project = (await db.execute(
        select(Project).where(Project.id == project_id)
    )).scalar_one_or_none()
    if project is None or project.deleted_at is not None:
        raise NotFoundError(
            detail=f"项目不存在: {project_id}", code="PROJECT_NOT_FOUND"
        )

    workset, through = await _resolve_workset(
        db, project_id, run_id=run_id, through_episode=through_episode
    )
    if through < 1:
        return {
            "project_id": str(project_id),
            "status": "missing",
            "requested_through": 0,
            "missing_episodes": [],
            "stale_from_episode": None,
            "state_artifact_id": None,
            "basis": None,
            "episode_summary_artifact_ids": {},
            "warnings": ["项目尚无剧本,无剧情状态"],
        }

    # resolve_status 不触发模型;agent 仅作服务构造参数(测试用
    # FakeLLM 调用计数断言 GET 零模型调用)。
    settings = load_settings()
    llm: Any = (
        FakeLLM(seed=0) if settings.app_env == "test"
        else OpenAICompatibleLLM(settings)
    )
    service = StoryStateService(BaseAgent(name="summarizer", llm=llm))
    status = await service.resolve_status(db, workset, through)
    return {
        "project_id": str(project_id),
        "run_id": str(run_id) if run_id else None,
        **status,
    }
