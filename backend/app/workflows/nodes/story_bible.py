"""story_bible 节点 — 故事设定生成 (C-07)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langgraph.config import get_config

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.domain.requirement import NormalizedRequirement
from app.domain.story_bible import StoryBibleInput
from app.events.publisher import EventPublisher
from app.prompts.loader import PromptLoader
from app.skills.story_bible import StoryBibleSkill
from app.workflows.state import CreationState

logger = logging.getLogger(__name__)


def _ctx() -> dict[str, Any]:
    return get_config()["configurable"]  # type: ignore[no-any-return]


async def story_bible_node(state: CreationState) -> dict[str, Any]:
    """从归一化需求生成 StoryBible。"""
    ctx = _ctx()
    db = ctx["db"]
    agent: BaseAgent = ctx["agent"]
    prompt_loader: PromptLoader = ctx["prompt_loader"]
    artifact_svc: ArtifactService = ctx["artifact_service"]
    publisher: EventPublisher = ctx["event_publisher"]
    project_id = uuid.UUID(state["project_id"])
    run_id = uuid.UUID(state["run_id"])
    progress = ctx.get("progress_callback", lambda *a: None)

    if "story_bible" in state.get("completed_nodes", []):
        return {}

    await publisher.publish(
        db, run_id=run_id, event_type="node.started",
        payload={"node": "story_bible", "progress": 0.1},
        autocommit=True,
    )
    progress("story_bible", "started", 0.1)

    try:
        req_artifact_id = state["requirement_artifact_id"]
        req_artifact = await artifact_svc.get_version(db, uuid.UUID(req_artifact_id))
        requirement = NormalizedRequirement.model_validate(req_artifact.content)

        sb_input = StoryBibleInput(
            normalized_requirement=requirement.model_dump(),
            # D-05: 优先消费本阶段检索结果，缺失时回退合并上下文（向后兼容）
            rag_context=ctx.get("story_bible_rag") or ctx.get("rag_context", ""),
        )

        skill = StoryBibleSkill()
        logger.info("正在调用 LLM 生成 StoryBible…")
        story_bible = await skill.execute({
            "input": sb_input, "agent": agent, "prompt_loader": prompt_loader,
        })

        content = story_bible.model_dump()
        prompt_version = prompt_loader.get("story_bible").version
        artifact = await artifact_svc.create_validated_artifact(
            db, project_id=project_id, artifact_type="story_bible",
            content=content, prompt_version=prompt_version,
            source_artifact_ids=[
                {"artifact_id": req_artifact_id, "version": req_artifact.version, "relation": "derived_from"},
            ],
        )

        await publisher.publish(
            db, run_id=run_id, event_type="node.completed",
            payload={"node": "story_bible", "artifact_id": str(artifact.id), "progress": 0.25},
            autocommit=True,
        )
        progress("story_bible", "completed", 0.25)

        return {
            "story_bible_artifact_id": str(artifact.id),
            "completed_nodes": state.get("completed_nodes", []) + ["story_bible"],
            "prompt_versions": {**state.get("prompt_versions", {}), "story_bible": prompt_version},
        }
    except Exception as e:
        logger.exception("StoryBible 生成失败")
        await publisher.publish(
            db, run_id=run_id, event_type="node.failed",
            payload={"node": "story_bible", "error": str(e)},
            autocommit=True,
        )
        return {"status": "failed", "error_node": "story_bible", "error_detail": str(e)}
