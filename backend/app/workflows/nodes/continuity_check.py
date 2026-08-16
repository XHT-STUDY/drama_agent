"""continuity_check 节点 — 修订稿连续性检查 (F-05).

对候选新稿执行规则优先 + 必要语义的连续性检查（ContinuityCheckSkill）：
- 通过 → 候选稿提升为 valid（成为 latest valid，随后 re_evaluate 重评）;
- 失败 → 保留候选稿（status="draft"，诊断用途）+ 连续性结果落库，
  并标记 needs_manual_review（Run 转人工审查，不进入 re_evaluate）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from langgraph.config import get_config

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.domain.continuity import ContinuityState, EpisodeSummary
from app.domain.outline import EpisodeOutlineSet
from app.domain.revision import (
    ContinuityCheckInput,
    ContinuityCheckResult,
    RevisionPlan,
)
from app.domain.script import ScriptDraft
from app.domain.story_bible import StoryBible
from app.events.publisher import EventPublisher
from app.memory.continuity import ContinuityManager
from app.prompts.loader import PromptLoader
from app.skills.continuity_check import ContinuityCheckSkill
from app.workflows.checkpoint import node_failure, raise_if_cancelled
from app.workflows.state import CreationState

logger = logging.getLogger(__name__)


def _ctx() -> dict[str, Any]:
    return cast(dict[str, Any], get_config()["configurable"])  # type: ignore[redundant-cast]


def _find_ep_outline(outline_set: EpisodeOutlineSet, ep_num: int) -> dict[str, Any] | None:
    for ep in outline_set.episodes:
        if ep.episode_number == ep_num:
            return ep.model_dump()
    return None


async def _reconstruct_continuity_state(
    story_bible: StoryBible,
    script_artifact_ids: dict[str, str],
    artifact_svc: ArtifactService,
    db: Any,
    up_to_episode: int,
) -> ContinuityState:
    """回放 1..up_to_episode-1 集的剧本摘要，重建修订前的连续性状态。

    与 write_episode 节点使用的构建方式一致：初始状态来自 StoryBible，
    每完成一集追加一条 EpisodeSummary（标题 / 关键事件 / 结尾钩子）。
    候选集本身（up_to_episode）尚未完成，故不含其摘要。
    """
    state = ContinuityManager.create_initial_state(story_bible)
    for ep in range(1, up_to_episode):
        sid = script_artifact_ids.get(str(ep))
        if sid is None:
            continue
        # 候选集之后新替换的稿不参与回放；此处只取既有集的原稿
        artifact = await _load_artifact_content(db, artifact_svc, sid)
        draft = ScriptDraft.model_validate(artifact)
        summary = EpisodeSummary(
            episode_number=ep,
            summary=f"第 {ep} 集完成: {draft.title}",
            key_events=[s.action[:30] for s in draft.scenes[:3]],
            ending_state=draft.ending_hook[:50],
        )
        state = ContinuityManager.update_after_episode(state, summary)
    return state


async def _load_artifact_content(
    db: Any,
    artifact_svc: ArtifactService,
    artifact_id: str,
) -> dict[str, Any]:
    artifact = await artifact_svc.get_version(db, uuid.UUID(artifact_id))
    return artifact.content


async def continuity_check_node(state: CreationState) -> dict[str, Any]:
    """对修订候选稿执行连续性检查并决定候选稿的最终状态。"""
    ctx = _ctx()
    db = ctx["db"]
    agent: BaseAgent = ctx["agent"]
    prompt_loader: PromptLoader = ctx["prompt_loader"]
    artifact_svc: ArtifactService = ctx["artifact_service"]
    publisher: EventPublisher = ctx["event_publisher"]
    project_id = uuid.UUID(state["project_id"])
    run_id = uuid.UUID(state["run_id"])
    progress = ctx.get("progress_callback", lambda *a: None)

    # 协作式取消守卫（I-01）
    raise_if_cancelled(state["run_id"])

    # 失败短路（I-01）：上游节点已失败则跳过本节点，保持失败状态不变
    if state.get("status") == "failed":
        return {}

    if "continuity_check" in state.get("completed_nodes", []):
        return {}

    candidate_ep = state.get("revision_candidate_episode")
    plan_artifact_id = state.get("revision_plan_artifact_id")
    if candidate_ep is None or plan_artifact_id is None:
        logger.warning("缺少修订上下文，跳过连续性检查")
        return {"completed_nodes": state.get("completed_nodes", []) + ["continuity_check"]}

    await publisher.publish(
        db, run_id=run_id, event_type="node.started",
        payload={"node": "continuity_check", "episode": candidate_ep, "progress": 0.99},
        autocommit=True,
    )
    progress("continuity_check", "started", 0.99)

    try:
        plan = RevisionPlan.model_validate(
            await _load_artifact_content(db, artifact_svc, plan_artifact_id)
        )
        new_draft_id = state["script_artifact_ids"][str(candidate_ep)]
        new_draft = ScriptDraft.model_validate(
            await _load_artifact_content(db, artifact_svc, new_draft_id)
        )
        original_draft = ScriptDraft.model_validate(
            await _load_artifact_content(db, artifact_svc, str(plan.source_script_artifact_id))
        )

        sb_artifact = await artifact_svc.get_version(db, uuid.UUID(state["story_bible_artifact_id"]))
        story_bible = StoryBible.model_validate(sb_artifact.content)
        outline_artifact = await artifact_svc.get_version(db, uuid.UUID(state["outline_set_artifact_id"]))
        outline_set = EpisodeOutlineSet.model_validate(outline_artifact.content)
        episode_outline = _find_ep_outline(outline_set, candidate_ep)
        if episode_outline is None:
            raise ValueError(f"大纲中未找到第 {candidate_ep} 集")

        continuity_state = await _reconstruct_continuity_state(
            story_bible, state.get("script_artifact_ids", {}),
            artifact_svc, db, candidate_ep,
        )

        check_input = ContinuityCheckInput(
            episode_number=candidate_ep,
            script_draft=new_draft,
            original_script_draft=original_draft,
            episode_outline=episode_outline,
            story_bible=story_bible.model_dump(),
            continuity_state=continuity_state,
            locked_facts=list(plan.locked_facts),
        )
        result: ContinuityCheckResult = await ContinuityCheckSkill().execute(
            {"input": check_input, "agent": agent, "prompt_loader": prompt_loader}
        )
        logger.info(
            "第 %d 集连续性检查: status=%s violations=%d warnings=%d",
            candidate_ep, result.status, len(result.violations), len(result.warnings),
        )

        # 持久化连续性检查结果（诊断可追溯）;LLM 语义检查实际调用的 prompt
        # 是 continuity_semantic_check（ContinuityCheckSkill 内部），以此记录版本
        prompt_version = prompt_loader.get("continuity_semantic_check").version
        await artifact_svc.create_validated_artifact(
            db,
            project_id=project_id,
            artifact_type="continuity_check",
            episode_number=candidate_ep,
            content=result.model_dump(mode="json"),
            prompt_version=prompt_version,
            source_artifact_ids=[
                {
                    "artifact_id": new_draft_id,
                    "version": 0,
                    "relation": "derived_from",
                }
            ],
        )

        if result.status == "fail":
            reason = "连续性检查失败: " + ", ".join(v.kind for v in result.violations[:5])
            logger.warning("第 %d 集连续性检查失败，转人工审查", candidate_ep)
            await publisher.publish(
                db, run_id=run_id, event_type="node.completed",
                payload={"node": "continuity_check", "status": "fail", "progress": 1.0},
                autocommit=True,
            )
            progress("continuity_check", "completed", 1.0)
            return {
                "needs_manual_review": True,
                "needs_manual_review_reason": reason,
                "completed_nodes": state.get("completed_nodes", []) + ["continuity_check"],
                "prompt_versions": {**state.get("prompt_versions", {}), "continuity_check": prompt_version},
            }

        # 通过 → 候选稿提升为 valid（内容不可变，仅改 status 元数据列）
        from app.db.models.artifact import Artifact as ArtifactModel

        art = await db.get(ArtifactModel, uuid.UUID(new_draft_id))
        if art is not None:
            art.status = "valid"
            await db.flush()
            logger.info("第 %d 集候选稿 %s 已提升为 valid", candidate_ep, new_draft_id[:12])

        await publisher.publish(
            db, run_id=run_id, event_type="node.completed",
            payload={"node": "continuity_check", "status": "pass", "progress": 1.0},
            autocommit=True,
        )
        progress("continuity_check", "completed", 1.0)

        return {
            "completed_nodes": state.get("completed_nodes", []) + ["continuity_check"],
            "prompt_versions": {**state.get("prompt_versions", {}), "continuity_check": prompt_version},
        }
    except Exception as e:
        logger.exception("连续性检查节点失败")
        await publisher.publish(
            db, run_id=run_id, event_type="node.failed",
            payload={"node": "continuity_check", "error": str(e)},
            autocommit=True,
        )
        return node_failure("continuity_check", e)
