"""EventPublisher — 事件持久化与实时发布。

- 使用调用方 DB 会话持久化事件
- autocommit 模式：在调用方会话中 commit 事件，使 SSE 客户端可实时看到
- Redis publish（best effort：失败不阻塞，不依赖 Redis）
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.workflow_event import WorkflowEvent


class EventPublisher:
    """Workflow 事件发布器。

    原则（DEV_PLAN §6.3）：
    - 事件持久化在 PostgreSQL
    - Redis 发布失败不回滚 PostgreSQL 事件
    - SSE 可从数据库补发历史事件
    """

    def __init__(self) -> None:
        self._redis: Any = None

    def _get_redis(self) -> Any | None:
        """惰性获取 Redis 客户端（best effort）。"""
        if self._redis is None:
            try:
                import redis.asyncio as aioredis

                from app.core.config import Settings

                settings = Settings()
                self._redis = aioredis.from_url(settings.redis_url)
            except Exception:
                self._redis = False
        return self._redis if self._redis is not False else None

    async def publish(
        self,
        db: AsyncSession,
        *,
        run_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any] | None = None,
        autocommit: bool = False,
    ) -> WorkflowEvent:
        """持久化事件并尝试 Redis 实时推送。

        Args:
            db: 调用方的事务会话
            run_id: 所属 Run UUID
            event_type: 事件类型字符串
            payload: 事件负载
            autocommit: True = 立即 commit（使 SSE 可见）+ 重新 begin（继续后续操作）
        """
        event = await self._insert_event(db, run_id, event_type, payload)

        if autocommit:
            from app.db.session import _async_session_factory
            if _async_session_factory is not None:
                try:
                    await db.commit()
                except Exception as e:
                    import logging
                    logging.getLogger("app.events.publisher").error(
                        "事件 autocommit 失败 (run=%s, seq=%d): %s",
                        str(run_id)[:12], event.sequence, e,
                    )
                    raise
                try:
                    await db.begin()
                except Exception as e:
                    import logging
                    logging.getLogger("app.events.publisher").error(
                        "autocommit 后 begin() 失败 (run=%s, seq=%d): %s",
                        str(run_id)[:12], event.sequence, e,
                    )
                    raise

        # Redis 实时通知（best effort）
        await self._try_redis_publish(event)
        return event

    async def _insert_event(
        self,
        db: AsyncSession,
        run_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any] | None,
    ) -> WorkflowEvent:
        """执行事件 INSERT（含 sequence 原子分配）。"""
        max_seq_result = await db.execute(
            select(WorkflowEvent.sequence)
            .where(WorkflowEvent.run_id == run_id)
            .order_by(WorkflowEvent.sequence.desc())
            .limit(1)
            .with_for_update()
        )
        max_seq: int = max_seq_result.scalar_one_or_none() or 0
        next_sequence = max_seq + 1

        event = WorkflowEvent(
            run_id=run_id,
            sequence=next_sequence,
            type=event_type,
            payload=payload,
        )
        db.add(event)
        await db.flush()
        return event

    async def _try_redis_publish(self, event: WorkflowEvent) -> None:
        """尝试通过 Redis 实时推送事件（best effort）。"""
        redis_client = self._get_redis()
        if redis_client:
            try:
                from app.events.schemas import WorkflowEventSchema

                msg = WorkflowEventSchema.from_orm(event).model_dump_json()
                await redis_client.publish(f"run:{event.run_id}", msg)
            except Exception:
                pass

    async def get_events_after(
        self,
        db: AsyncSession,
        run_id: uuid.UUID,
        last_event_id: str | None,
    ) -> list[WorkflowEvent]:
        """查询指定 Run 在 last_event_id 之后的所有事件。"""
        stmt = select(WorkflowEvent).where(WorkflowEvent.run_id == run_id)

        if last_event_id:
            try:
                last_uuid = uuid.UUID(last_event_id)
                ref_result = await db.execute(
                    select(WorkflowEvent.sequence).where(WorkflowEvent.id == last_uuid)
                )
                ref_seq = ref_result.scalar_one_or_none()
                if ref_seq is not None:
                    stmt = stmt.where(WorkflowEvent.sequence > ref_seq)
            except ValueError:
                pass

        stmt = stmt.order_by(WorkflowEvent.sequence.asc())
        result = await db.execute(stmt)
        return list(result.scalars().all())
