"""outline 节点 — 分集大纲生成 (C-07)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langgraph.config import get_config

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.domain.outline import OutlineInput
from app.domain.story_bible import StoryBible
from app.events.publisher import EventPublisher
from app.prompts.loader import PromptLoader
from app.skills.outline import OutlineSkill
from app.workflows.state import CreationState

logger = logging.getLogger(__name__)


def _ctx() -> dict[str, Any]:
    return get_config()["configurable"]  # type: ignore[no-any-return]


async def outline_node(state: CreationState) -> dict[str, Any]:
    """从 StoryBible 生成 10 集分集大纲。"""
    ctx = _ctx()
    db = ctx["db"]
    agent: BaseAgent = ctx["agent"]
    prompt_loader: PromptLoader = ctx["prompt_loader"]
    artifact_svc: ArtifactService = ctx["artifact_service"]
    publisher: EventPublisher = ctx["event_publisher"]
    project_id = uuid.UUID(state["project_id"])
    run_id = uuid.UUID(state["run_id"])
    progress = ctx.get("progress_callback", lambda *a: None)

    if "outline" in state.get("completed_nodes", []):
        return {}

    await publisher.publish(
        db, run_id=run_id, event_type="node.started",
        payload={"node": "outline", "progress": 0.25},
        autocommit=True,
    )
    progress("outline", "started", 0.25)

    try:
        sb_artifact_id = state["story_bible_artifact_id"]
        sb_artifact = await artifact_svc.get_version(db, uuid.UUID(sb_artifact_id))
        story_bible = StoryBible.model_validate(sb_artifact.content)

        # 从 workflow config 读取大纲集数（默认 10，与 script_count 独立）
        outline_count = ctx.get("outline_count", 10)
        if outline_count < 1:
            outline_count = 10
        logger.info("正在调用 LLM 生成 %d 集大纲…", outline_count)

        ol_input = OutlineInput(
            story_bible=story_bible.model_dump(),
            # D-05: 优先消费本阶段检索结果，缺失时回退合并上下文（向后兼容）
            rag_context=ctx.get("outline_rag") or ctx.get("rag_context", ""),
            outline_count=outline_count,
        )

        skill = OutlineSkill()
        logger.info("正在调用 LLM 生成分集大纲…")
        episode_set = await skill.execute({
            "input": ol_input, "agent": agent, "prompt_loader": prompt_loader,
        })

        content = episode_set.model_dump()
        prompt_version = prompt_loader.get("outline").version
        artifact = await artifact_svc.create_validated_artifact(
            db, project_id=project_id, artifact_type="episode_outline_set",
            content=content, prompt_version=prompt_version,
            source_artifact_ids=[
                {"artifact_id": sb_artifact_id, "version": sb_artifact.version, "relation": "derived_from"},
            ],
        )

        await publisher.publish(
            db, run_id=run_id, event_type="node.completed",
            payload={"node": "outline", "artifact_id": str(artifact.id), "progress": 0.40},
            autocommit=True,
        )
        progress("outline", "completed", 0.40)

        return {
            "outline_set_artifact_id": str(artifact.id),
            "current_episode": 1,
            "completed_nodes": state.get("completed_nodes", []) + ["outline"],
            "prompt_versions": {**state.get("prompt_versions", {}), "outline": prompt_version},
        }
    except Exception as e:
        logger.exception("分集大纲生成失败")
        await publisher.publish(
            db, run_id=run_id, event_type="node.failed",
            payload={"node": "outline", "error": str(e)},
            autocommit=True,
        )
        return {"status": "failed", "error_node": "outline", "error_detail": str(e)}
