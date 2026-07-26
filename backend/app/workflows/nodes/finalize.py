"""finalize 节点 — 工作流收尾 (C-07)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langgraph.config import get_config

from app.events.publisher import EventPublisher
from app.workflows.state import CreationState

logger = logging.getLogger(__name__)


def _ctx() -> dict[str, Any]:
    return get_config()["configurable"]  # type: ignore[no-any-return]


async def finalize_node(state: CreationState) -> dict[str, Any]:
    """工作流收尾：更新 Run 状态为 completed。"""
    ctx = _ctx()
    db = ctx["db"]
    run_svc: Any = ctx["run_service"]
    publisher: EventPublisher = ctx["event_publisher"]
    run_id = uuid.UUID(state["run_id"])
    progress = ctx.get("progress_callback", lambda *a: None)

    if "finalize" in state.get("completed_nodes", []):
        return {}

    await publisher.publish(
        db, run_id=run_id, event_type="node.started",
        payload={"node": "finalize", "progress": 0.90},
    )
    progress("finalize", "started", 0.90)

    try:
        scripts = state.get("script_artifact_ids", {})
        total = 1 + 1 + 1 + len(scripts)  # req + sb + outline + scripts

        await run_svc.transition_status(db, run_id, "completed")

        await publisher.publish(
            db, run_id=run_id, event_type="run.completed",
            payload={
                "total_artifacts": total,
                "script_count": len(scripts),
                "prompt_versions": state.get("prompt_versions", {}),
                "progress": 1.0,
            },
        )
        progress("finalize", "completed", 1.0)

        return {
            "status": "completed",
            "completed_nodes": state.get("completed_nodes", []) + ["finalize"],
        }
    except Exception as e:
        logger.exception("工作流收尾失败")
        await publisher.publish(
            db, run_id=run_id, event_type="node.failed",
            payload={"node": "finalize", "error": str(e)},
        )
        return {"status": "failed", "error_node": "finalize", "error_detail": str(e)}
