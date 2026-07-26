"""write_episode 节点 — 按集顺序生成剧本 (C-07)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langgraph.config import get_config

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.domain.continuity import EpisodeSummary as EpSummary
from app.domain.outline import EpisodeOutlineSet
from app.domain.script import EpisodeWriterInput
from app.domain.story_bible import StoryBible
from app.events.publisher import EventPublisher
from app.memory.continuity import ContinuityManager
from app.prompts.loader import PromptLoader
from app.skills.episode_writer import EpisodeWriterSkill
from app.workflows.state import CreationState

logger = logging.getLogger(__name__)
_MVP_DEFAULT_SCRIPT_COUNT = 3  # 默认值，实际从 workflow config 读取


def _ctx() -> dict[str, Any]:
    return get_config()["configurable"]  # type: ignore[no-any-return]


async def write_episodes_node(state: CreationState) -> dict[str, Any]:
    """按 1..3 顺序撰写各集剧本草稿。"""
    ctx = _ctx()
    db = ctx["db"]
    agent: BaseAgent = ctx["agent"]
    prompt_loader: PromptLoader = ctx["prompt_loader"]
    artifact_svc: ArtifactService = ctx["artifact_service"]
    publisher: EventPublisher = ctx["event_publisher"]
    project_id = uuid.UUID(state["project_id"])
    run_id = uuid.UUID(state["run_id"])
    progress = ctx.get("progress_callback", lambda *a: None)

    if "write_episodes" in state.get("completed_nodes", []):
        return {}

    sb_artifact = await artifact_svc.get_version(db, uuid.UUID(state["story_bible_artifact_id"]))
    story_bible = StoryBible.model_validate(sb_artifact.content)
    outline_artifact = await artifact_svc.get_version(db, uuid.UUID(state["outline_set_artifact_id"]))
    outline_set = EpisodeOutlineSet.model_validate(outline_artifact.content)
    outline_aid = outline_artifact.id

    continuity_mgr = ContinuityManager()
    continuity_state = continuity_mgr.create_initial_state(story_bible)

    existing_scripts: dict[str, str] = state.get("script_artifact_ids", {})
    start_ep = state.get("current_episode", 1)
    completed_scripts: dict[str, str] = {}

    # 从 workflow config 读取 script_count（兼容旧 config 无此字段）
    script_count = ctx.get("script_count", _MVP_DEFAULT_SCRIPT_COUNT)
    if script_count < 1:
        script_count = _MVP_DEFAULT_SCRIPT_COUNT
    logger.info(
        "开始撰写剧本: 从第 %d 集到第 %d 集 (共 %d 集)",
        start_ep, script_count, script_count - start_ep + 1,
    )

    await publisher.publish(
        db, run_id=run_id, event_type="node.started",
        payload={"node": "write_episodes", "start_episode": start_ep, "script_count": script_count, "progress": 0.40},
        autocommit=True,
    )
    progress("write_episodes", "started", 0.40)

    try:
        skill = EpisodeWriterSkill()
        prompt_version = prompt_loader.get("write_episode").version

        for ep_num in range(start_ep, script_count + 1):
            if str(ep_num) in existing_scripts:
                completed_scripts[str(ep_num)] = existing_scripts[str(ep_num)]
                continue

            episode_outline = _find_ep_outline(outline_set, ep_num)
            if episode_outline is None:
                raise ValueError(f"大纲中未找到第 {ep_num} 集")

            continuity_text = continuity_mgr.get_context_for_episode(continuity_state, ep_num)
            previous_summary = ""
            if ep_num > 1:
                prev = [s for s in continuity_state.episode_summaries if s.episode_number == ep_num - 1]
                if prev:
                    previous_summary = prev[0].summary

            ew_input = EpisodeWriterInput(
                episode_number=ep_num,
                episode_outline=episode_outline,
                story_bible=story_bible.model_dump(),
                previous_summary=previous_summary,
                continuity_state=continuity_text,
                rag_context=ctx.get("rag_context", ""),
            )

            logger.info("正在调用 LLM 生成第 %d 集剧本…", ep_num)
            draft = await skill.execute({
                "input": ew_input, "agent": agent, "prompt_loader": prompt_loader,
                "outline_artifact_id": outline_aid,
            })
            logger.info("第 %d 集 LLM 生成完成，开始后处理…", ep_num)

            content = draft.model_dump(mode="json")
            artifact = await artifact_svc.create_validated_artifact(
                db, project_id=project_id, artifact_type="script_draft",
                episode_number=ep_num, content=content, prompt_version=prompt_version,
                source_artifact_ids=[
                    {
                        "artifact_id": state["outline_set_artifact_id"],
                        "version": outline_artifact.version,
                        "relation": "derived_from",
                    },
                    {
                        "artifact_id": state["story_bible_artifact_id"],
                        "version": sb_artifact.version,
                        "relation": "references",
                    },
                ],
            )
            completed_scripts[str(ep_num)] = str(artifact.id)

            ep_summary = EpSummary(
                episode_number=ep_num,
                summary=f"第 {ep_num} 集完成: {draft.title}",
                key_events=[s.action[:30] for s in draft.scenes[:3]],
                ending_state=draft.ending_hook[:50],
            )
            continuity_state = continuity_mgr.update_after_episode(continuity_state, ep_summary)

            await publisher.publish(
                db, run_id=run_id, event_type="artifact.created",
                payload={
                    "artifact_id": str(artifact.id), "artifact_type": "script_draft",
                    "episode": ep_num, "version": artifact.version,
                    "progress": 0.40 + ep_num * 0.15,
                    "message": f"第 {ep_num} 集剧本已完成",
                },
                autocommit=True,
            )
            progress("write_episodes", f"ep_{ep_num}_done", 0.40 + ep_num * 0.15)

        continuity_text = continuity_mgr.get_context_for_episode(continuity_state, script_count + 1)

        await publisher.publish(
            db, run_id=run_id, event_type="node.completed",
            payload={"node": "write_episodes", "episodes_written": len(completed_scripts), "progress": 0.85},
            autocommit=True,
        )
        progress("write_episodes", "completed", 0.85)

        return {
            "script_artifact_ids": completed_scripts,
            "continuity_state_text": continuity_text,
            "completed_nodes": state.get("completed_nodes", []) + ["write_episodes"],
            "prompt_versions": {**state.get("prompt_versions", {}), "write_episode": prompt_version},
        }
    except Exception as e:
        logger.exception("剧本撰写失败")
        await publisher.publish(
            db, run_id=run_id, event_type="node.failed",
            payload={"node": "write_episodes", "error": str(e)},
            autocommit=True,
        )
        return {
            "status": "failed", "error_node": "write_episodes", "error_detail": str(e),
            "script_artifact_ids": completed_scripts,
        }


def _find_ep_outline(outline_set: EpisodeOutlineSet, ep_num: int) -> dict[str, Any] | None:
    for ep in outline_set.episodes:
        if ep.episode_number == ep_num:
            return ep.model_dump()
    return None
