"""SSE 流端点 — Server-Sent Events 实时推送。

支持：
- 实时订阅（Redis pub/sub）
- heartbeat 保活
- Last-Event-ID 断线补发（从 PostgreSQL 查询历史事件）
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.core.config import Settings
from app.events.publisher import EventPublisher
from app.events.schemas import WorkflowEventSchema

router = APIRouter(tags=["events"])


async def _event_generator(
    run_id: uuid.UUID,
    last_event_id: str | None,
    db_factory: Any,
    settings: Settings,
) -> Any:
    """SSE 事件生成器——先补发历史，再实时推送。"""
    publisher = EventPublisher()
    redis_client = publisher._get_redis()

    # 阶段 1：补发历史事件
    if last_event_id:
        async with db_factory() as db:
            try:
                events = await publisher.get_events_after(db, run_id, last_event_id)
                for event in events:
                    yield WorkflowEventSchema.from_orm(event).to_sse()
            finally:
                await db.close()

    # 阶段 2：实时订阅 + heartbeat
    heartbeat = settings.sse_heartbeat_seconds
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def _redis_listener() -> None:
        if redis_client is None:
            return
        try:
            pubsub = redis_client.pubsub()
            await pubsub.subscribe(f"run:{run_id}")
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    await queue.put(data)
        except Exception:
            pass

    listener_task = asyncio.create_task(_redis_listener()) if redis_client else None

    try:
        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            except TimeoutError:
                yield ": heartbeat\n\n"
    finally:
        if listener_task:
            listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await listener_task

    # 阶段 3：检查 Run 是否结束
    async with db_factory() as db:
        try:
            from sqlalchemy import select

            from app.db.models.workflow_run import WorkflowRun

            result = await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            run = result.scalar_one_or_none()
            if run and run.status in ("completed", "failed", "cancelled"):
                yield f"event: run_ended\ndata: {json.dumps({'status': run.status})}\n\n"
        finally:
            await db.close()


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: uuid.UUID,
    request: Request,
    last_event_id: str | None = Query(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """SSE 事件流端点。

    客户端通过 Last-Event-ID header 传入最后收到的事件 ID，
    服务端补发之后的所有事件，然后推送实时事件。
    """
    settings: Settings = request.app.state.settings
    from app.db.session import _async_session_factory

    return StreamingResponse(
        _event_generator(run_id, last_event_id, _async_session_factory, settings),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
