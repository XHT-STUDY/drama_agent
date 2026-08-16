"""redis_client — 惰性共享 Redis 客户端（G-01）。

短期记忆等新模块统一从这里获取 Redis 客户端；
EventPublisher 的私有 _get_redis 保持不动（不重构工作代码，
待后续再统一到本模块）。

策略（best effort）：
- 首次调用时惰性建立连接；
- 连接失败后标记不可用，后续调用直接抛 RedisUnavailableError，
  由调用方按降级策略处理（短期记忆回退 DB 恢复）。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_redis: Any | None = None
_redis_failed = False


class RedisUnavailableError(Exception):
    """Redis 不可用——调用方应降级而非崩溃。"""


async def get_redis() -> Any:
    """惰性获取共享 Redis 客户端（decode_responses=True）。

    Returns:
        可用的 redis.asyncio 客户端。

    Raises:
        RedisUnavailableError: 连接建立失败或此前已标记不可用。
    """
    global _redis, _redis_failed

    if _redis_failed:
        raise RedisUnavailableError("Redis 不可用（此前连接失败）")

    if _redis is None:
        try:
            import redis.asyncio as aioredis

            from app.core.config import Settings

            settings = Settings()
            _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            # 首次连接探测，尽早暴露配置错误
            await _redis.ping()
            logger.info("Redis 客户端就绪: %s", settings.redis_url)
        except Exception as e:  # noqa: BLE001 — 探测失败整体降级
            _redis_failed = True
            logger.warning("Redis 连接失败，短期记忆降级为 DB 恢复: %s", e)
            raise RedisUnavailableError(str(e)) from e

    return _redis


async def close_redis() -> None:
    """关闭共享 Redis 客户端并重置状态（测试收尾 / 应用关闭时调用）。"""
    global _redis, _redis_failed

    if _redis is not None:
        try:
            await _redis.aclose()
        except Exception:  # noqa: BLE001 — 关闭失败无需上抛
            logger.debug("Redis 客户端关闭失败", exc_info=True)
    _redis = None
    _redis_failed = False
