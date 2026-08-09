"""select_revision 节点 — 确定性选集与修订轮次推进 (F-05).

从 State 的评估报告 Artifact 中按纯函数 select_revision_candidate
选出最低分集（仅 need_revision=true、同分取最小集号），并把本轮集号写入
State。**不调用 LLM** —— 修订"改哪一集"的决策必须确定性、可审计。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from langgraph.config import get_config

from app.application.artifact_service import ArtifactService
from app.domain.evaluation import EvaluationReport
from app.domain.revision import select_revision_candidate
from app.events.publisher import EventPublisher
from app.workflows.state import CreationState

logger = logging.getLogger(__name__)


def _ctx() -> dict[str, Any]:
    return cast(dict[str, Any], get_config()["configurable"])  # type: ignore[redundant-cast]


async def select_revision_node(state: CreationState) -> dict[str, Any]:
    """确定性选出最低分集并推进 revision_round。

    revision_round 在此自增，代表"本轮修订已选定并开始"。
    原子性保证：节点崩溃时返回值不入 State 且未进 completed_nodes，
    重试不会重复自增（详见 F-05 验收"重试不会重复增加 revision_round"）。
    """
    ctx = _ctx()
    db = ctx["db"]
    artifact_svc: ArtifactService = ctx["artifact_service"]
    publisher: EventPublisher = ctx["event_publisher"]
    run_id = uuid.UUID(state["run_id"])
    progress = ctx.get("progress_callback", lambda *a: None)

    if "select_revision" in state.get("completed_nodes", []):
        return {}

    eval_ids = state.get("evaluation_artifact_ids", {})
    if not eval_ids:
        logger.warning("没有评估报告，跳过选集节点")
        return {"completed_nodes": state.get("completed_nodes", []) + ["select_revision"]}

    await publisher.publish(
        db, run_id=run_id, event_type="node.started",
        payload={"node": "select_revision", "evaluation_count": len(eval_ids), "progress": 0.99},
        autocommit=True,
    )
    progress("select_revision", "started", 0.99)

    try:
        # F-06：预置候选（用户显式指定单集）。存在时不走确定性自动选集，
        # 仅校验其评估报告在 State 中可寻址；缺失则直接判定失败。
        preset_ep = state.get("revision_candidate_episode")
        if preset_ep is not None:
            if str(preset_ep) not in eval_ids:
                logger.error("预置待修订集 %d 无评估报告", preset_ep)
                await publisher.publish(
                    db, run_id=run_id, event_type="node.failed",
                    payload={"node": "select_revision", "error": f"第 {preset_ep} 集无评估报告"},
                    autocommit=True,
                )
                return {
                    "status": "failed",
                    "error_node": "select_revision",
                    "error_detail": f"第 {preset_ep} 集无评估报告",
                }
            new_round = state.get("revision_round", 0) + 1
            await publisher.publish(
                db, run_id=run_id, event_type="node.completed",
                payload={
                    "node": "select_revision",
                    "selected_episode": preset_ep,
                    "preset": True,
                    "revision_round": new_round,
                    "progress": 1.0,
                },
                autocommit=True,
            )
            progress("select_revision", "completed", 1.0)
            return {
                "revision_candidate_episode": preset_ep,
                "revision_round": new_round,
                "completed_nodes": state.get("completed_nodes", []) + ["select_revision"],
            }

        reports: list[EvaluationReport] = []
        for aid in eval_ids.values():
            artifact = await artifact_svc.get_version(db, uuid.UUID(aid))
            reports.append(EvaluationReport.model_validate(artifact.content))

        selected = select_revision_candidate(reports)
        if selected is None:
            logger.info("全部评估通过，无需修订")
            await publisher.publish(
                db, run_id=run_id, event_type="node.completed",
                payload={"node": "select_revision", "selected_episode": None, "progress": 1.0},
                autocommit=True,
            )
            progress("select_revision", "completed", 1.0)
            return {
                "needs_revision_decision": False,
                "revision_candidate_episode": None,
                "completed_nodes": state.get("completed_nodes", []) + ["select_revision"],
            }

        new_round = state.get("revision_round", 0) + 1
        logger.info(
            "第 %d 轮修订选中第 %d 集 (overall=%.1f)",
            new_round, selected.episode_number, selected.overall_score,
        )
        await publisher.publish(
            db, run_id=run_id, event_type="node.completed",
            payload={
                "node": "select_revision",
                "selected_episode": selected.episode_number,
                "overall_score": selected.overall_score,
                "revision_round": new_round,
                "progress": 1.0,
            },
            autocommit=True,
        )
        progress("select_revision", "completed", 1.0)

        return {
            "revision_candidate_episode": selected.episode_number,
            "revision_round": new_round,
            "completed_nodes": state.get("completed_nodes", []) + ["select_revision"],
        }
    except Exception as e:
        logger.exception("选集节点失败")
        await publisher.publish(
            db, run_id=run_id, event_type="node.failed",
            payload={"node": "select_revision", "error": str(e)},
            autocommit=True,
        )
        return {"status": "failed", "error_node": "select_revision", "error_detail": str(e)}
