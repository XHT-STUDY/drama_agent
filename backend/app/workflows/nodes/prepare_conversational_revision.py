"""对话式剧本修订的准备节点（J-06）。

prepare_target：解析服务端下发的 source script ID（不允许 Planner 任意
指定 UUID），校验归属/类型/状态，解析集号并补齐修订分支所需的
StoryBible / 大纲 / 各集最新 valid 剧本映射。

ensure_evaluation：目标剧本没有绑定评估时，先仅评估目标集并持久化报告
（评估幂等：同一剧本版本已有评估直接复用），再进入 revise。

用户约束（user_constraints）在 revise 节点经 user_instruction 写入
RevisionPlan；重复执行由 completed_nodes 早退 + 评估/计划的 input_hash
幂等共同兜底，不会覆盖原稿或产生相同输入的重复有效版本。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from langgraph.config import get_config

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.application.evaluation_service import EvaluationService
from app.artifacts.store import ArtifactStore
from app.db.repositories.artifacts import ArtifactRepository
from app.events.publisher import EventPublisher
from app.prompts.loader import PromptLoader
from app.workflows.checkpoint import node_failure, raise_if_cancelled
from app.workflows.nodes.evaluate_episode import _build_eval_agent
from app.workflows.state import CreationState

logger = logging.getLogger(__name__)


def _ctx() -> dict[str, Any]:
    return cast(dict[str, Any], get_config()["configurable"])  # type: ignore[redundant-cast]


async def prepare_target_node(state: CreationState) -> dict[str, Any]:
    """解析修订目标：source script → 集号 + 修订分支所需上下文。"""
    ctx = _ctx()
    db = ctx["db"]
    artifact_svc: ArtifactService = ctx["artifact_service"]
    publisher: EventPublisher = ctx["event_publisher"]
    run_id = uuid.UUID(state["run_id"])
    progress = ctx.get("progress_callback", lambda *a: None)

    raise_if_cancelled(state["run_id"])
    if state.get("status") == "failed":
        return {}
    if "prepare_target" in state.get("completed_nodes", []):
        return {}

    await publisher.publish(
        db, run_id=run_id, event_type="node.started",
        payload={"node": "prepare_target", "progress": 0.9},
        autocommit=True,
    )
    progress("prepare_target", "started", 0.9)

    try:
        source_id = state.get("source_script_artifact_id")
        if not source_id:
            raise ValueError("缺少服务端解析的 source_script_artifact_id")
        project_id = uuid.UUID(state["project_id"])

        source = await artifact_svc.get_version(db, uuid.UUID(source_id))
        if (
            source.project_id != project_id
            or source.type != "script_draft"
            or source.status != "valid"
        ):
            raise ValueError("修订目标不是当前项目的有效剧本版本")
        episode = source.episode_number

        store = ArtifactStore()
        sb = await store.get_latest(db, project_id, "story_bible", 1)
        outline = await store.get_latest(db, project_id, "episode_outline_set", 1)
        if sb is None or outline is None:
            raise ValueError("缺少 StoryBible 或大纲，无法执行对话式修订")

        # 各集最新 valid 剧本映射：continuity 回放 1..ep-1 集需要
        scripts = await store.list_by_project(
            db, project_id, "script_draft", offset=0, limit=1000
        )
        script_ids: dict[int, str] = {}
        for a in scripts:
            if a.status == "valid" and a.episode_number not in script_ids:
                script_ids[a.episode_number] = str(a.id)
        # 目标集显式指向服务端解析的 source（不要求是最新版本）
        script_ids[episode] = str(source.id)

        await publisher.publish(
            db, run_id=run_id, event_type="node.completed",
            payload={
                "node": "prepare_target", "episode": episode,
                "source_script_artifact_id": str(source.id), "progress": 1.0,
            },
            autocommit=True,
        )
        progress("prepare_target", "completed", 1.0)

        return {
            "story_bible_artifact_id": str(sb.id),
            "outline_set_artifact_id": str(outline.id),
            "script_artifact_ids": {str(ep): sid for ep, sid in script_ids.items()},
            "revision_candidate_episode": episode,
            "revision_round": 0,
            "completed_nodes": state.get("completed_nodes", []) + ["prepare_target"],
        }
    except Exception as e:
        logger.exception("prepare_target 节点失败")
        await publisher.publish(
            db, run_id=run_id, event_type="node.failed",
            payload={"node": "prepare_target", "error": str(e)},
            autocommit=True,
        )
        return node_failure("prepare_target", e)


async def ensure_evaluation_node(state: CreationState) -> dict[str, Any]:
    """确保目标剧本有绑定评估；缺失时仅评估目标集并持久化报告。"""
    ctx = _ctx()
    db = ctx["db"]
    agent: BaseAgent = ctx["agent"]
    prompt_loader: PromptLoader = ctx["prompt_loader"]
    publisher: EventPublisher = ctx["event_publisher"]
    run_id = uuid.UUID(state["run_id"])
    progress = ctx.get("progress_callback", lambda *a: None)

    raise_if_cancelled(state["run_id"])
    if state.get("status") == "failed":
        return {}
    if "ensure_evaluation" in state.get("completed_nodes", []):
        return {}

    await publisher.publish(
        db, run_id=run_id, event_type="node.started",
        payload={"node": "ensure_evaluation", "progress": 0.95},
        autocommit=True,
    )
    progress("ensure_evaluation", "started", 0.95)

    try:
        project_id = uuid.UUID(state["project_id"])
        episode = state.get("revision_candidate_episode")
        source_id = state.get("source_script_artifact_id")
        if episode is None or not source_id:
            raise ValueError("缺少修订目标上下文")

        repo = ArtifactRepository(db)
        bound = await repo.find_evaluation_for_script(
            project_id, uuid.UUID(source_id)
        )
        if bound is not None:
            eval_id = str(bound.id)
            logger.info("第 %d 集已有绑定评估 %s，跳过评估", episode, eval_id)
        else:
            evaluator = _build_eval_agent(agent)
            report = await EvaluationService().evaluate_script(
                db,
                project_id=project_id,
                script_artifact_id=uuid.UUID(source_id),
                evaluator=evaluator,
                prompt_loader=prompt_loader,
            )
            eval_id = str(report.id)
            await publisher.publish(
                db, run_id=run_id, event_type="artifact.created",
                payload={
                    "artifact_id": eval_id, "artifact_type": "evaluation_report",
                    "episode": episode, "version": report.version,
                    "message": f"第 {episode} 集目标剧本缺少评估，已先评估",
                },
                autocommit=True,
            )

        await publisher.publish(
            db, run_id=run_id, event_type="node.completed",
            payload={"node": "ensure_evaluation", "episode": episode, "progress": 1.0},
            autocommit=True,
        )
        progress("ensure_evaluation", "completed", 1.0)

        return {
            "evaluation_artifact_ids": {
                **state.get("evaluation_artifact_ids", {}),
                str(episode): eval_id,
            },
            # 用户指定的修订不进入自动修订循环
            "needs_revision_decision": False,
            "completed_nodes": state.get("completed_nodes", []) + ["ensure_evaluation"],
            "prompt_versions": {
                **state.get("prompt_versions", {}),
                "ensure_evaluation": prompt_loader.get("evaluate_episode").version,
            },
        }
    except Exception as e:
        logger.exception("ensure_evaluation 节点失败")
        await publisher.publish(
            db, run_id=run_id, event_type="node.failed",
            payload={"node": "ensure_evaluation", "error": str(e)},
            autocommit=True,
        )
        return node_failure("ensure_evaluation", e)
