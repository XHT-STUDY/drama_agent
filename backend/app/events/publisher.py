"""EventPublisher — 事件持久化与实时发布。

- 事务内分配递增 sequence（SELECT MAX(sequence) FOR UPDATE）
- INSERT workflow_event 到 PostgreSQL
- Redis publish（best effort：失败不阻塞，不回滚 DB）
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
                self._redis = False  # 标记不可用
        return self._redis if self._redis is not False else None

    async def publish(
        self,
        db: AsyncSession,
        *,
        run_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> WorkflowEvent:
        """持久化事件并尝试 Redis 实时推送。

        步骤：
        1. SELECT MAX(sequence) FOR UPDATE 锁定当前 run 的最大 sequence
        2. INSERT 新事件（sequence = max + 1）
        3. Redis PUBLISH（best effort）
        """
        # 原子分配 sequence
        # 注意：asyncpg 不支持 SELECT MAX(...) FOR UPDATE，
        # 改用 ORDER BY + LIMIT 1 锁定当前最大 sequence 行
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

        # Redis 实时通知（best effort）
        redis_client = self._get_redis()
        if redis_client:
            try:
                from app.events.schemas import WorkflowEventSchema

                msg = WorkflowEventSchema.from_orm(event).model_dump_json()
                await redis_client.publish(f"run:{run_id}", msg)
            except Exception:
                pass  # Redis 故障不影响主流程

        return event

    async def get_events_after(
        self,
        db: AsyncSession,
        run_id: uuid.UUID,
        last_event_id: str | None,
    ) -> list[WorkflowEvent]:
        """查询指定 Run 在 last_event_id 之后的所有事件。

        用于 SSE 断线重连时的 Last-Event-ID 补发。
        """
        stmt = select(WorkflowEvent).where(WorkflowEvent.run_id == run_id)

        if last_event_id:
            try:
                last_uuid = uuid.UUID(last_event_id)
                # 找到该事件的 sequence
                ref_result = await db.execute(
                    select(WorkflowEvent.sequence).where(WorkflowEvent.id == last_uuid)
                )
                ref_seq = ref_result.scalar_one_or_none()
                if ref_seq is not None:
                    stmt = stmt.where(WorkflowEvent.sequence > ref_seq)
            except ValueError:
                pass  # 无效 UUID，返回全部

        stmt = stmt.order_by(WorkflowEvent.sequence.asc())
        result = await db.execute(stmt)
        return list(result.scalars().all())
