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
from app.db.models.workflow_event import WorkflowEvent
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
    # 立即发送初始注释，确保浏览器 EventSource 连接立即可用
    yield ": connected\n\n"

    publisher = EventPublisher()
    redis_client = publisher._get_redis()

    # 阶段 1：补发历史事件（始终执行，确保新连接也能收到已完成的事件）
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

    import logging
    _logger = logging.getLogger("app.events.stream")

    async def _redis_listener() -> None:
        if redis_client is None:
            _logger.debug("SSE: Redis 不可用，仅使用 DB 轮询回退")
            return
        try:
            pubsub = redis_client.pubsub()
            await pubsub.subscribe(f"run:{run_id}")
            _logger.debug("SSE: Redis 订阅成功 channel=run:%s", run_id)
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    await queue.put(data)
        except Exception as e:
            _logger.warning("SSE: Redis 订阅异常 (将使用 DB 轮询回退): %s", e)

    async def _db_poller() -> None:
        """DB 轮询回退：每 heartbeat 秒查询新事件。

        当 Redis 不可用时，轮询 DB 确保事件仍然流动。
        事件由 EventPublisher 的独立会话立即提交，轮询可及时获取。
        """
        _last_seq = 0
        while True:
            await asyncio.sleep(heartbeat)
            try:
                async with db_factory() as poll_db:
                    try:
                        stmt = (
                            select(WorkflowEvent)
                            .where(WorkflowEvent.run_id == run_id)
                            .where(WorkflowEvent.sequence > _last_seq)
                            .order_by(WorkflowEvent.sequence.asc())
                        )
                        result = await poll_db.execute(stmt)
                        new_events = list(result.scalars().all())
                        for ev in new_events:
                            await queue.put({
                                "event_id": str(ev.id),
                                "run_id": str(ev.run_id),
                                "sequence": ev.sequence,
                                "event_type": ev.type,
                                "payload": ev.payload or {},
                                "timestamp": ev.created_at.isoformat(),
                            })
                            _last_seq = max(_last_seq, ev.sequence)
                    finally:
                        await poll_db.close()
            except Exception as e:
                _logger.debug("DB 轮询查询异常: %s", e)

    listener_task = asyncio.create_task(_redis_listener()) if redis_client else None
    poller_task = asyncio.create_task(_db_poller())  # 始终运行 DB 轮询作为回退

    try:
        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            except TimeoutError:
                yield ": heartbeat\n\n"
    finally:
        for task in [listener_task, poller_task]:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

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
