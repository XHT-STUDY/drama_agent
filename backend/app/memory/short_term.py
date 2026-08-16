"""短期记忆存储（G-01）。

设计决策（见 Phase G 计划决策 2）：
- ShortTermStore(ABC) 协议镜像 Real/Fake 决策——
  生产用 RedisShortTermStore（Redis list + TTL），
  单测/降级用 InMemoryShortTermStore。
- **PostgreSQL 是消息事实源**：Redis 只缓存最近 N 条，
  miss / 连接失败时从 Message 表恢复（验收「Redis 丢失不丢消息」）。
- Redis 缓存仅加速 recent()，push 时滑动窗口裁剪 + 重置 TTL。
"""

from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import RedisUnavailableError, get_redis
from app.db.models.message import Message

logger = logging.getLogger(__name__)


class ShortTermMessage(BaseModel):
    """短期记忆中的单条消息（G-01）。"""

    model_config = {"extra": "forbid"}

    role: str = Field(..., description="消息角色（user/assistant/system）")
    content: str = Field(..., description="消息内容")
    sequence: int = Field(..., description="会话内递增序号", ge=1)


async def recover_from_db(
    db: AsyncSession | None,
    conversation_id: uuid.UUID,
    n: int,
) -> list[ShortTermMessage]:
    """从 PostgreSQL（事实源）恢复最近 n 条消息。

    Redis 缓存丢失 / 不可用时调用，保证消息不丢失。
    按 sequence 降序取最近 n 条后反转回升序。
    db 为 None（纯内存调用方）时返回空列表——由调用方保证内存实现
    不会在无 DB 句柄时走到真实恢复分支（单测用桩替换本函数）。
    """
    if db is None:
        return []
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.sequence.desc())
        .limit(n)
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    rows.reverse()
    return [
        ShortTermMessage(role=r.role, content=r.content, sequence=r.sequence)
        for r in rows
    ]


class ShortTermStore(ABC):
    """短期记忆存储协议（G-01）。

    镜像 Real/Fake 决策：生产用 RedisShortTermStore，测试/降级用
    InMemoryShortTermStore。所有实现必须保证「Redis/内存丢失可从 DB 恢复」。
    """

    @abstractmethod
    async def push(
        self,
        db: AsyncSession | None,
        conversation_id: uuid.UUID,
        *,
        role: str,
        content: str,
        sequence: int,
    ) -> None:
        """追加一条消息到短期记忆缓存。"""

    @abstractmethod
    async def recent(
        self,
        db: AsyncSession | None,
        conversation_id: uuid.UUID,
        n: int,
    ) -> list[ShortTermMessage]:
        """返回最近 n 条消息（升序）；缓存 miss 时从 DB 恢复。"""

    @abstractmethod
    async def drop(self, conversation_id: uuid.UUID) -> None:
        """清空指定会话的短期记忆缓存（会话删除时调用）。"""


class RedisShortTermStore(ShortTermStore):
    """Redis 短期记忆实现（G-01）。

    key = short_term:{conversation_id}（Redis list）。
    push 时 RPUSH + LTRIM 到最近 keep 条 + 重置 TTL（滑动窗口）。
    Redis 不可用 / 列表为空但有 DB 消息时回退 DB 恢复。
    """

    def __init__(
        self,
        *,
        keep_count: int = 12,
        ttl_seconds: int = 7 * 24 * 3600,
        redis_client: Any | None = None,
    ) -> None:
        self._keep = max(keep_count, 1)
        self._ttl = ttl_seconds
        self._redis = redis_client
        self._unavailable = False

    @staticmethod
    def _key(conversation_id: uuid.UUID) -> str:
        return f"short_term:{conversation_id}"

    async def _get_redis(self) -> Any | None:
        """惰性获取 Redis 客户端；失败后标记不可用，后续直接返回 None。"""
        if self._unavailable:
            return None
        if self._redis is None:
            try:
                self._redis = await get_redis()
            except RedisUnavailableError:
                self._unavailable = True
                return None
        return self._redis

    async def push(
        self,
        db: AsyncSession | None,
        conversation_id: uuid.UUID,
        *,
        role: str,
        content: str,
        sequence: int,
    ) -> None:
        """写入 Redis；失败仅记录日志（消息已在 DB，不丢失）。"""
        redis = await self._get_redis()
        if redis is None:
            return
        try:
            payload = json.dumps(
                {"role": role, "content": content, "sequence": sequence},
                ensure_ascii=False,
            )
            key = self._key(conversation_id)
            await redis.rpush(key, payload)
            await redis.ltrim(key, -self._keep, -1)
            await redis.expire(key, self._ttl)
        except Exception:  # noqa: BLE001 — best effort
            logger.warning(
                "短期记忆 Redis 写入失败（conversation=%s）: 消息仍在 DB，不丢失",
                conversation_id,
                exc_info=True,
            )

    async def recent(
        self,
        db: AsyncSession | None,
        conversation_id: uuid.UUID,
        n: int,
    ) -> list[ShortTermMessage]:
        """返回最近 n 条（升序）。Redis miss / 失败 → 从 DB 恢复。"""
        redis = await self._get_redis()
        if redis is not None:
            try:
                raw = await redis.lrange(self._key(conversation_id), 0, -1)
                if raw:
                    items = [
                        ShortTermMessage(**json.loads(item)) for item in raw
                    ]
                    return items[-n:]
            except Exception:  # noqa: BLE001 — best effort
                logger.warning(
                    "短期记忆 Redis 读取失败（conversation=%s），回退 DB 恢复",
                    conversation_id,
                    exc_info=True,
                )

        return await recover_from_db(db, conversation_id, n)

    async def drop(self, conversation_id: uuid.UUID) -> None:
        """删除会话短期记忆 key（best effort）。"""
        redis = await self._get_redis()
        if redis is None:
            return
        try:
            await redis.delete(self._key(conversation_id))
        except Exception:  # noqa: BLE001 — best effort
            logger.warning(
                "短期记忆 Redis 删除失败（conversation=%s）", conversation_id,
                exc_info=True,
            )


class InMemoryShortTermStore(ShortTermStore):
    """内存短期记忆实现（G-01）—— 单测 / Redis 降级用。

    与 RedisShortTermStore 同语义：只保留最近 keep_count 条；
    recent() 内存 miss 时同样回退 DB 恢复，保证协议行为一致。
    """

    def __init__(self, *, keep_count: int = 12) -> None:
        self._keep = max(keep_count, 1)
        self._items: dict[str, list[ShortTermMessage]] = {}

    async def push(
        self,
        db: AsyncSession | None,
        conversation_id: uuid.UUID,
        *,
        role: str,
        content: str,
        sequence: int,
    ) -> None:
        key = str(conversation_id)
        items = self._items.setdefault(key, [])
        items.append(
            ShortTermMessage(role=role, content=content, sequence=sequence)
        )
        if len(items) > self._keep:
            del items[: len(items) - self._keep]

    async def recent(
        self,
        db: AsyncSession | None,
        conversation_id: uuid.UUID,
        n: int,
    ) -> list[ShortTermMessage]:
        key = str(conversation_id)
        items = self._items.get(key)
        if items:
            return items[-n:]
        # 内存丢失（模拟 Redis 清空）→ 从 DB 恢复
        return await recover_from_db(db, conversation_id, n)

    async def drop(self, conversation_id: uuid.UUID) -> None:
        self._items.pop(str(conversation_id), None)
