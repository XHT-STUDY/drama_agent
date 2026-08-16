"""evaluate_episodes 节点 — 对已写集剧本进行逐集评估 (E-04).

从 State 读取 script_artifact_ids，调用 EvaluationService.evaluate_many
生成各集评估报告。评估完成后若存在需修订的集，标记 needs_revision_decision，
工作流在修订决策点暂停（Phase F 处理实际修订）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from langgraph.config import get_config

from app.agents.base import BaseAgent
from app.agents.evaluation import EvaluationAgent
from app.application.evaluation_service import EvaluationService
from app.prompts.loader import PromptLoader
from app.skills.evaluator import EvaluationSkill
from app.skills.registry import SkillRegistry
from app.workflows.checkpoint import node_failure, raise_if_cancelled
from app.workflows.state import CreationState

logger = logging.getLogger(__name__)


def _ctx() -> dict[str, Any]:
    return cast(dict[str, Any], get_config()["configurable"])  # type: ignore[redundant-cast]


# Skill 注册一次即复用（模块级缓存）
_eval_registry: SkillRegistry | None = None


def _build_eval_agent(base_agent: BaseAgent) -> EvaluationAgent:
    """构造评估 Agent（复用模块级 SkillRegistry）。"""
    global _eval_registry
    if _eval_registry is None:
        _eval_registry = SkillRegistry()
        _eval_registry.register(EvaluationSkill())
    return EvaluationAgent(base_agent=base_agent, skill_registry=_eval_registry)


async def evaluate_episodes_node(state: CreationState) -> dict[str, Any]:
    """对 State 中已写集剧本逐集评估。"""
    ctx = _ctx()
    db = ctx["db"]
    agent: BaseAgent = ctx["agent"]
    prompt_loader: PromptLoader = ctx["prompt_loader"]
    publisher: Any = ctx["event_publisher"]
    project_id = uuid.UUID(state["project_id"])
    run_id = uuid.UUID(state["run_id"])
    progress = ctx.get("progress_callback", lambda *a: None)

    # 协作式取消守卫（I-01）
    raise_if_cancelled(state["run_id"])

    # 失败短路（I-01）：上游节点已失败则跳过本节点，保持失败状态不变
    if state.get("status") == "failed":
        return {}

    if "evaluate_episodes" in state.get("completed_nodes", []):
        return {}

    script_ids = state.get("script_artifact_ids", {})
    if not script_ids:
        logger.warning("没有可评估的剧本，跳过评估节点")
        return {"completed_nodes": state.get("completed_nodes", []) + ["evaluate_episodes"]}

    await publisher.publish(
        db, run_id=run_id, event_type="node.started",
        payload={"node": "evaluate_episodes", "episode_count": len(script_ids), "progress": 0.86},
        autocommit=True,
    )
    progress("evaluate_episodes", "started", 0.86)

    try:
        evaluator = _build_eval_agent(agent)
        svc = EvaluationService()

        # 按集号排序，保证返回顺序稳定
        ordered = sorted(script_ids.items(), key=lambda kv: int(kv[0]))
        script_ids_sorted = [uuid.UUID(sid) for _, sid in ordered]

        for idx, (ep_num, _sid) in enumerate(ordered):
            await publisher.publish(
                db, run_id=run_id, event_type="node.event",
                payload={
                    "node": "evaluate_episodes",
                    "message": f"正在评估第 {ep_num} 集",
                    "episode": int(ep_num),
                },
                autocommit=True,
            )
            progress("evaluate_episodes", f"ep_{ep_num}_evaluating", 0.86 + (idx + 1) * 0.04)

        reports = await svc.evaluate_many(
            db,
            project_id=project_id,
            script_artifact_ids=script_ids_sorted,
            evaluator=evaluator,
            prompt_loader=prompt_loader,
        )

        evaluation_artifact_ids: dict[str, str] = {}
        any_need_revision = False
        # 集号取自 State 的 script_artifact_ids key（FakeLLM 下 report 集号可能固定为 1）
        for (ep_num, _sid), artifact in zip(ordered, reports, strict=False):
            ep = int(ep_num)
            evaluation_artifact_ids[str(ep)] = str(artifact.id)
            if artifact.content.get("need_revision"):
                any_need_revision = True
            await publisher.publish(
                db, run_id=run_id, event_type="artifact.created",
                payload={
                    "artifact_id": str(artifact.id), "artifact_type": "evaluation_report",
                    "episode": ep, "version": artifact.version,
                    "overall_score": artifact.content.get("overall_score"),
                    "need_revision": artifact.content.get("need_revision"),
                    "progress": 0.86 + ep * 0.03,
                    "message": f"第 {ep} 集评估完成",
                },
                autocommit=True,
            )
            logger.info(
                "第 %d 集评估报告已保存 (id=%s) overall=%s",
                ep, str(artifact.id)[:12], artifact.content.get("overall_score"),
            )
            progress("evaluate_episodes", f"ep_{ep}_done", 0.86 + ep * 0.03)

        await publisher.publish(
            db, run_id=run_id, event_type="node.completed",
            payload={"node": "evaluate_episodes", "evaluated": len(reports), "progress": 0.99},
            autocommit=True,
        )
        progress("evaluate_episodes", "completed", 0.99)

        return {
            "evaluation_artifact_ids": {
                **state.get("evaluation_artifact_ids", {}),
                **evaluation_artifact_ids,
            },
            "needs_revision_decision": any_need_revision,
            "completed_nodes": state.get("completed_nodes", []) + ["evaluate_episodes"],
            "prompt_versions": {
                **state.get("prompt_versions", {}),
                "evaluate_episode": prompt_loader.get("evaluate_episode").version,
            },
        }
    except Exception as e:
        logger.exception("评估节点失败")
        await publisher.publish(
            db, run_id=run_id, event_type="node.failed",
            payload={"node": "evaluate_episodes", "error": str(e)},
            autocommit=True,
        )
        return {
            **node_failure("evaluate_episodes", e),
            "evaluation_artifact_ids": state.get("evaluation_artifact_ids", {}),
        }
