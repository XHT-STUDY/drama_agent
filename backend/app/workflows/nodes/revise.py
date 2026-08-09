"""revise 节点 — 生成修订计划并产出候选新稿 (F-05).

流程:
1. RevisionService.build_revision_plan 确定性复选最低分集并持久化修订计划;
2. RevisionAgent.revise_episode 调用 ReviserSkill 产出完整新稿;
3. 候选稿以 status="draft" 落库 —— 是否成为 latest valid 由 continuity_check
   节点决定（通过后提升为 valid，失败保留为诊断用的候选版本）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from langgraph.config import get_config

from app.agents.base import BaseAgent
from app.agents.revision import RevisionAgent
from app.application.artifact_service import ArtifactResponse, ArtifactService
from app.application.revision_service import RevisionService
from app.artifacts.store import ArtifactStore
from app.domain.outline import EpisodeOutlineSet
from app.domain.revision import RevisionPlan
from app.domain.script import ScriptDraft
from app.domain.story_bible import StoryBible
from app.events.publisher import EventPublisher
from app.prompts.loader import PromptLoader
from app.skills.registry import SkillRegistry
from app.skills.reviser import ReviserSkill
from app.workflows.state import CreationState

logger = logging.getLogger(__name__)

_REL_DERIVED = "derived_from"
_REL_REFERENCE = "references"

# ReviserSkill 注册一次即复用（模块级缓存，仿 evaluate_episode.py 模式）
_revision_registry: SkillRegistry | None = None


def _build_revision_agent(base_agent: BaseAgent) -> RevisionAgent:
    """构造修订 Agent（复用模块级 SkillRegistry）。"""
    global _revision_registry
    if _revision_registry is None:
        _revision_registry = SkillRegistry()
        _revision_registry.register(ReviserSkill())
    return RevisionAgent(base_agent=base_agent, skill_registry=_revision_registry)


def _ctx() -> dict[str, Any]:
    return cast(dict[str, Any], get_config()["configurable"])  # type: ignore[redundant-cast]


def _find_ep_outline(outline_set: EpisodeOutlineSet, ep_num: int) -> dict[str, Any] | None:
    for ep in outline_set.episodes:
        if ep.episode_number == ep_num:
            return ep.model_dump()
    return None


async def revise_node(state: CreationState) -> dict[str, Any]:
    """为选中的最低分集生成修订计划并产出候选新稿。"""
    ctx = _ctx()
    db = ctx["db"]
    agent: BaseAgent = ctx["agent"]
    prompt_loader: PromptLoader = ctx["prompt_loader"]
    artifact_svc: ArtifactService = ctx["artifact_service"]
    publisher: EventPublisher = ctx["event_publisher"]
    project_id = uuid.UUID(state["project_id"])
    run_id = uuid.UUID(state["run_id"])
    progress = ctx.get("progress_callback", lambda *a: None)

    if "revise" in state.get("completed_nodes", []):
        return {}

    candidate_ep = state.get("revision_candidate_episode")
    if candidate_ep is None:
        logger.warning("无待修订集，跳过修订节点")
        return {"completed_nodes": state.get("completed_nodes", []) + ["revise"]}

    await publisher.publish(
        db, run_id=run_id, event_type="node.started",
        payload={"node": "revise", "episode": candidate_ep, "progress": 0.99},
        autocommit=True,
    )
    progress("revise", "started", 0.99)

    try:
        # 1. 组装评估报告（ArtifactResponse），交给 Service 确定性复选 + 生成计划
        eval_ids = state.get("evaluation_artifact_ids", {})
        eval_artifacts: list[ArtifactResponse] = []
        for aid in eval_ids.values():
            eval_artifacts.append(await artifact_svc.get_version(db, uuid.UUID(aid)))

        revision_svc = RevisionService()
        plan_artifact = await revision_svc.build_revision_plan(
            db,
            project_id=project_id,
            evaluation_reports=eval_artifacts,
            agent=agent,
            prompt_loader=prompt_loader,
        )
        if plan_artifact is None:
            logger.info("无需修订（无 need_revision 集），跳过修订节点")
            return {"completed_nodes": state.get("completed_nodes", []) + ["revise"]}

        plan = RevisionPlan.model_validate(plan_artifact.content)
        plan_artifact_id = plan_artifact.id

        # 2. 追溯原稿与上下文
        original_artifact = await artifact_svc.get_version(db, plan.source_script_artifact_id)
        original_draft = ScriptDraft.model_validate(original_artifact.content)

        sb_artifact = await artifact_svc.get_version(db, uuid.UUID(state["story_bible_artifact_id"]))
        story_bible = StoryBible.model_validate(sb_artifact.content)
        outline_artifact = await artifact_svc.get_version(db, uuid.UUID(state["outline_set_artifact_id"]))
        outline_set = EpisodeOutlineSet.model_validate(outline_artifact.content)
        episode_outline = _find_ep_outline(outline_set, plan.episode_number)
        if episode_outline is None:
            raise ValueError(f"大纲中未找到第 {plan.episode_number} 集")

        continuity_state = state.get("continuity_state_text", "")

        # 3. 调用 Reviser 生成完整新稿
        revision_agent = _build_revision_agent(agent)
        result = await revision_agent.revise_episode(
            script_draft=original_draft,
            revision_plan=plan,
            story_bible=story_bible.model_dump(),
            episode_outline=episode_outline,
            source_revision_plan_artifact_id=plan_artifact_id,
            prompt_loader=prompt_loader,
            continuity_state=continuity_state,
        )
        logger.info(
            "第 %d 集修订完成: scenes=%d executions=%d",
            result.script_draft.episode_number,
            len(result.script_draft.scenes),
            len(result.operation_executions),
        )

        # 4. 候选稿以 draft 状态落库（通过与否由 continuity_check 决定）
        # source 依赖遵循 write_episode 约定（evaluation_service._resolve_context
        # 依赖它追溯评估上下文）: derived_from → outline（取最后一项）、
        # references → story_bible（取第一项）。原稿与计划用 "revises" 关系
        # 保留溯源，避免被误当作 outline/story_bible。
        prompt_version = prompt_loader.get("revise_episode").version
        draft_content = result.script_draft.model_dump(mode="json")
        store = ArtifactStore()
        new_draft = await store.create(
            db,
            project_id=project_id,
            artifact_type="script_draft",
            episode_number=plan.episode_number,
            content=draft_content,
            status="draft",
            prompt_version=prompt_version,
            source_artifact_ids=[
                {
                    "artifact_id": str(original_artifact.id),
                    "version": original_artifact.version,
                    "relation": "revises",
                },
                {
                    "artifact_id": str(plan_artifact_id),
                    "version": plan_artifact.version,
                    "relation": "revises",
                },
                {
                    "artifact_id": str(sb_artifact.id),
                    "version": sb_artifact.version,
                    "relation": _REL_REFERENCE,
                },
                {
                    "artifact_id": str(outline_artifact.id),
                    "version": outline_artifact.version,
                    "relation": _REL_DERIVED,
                },
            ],
        )
        new_draft_id = str(new_draft.id)
        updated_scripts = {
            **state.get("script_artifact_ids", {}),
            str(plan.episode_number): new_draft_id,
        }

        await publisher.publish(
            db, run_id=run_id, event_type="artifact.created",
            payload={
                "artifact_id": new_draft_id, "artifact_type": "script_draft",
                "episode": plan.episode_number, "version": new_draft.version,
                "message": f"第 {plan.episode_number} 集修订候选稿已生成",
            },
            autocommit=True,
        )
        await publisher.publish(
            db, run_id=run_id, event_type="node.completed",
            payload={"node": "revise", "episode": plan.episode_number, "progress": 1.0},
            autocommit=True,
        )
        progress("revise", "completed", 1.0)

        return {
            "script_artifact_ids": updated_scripts,
            "revision_plan_artifact_id": str(plan_artifact_id),
            "completed_nodes": state.get("completed_nodes", []) + ["revise"],
            "prompt_versions": {**state.get("prompt_versions", {}), "revise_episode": prompt_version},
        }
    except Exception as e:
        logger.exception("修订节点失败")
        await publisher.publish(
            db, run_id=run_id, event_type="node.failed",
            payload={"node": "revise", "error": str(e)},
            autocommit=True,
        )
        return {"status": "failed", "error_node": "revise", "error_detail": str(e)}
