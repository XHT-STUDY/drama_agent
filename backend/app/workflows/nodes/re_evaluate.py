"""re_evaluate 节点 — 对修订候选稿重新评估 (F-05).

候选稿通过连续性检查（已提升为 valid）后对其执行全新评估:
- 原始分从 RevisionPlan.source_evaluation_artifact_id 取（权威——
  该 ID 指向修订前的评估，不会被 evaluation_artifact_ids[ep] 覆盖）;
- 修订稿是新 Artifact ID → 不会命中旧评估的幂等复用，生成全新评估且只绑新稿;
- 若 旧分 - 新分 > _SCORE_DROP_MANUAL_REVIEW_THRESHOLD（5 分）:
  视为"修订反而变差"，转人工审查（needs_manual_review=True）;
- 更新 evaluation_artifact_ids[ep]，并遍历全部评估报告重算
  needs_revision_decision（任一 need_revision 即 True）——供 Router
  判断是进入下一轮修订还是最终收尾。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from langgraph.config import get_config

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.application.evaluation_service import EvaluationService
from app.domain.evaluation import EvaluationReport
from app.domain.revision import RevisionPlan
from app.events.publisher import EventPublisher
from app.prompts.loader import PromptLoader
from app.workflows.checkpoint import node_failure, raise_if_cancelled
from app.workflows.nodes.evaluate_episode import _build_eval_agent
from app.workflows.state import CreationState

logger = logging.getLogger(__name__)

# 重评显著下降阈值：修订稿比原稿低超过 5 分视为"修订失败"，转人工审查
_SCORE_DROP_MANUAL_REVIEW_THRESHOLD = 5.0


def _ctx() -> dict[str, Any]:
    return cast(dict[str, Any], get_config()["configurable"])  # type: ignore[redundant-cast]


async def re_evaluate_node(state: CreationState) -> dict[str, Any]:
    """对修订候选稿重新评估，并更新评估状态与决策标志。"""
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

    if "re_evaluate" in state.get("completed_nodes", []):
        return {}

    candidate_ep = state.get("revision_candidate_episode")
    plan_artifact_id = state.get("revision_plan_artifact_id")
    if candidate_ep is None or plan_artifact_id is None:
        logger.warning("缺少修订上下文，跳过重评节点")
        return {"completed_nodes": state.get("completed_nodes", []) + ["re_evaluate"]}

    await publisher.publish(
        db, run_id=run_id, event_type="node.started",
        payload={"node": "re_evaluate", "episode": candidate_ep, "progress": 0.99},
        autocommit=True,
    )
    progress("re_evaluate", "started", 0.99)

    try:
        plan = RevisionPlan.model_validate(
            (await artifact_svc.get_version(db, uuid.UUID(plan_artifact_id))).content
        )
        new_draft_id = state["script_artifact_ids"][str(candidate_ep)]

        # 1. 原始分（权威：修订计划引用的评估报告）
        original_eval = EvaluationReport.model_validate(
            (await artifact_svc.get_version(db, plan.source_evaluation_artifact_id)).content
        )
        old_score = original_eval.overall_score

        # 2. 对新稿执行全新评估（新 artifact id → 不命中幂等，只绑新稿）
        evaluator = _build_eval_agent(agent)
        svc = EvaluationService()
        new_report = await svc.evaluate_script(
            db,
            project_id=project_id,
            script_artifact_id=uuid.UUID(new_draft_id),
            evaluator=evaluator,
            prompt_loader=prompt_loader,
        )
        new_report_model = EvaluationReport.model_validate(new_report.content)
        new_score = new_report_model.overall_score
        logger.info(
            "第 %d 集重评完成: %s → %s (delta=%.1f)",
            candidate_ep, old_score, new_score, new_score - old_score,
        )

        # 3. 更新评估 ID 映射；除候选集外，其余集原评估不动
        eval_ids = {
            **state.get("evaluation_artifact_ids", {}),
            str(candidate_ep): str(new_report.id),
        }

        # 4. 重算 needs_revision_decision（任一 need_revision 即 True）
        any_need_revision = new_report_model.need_revision
        for ep_str, aid in eval_ids.items():
            if ep_str == str(candidate_ep):
                continue
            report = EvaluationReport.model_validate(
                (await artifact_svc.get_version(db, uuid.UUID(aid))).content
            )
            if report.need_revision:
                any_need_revision = True

        # 5. 显著下降 → 修订失败，转人工审查
        manual_review = False
        manual_review_reason: str | None = None
        if old_score - new_score > _SCORE_DROP_MANUAL_REVIEW_THRESHOLD:
            manual_review = True
            manual_review_reason = (
                f"第 {candidate_ep} 集修订后重评显著下降: "
                f"{old_score:.1f} → {new_score:.1f}"
                f"（下降 {old_score - new_score:.1f} 分，超过阈值"
                f" {_SCORE_DROP_MANUAL_REVIEW_THRESHOLD:.0f} 分）"
            )

        await publisher.publish(
            db, run_id=run_id, event_type="artifact.created",
            payload={
                "artifact_id": str(new_report.id), "artifact_type": "evaluation_report",
                "episode": candidate_ep, "version": new_report.version,
                "overall_score": new_score, "need_revision": new_report_model.need_revision,
                "message": f"第 {candidate_ep} 集修订稿重评完成",
            },
            autocommit=True,
        )
        await publisher.publish(
            db, run_id=run_id, event_type="node.completed",
            payload={
                "node": "re_evaluate", "episode": candidate_ep,
                "old_score": old_score, "new_score": new_score,
                "needs_manual_review": manual_review, "progress": 1.0,
            },
            autocommit=True,
        )
        progress("re_evaluate", "completed", 1.0)

        return {
            "evaluation_artifact_ids": eval_ids,
            "needs_revision_decision": any_need_revision,
            "needs_manual_review": manual_review,
            "needs_manual_review_reason": manual_review_reason,
            "completed_nodes": state.get("completed_nodes", []) + ["re_evaluate"],
            "prompt_versions": {
                **state.get("prompt_versions", {}),
                "re_evaluate": prompt_loader.get("evaluate_episode").version,
            },
        }
    except Exception as e:
        logger.exception("重评节点失败")
        await publisher.publish(
            db, run_id=run_id, event_type="node.failed",
            payload={"node": "re_evaluate", "error": str(e)},
            autocommit=True,
        )
        return node_failure("re_evaluate", e)
