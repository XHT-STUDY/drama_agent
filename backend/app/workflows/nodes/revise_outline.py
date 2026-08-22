"""revise_outline 节点 — 对话式大纲修订与版本落库（J-08）。

流程:
1. 加载 source outline（服务端解析的 Artifact ID，防御性校验归属/类型/
   valid）与最新 valid Story Bible;
2. 调用 OutlineReviserSkill（不变量失败重试，用户约束写入 Prompt）;
3. 合法输出 → create_validated_artifact 持久化为新版本（成为 latest valid），
   sources 含旧 outline `revises` + Story Bible `references`;
4. OutlineImpactTool 确定性比较新旧大纲（dependent_scripts 取 derived_from
   旧大纲的剧本），结果写入 state.outline_impact;
5. 不变量失败（重试耗尽）→ 最后一次被拒绝的输出落库为 status="invalid"
   诊断版本，Run failed，latest valid 与旧 Artifact 内容/checksum 不变。

工作流不调用剧本生成或修订——受影响剧本以 follow-up 建议的形式报告，
由后续 Action（J-09）决定是否发起 revise_script。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from langgraph.config import get_config

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactResponse, ArtifactService
from app.artifacts.store import ArtifactStore
from app.domain.outline import EpisodeOutlineSet
from app.domain.outline_revision import OutlineRevisionInput
from app.events.publisher import EventPublisher
from app.prompts.loader import PromptLoader
from app.skills.outline_reviser import (
    OutlineReviserSkill,
    OutlineRevisionValidationError,
)
from app.tools.outline_impact import DependentScript, OutlineImpactTool
from app.workflows.checkpoint import node_failure, raise_if_cancelled
from app.workflows.state import CreationState

logger = logging.getLogger(__name__)


def _ctx() -> dict[str, Any]:
    return cast(dict[str, Any], get_config()["configurable"])  # type: ignore[redundant-cast]


async def _dependent_scripts(
    artifact_svc: ArtifactService, db: Any, outline_id: uuid.UUID
) -> list[DependentScript]:
    """收集 derived_from 旧大纲的剧本（status 不变，仅读取引用关系）。"""
    scripts = await artifact_svc.find_referencing_artifacts(
        db, outline_id, relation="derived_from", artifact_type="script_draft"
    )
    return [
        DependentScript(
            script_artifact_id=str(s.id),
            episode_number=s.episode_number,
            source_outline_artifact_id=str(outline_id),
        )
        for s in scripts
    ]


async def revise_outline_node(state: CreationState) -> dict[str, Any]:
    """执行大纲修订：加载 → Skill → 落库 → 影响分析。"""
    ctx = _ctx()
    db = ctx["db"]
    agent: BaseAgent = ctx["agent"]
    prompt_loader: PromptLoader = ctx["prompt_loader"]
    artifact_svc: ArtifactService = ctx["artifact_service"]
    publisher: EventPublisher = ctx["event_publisher"]
    run_id = uuid.UUID(state["run_id"])
    progress = ctx.get("progress_callback", lambda *a: None)

    raise_if_cancelled(state["run_id"])
    if state.get("status") == "failed":
        return {}
    if "revise_outline" in state.get("completed_nodes", []):
        return {}

    await publisher.publish(
        db, run_id=run_id, event_type="node.started",
        payload={"node": "revise_outline", "progress": 0.9},
        autocommit=True,
    )
    progress("revise_outline", "started", 0.9)

    try:
        project_id = uuid.UUID(state["project_id"])
        source_id = state.get("source_outline_artifact_id")
        if not source_id:
            raise ValueError("缺少服务端解析的 source_outline_artifact_id")

        source = await artifact_svc.get_version(db, uuid.UUID(source_id))
        if (
            source.project_id != project_id
            or source.type != "episode_outline_set"
            or source.status != "valid"
        ):
            raise ValueError("修订目标不是当前项目的有效大纲版本")
        old_outline = EpisodeOutlineSet.model_validate(source.content)

        sb_artifact = await ArtifactStore().get_latest(
            db, project_id, "story_bible", 1
        )
        if sb_artifact is None:
            raise ValueError("缺少最新有效 Story Bible，无法执行大纲修订")

        revision_input = OutlineRevisionInput(
            old_outline=old_outline,
            story_bible=sb_artifact.content,
            user_constraints=list(state.get("user_constraints", [])),
            source_outline_artifact_id=uuid.UUID(source_id),
        )
        prompt_version = prompt_loader.get("outline_reviser").version
        sources = [
            {"artifact_id": str(source.id), "version": source.version, "relation": "revises"},
            {"artifact_id": str(sb_artifact.id), "version": sb_artifact.version, "relation": "references"},
        ]

        try:
            new_outline = await OutlineReviserSkill().execute(
                {"input": revision_input, "agent": agent, "prompt_loader": prompt_loader}
            )
        except OutlineRevisionValidationError as exc:
            # 不变量失败：最后一次被拒绝的输出保存为 invalid 诊断版本；
            # latest valid 不变，旧 Artifact 内容/checksum 不变。
            candidate = exc.last_candidate
            if candidate is not None:
                diagnostic = await ArtifactStore().create(
                    db,
                    project_id=project_id,
                    artifact_type="episode_outline_set",
                    content=candidate.model_dump(mode="json"),
                    status="invalid",
                    prompt_version=prompt_version,
                    source_artifact_ids=sources,
                    dedup_extra="\n".join(revision_input.user_constraints),
                )
                await publisher.publish(
                    db, run_id=run_id, event_type="artifact.created",
                    payload={
                        "artifact_id": str(diagnostic.id),
                        "artifact_type": "episode_outline_set",
                        "version": diagnostic.version, "status": "invalid",
                        "message": "大纲修订未通过不变量校验，已保存诊断版本",
                    },
                    autocommit=True,
                )
            raise ValueError(str(exc)) from exc

        # 合法输出 → 新版本落库（成为 latest valid；旧版本不可变）
        new_artifact: ArtifactResponse = await artifact_svc.create_validated_artifact(
            db,
            project_id=project_id,
            artifact_type="episode_outline_set",
            content=new_outline.model_dump(mode="json"),
            prompt_version=prompt_version,
            source_artifact_ids=sources,
            dedup_extra="\n".join(revision_input.user_constraints),
        )

        # 影响分析（确定性，不调 LLM）：哪些集变化、哪些剧本仍引用旧大纲
        dependents = await _dependent_scripts(artifact_svc, db, source.id)
        impact = await OutlineImpactTool().execute(
            old=old_outline,
            new=new_outline,
            dependent_scripts=dependents,
            old_outline_artifact_id=str(source.id),
        )
        impact_data = impact.model_dump(mode="json")

        await publisher.publish(
            db, run_id=run_id, event_type="artifact.created",
            payload={
                "artifact_id": str(new_artifact.id),
                "artifact_type": "episode_outline_set",
                "version": new_artifact.version,
                "changed_episodes": impact.changed_episodes,
                "dependent_script_ids": impact.dependent_script_ids,
                "message": "大纲修订完成，新版本已成为最新有效版本",
            },
            autocommit=True,
        )
        await publisher.publish(
            db, run_id=run_id, event_type="node.completed",
            payload={
                "node": "revise_outline",
                "old_outline_artifact_id": str(source.id),
                "new_outline_artifact_id": str(new_artifact.id),
                "changed_episodes": impact.changed_episodes,
                "follow_ups": impact.follow_ups,
                "progress": 1.0,
            },
            autocommit=True,
        )
        progress("revise_outline", "completed", 1.0)

        return {
            "outline_set_artifact_id": str(new_artifact.id),
            "source_outline_artifact_id": str(source.id),
            "outline_impact": impact_data,
            "completed_nodes": state.get("completed_nodes", []) + ["revise_outline"],
            "prompt_versions": {
                **state.get("prompt_versions", {}),
                "outline_reviser": prompt_version,
            },
        }
    except Exception as e:
        logger.exception("大纲修订节点失败")
        await publisher.publish(
            db, run_id=run_id, event_type="node.failed",
            payload={"node": "revise_outline", "error": str(e)},
            autocommit=True,
        )
        return node_failure("revise_outline", e)
