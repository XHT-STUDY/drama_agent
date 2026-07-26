"""retrieve 节点 — 知识库检索（MVP 占位）(C-07).

MVP 阶段 RAG 尚未实现（Phase D），此节点为直通节点。
"""

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


async def retrieve_node(state: CreationState) -> dict[str, Any]:
    """MVP 占位：不做知识库检索，直通下一节点。"""
    ctx = _ctx()
    db = ctx["db"]
    publisher: EventPublisher = ctx["event_publisher"]
    run_id = uuid.UUID(state["run_id"])
    progress = ctx.get("progress_callback", lambda *a: None)

    if "retrieve" in state.get("completed_nodes", []):
        return {}

    await publisher.publish(
        db, run_id=run_id, event_type="node.started",
        payload={"node": "retrieve", "progress": 0.0},
        autocommit=True,
    )
    progress("retrieve", "started", 0.0)

    await publisher.publish(
        db, run_id=run_id, event_type="node.completed",
        payload={"node": "retrieve", "doc_count": 0, "progress": 1.0},
        autocommit=True,
    )
    progress("retrieve", "completed", 1.0)

    ctx["rag_context"] = ""
    return {"completed_nodes": state.get("completed_nodes", []) + ["retrieve"]}
