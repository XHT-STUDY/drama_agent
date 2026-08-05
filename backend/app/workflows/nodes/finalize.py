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
        autocommit=True,
    )
    progress("finalize", "started", 0.90)

    try:
        scripts = state.get("script_artifact_ids", {})
        total = 1 + 1 + 1 + len(scripts)  # req + sb + outline + scripts

        # 更新项目的 current_episode_count
        from app.db.models.project import Project as ProjectModel
        _project_id = uuid.UUID(state["project_id"])
        project = await db.get(ProjectModel, _project_id)
        if project is not None:
            scripts_count = len(scripts)
            if scripts_count > project.current_episode_count:
                old_count = project.current_episode_count
                project.current_episode_count = scripts_count
                logger.info(
                    "更新项目 %s current_episode_count: %d → %d",
                    str(_project_id)[:12], old_count, scripts_count,
                )

        await run_svc.transition_status(db, run_id, "completed")

        # 先发 node.completed，再发 run.completed（前端依赖此顺序）
        await publisher.publish(
            db, run_id=run_id, event_type="node.completed",
            payload={"node": "finalize", "progress": 1.0},
            autocommit=True,
        )

        await publisher.publish(
            db, run_id=run_id, event_type="run.completed",
            payload={
                "total_artifacts": total,
                "script_count": len(scripts),
                "prompt_versions": state.get("prompt_versions", {}),
                "progress": 1.0,
            },
            autocommit=True,
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
            autocommit=True,
        )
        return {"status": "failed", "error_node": "finalize", "error_detail": str(e)}
