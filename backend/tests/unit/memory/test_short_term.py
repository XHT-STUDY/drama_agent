"""G-01 短期记忆存储单元测试。

覆盖：
- InMemoryShortTermStore：push/recent 顺序与窗口裁剪、drop、内存丢失后回退 DB 恢复；
- RedisShortTermStore：脚本化假 Redis 验证 rpush+ltrim+expire 调用、TTL 传递、
  key 格式、读取失败回退 DB 恢复（degrade）。

DB 恢复分支用 monkeypatch 桩函数验证；
真实 DB + 真实 Redis 的恢复场景在 tests/integration/memory 中验证。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

import app.memory.short_term as short_term_mod
from app.memory.short_term import (
    InMemoryShortTermStore,
    RedisShortTermStore,
    ShortTermMessage,
)


class _FakeRedis:
    """脚本化假 Redis：记录调用，可注入读取失败。"""

    def __init__(self) -> None:
        self.store: dict[str, list[str]] = {}
        self.ttls: dict[str, int] = {}
        self.calls: list[str] = []
        self.raise_on_read = False

    async def rpush(self, key: str, value: str) -> int:
        self.calls.append("rpush")
        self.store.setdefault(key, []).append(value)
        return len(self.store[key])

    async def ltrim(self, key: str, start: int, end: int) -> None:
        self.calls.append("ltrim")
        items = self.store.get(key, [])
        self.store[key] = items[start:] if end == -1 else items[start : end + 1]

    async def expire(self, key: str, ttl: int) -> bool:
        self.calls.append("expire")
        self.ttls[key] = ttl
        return True

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        self.calls.append("lrange")
        if self.raise_on_read:
            raise ConnectionError("redis down")
        items = self.store.get(key, [])
        return items[start:] if end == -1 else items[start : end + 1]

    async def delete(self, key: str) -> int:
        self.calls.append("delete")
        return int(key in self.store and self.store.pop(key) is not None)


def _msg(seq: int, role: str = "user") -> dict[str, Any]:
    return {"role": role, "content": f"消息 {seq}", "sequence": seq}


async def _stub_recover(
    monkeypatch: pytest.MonkeyPatch, result: list[ShortTermMessage]
) -> None:
    """用桩替换 recover_from_db，隔离真实 DB 依赖。"""

    async def _fake(db: Any, conversation_id: uuid.UUID, n: int) -> list[ShortTermMessage]:
        return result

    monkeypatch.setattr(short_term_mod, "recover_from_db", _fake)


# ========================================================================
# InMemoryShortTermStore
# ========================================================================


class TestInMemoryShortTermStore:
    """内存实现：窗口裁剪 / 顺序 / drop / 恢复。"""

    @pytest.mark.asyncio
    async def test_push_and_recent_returns_last_n_ascending(self) -> None:
        """push 后 recent 返回最近 n 条且为升序。"""
        store = InMemoryShortTermStore(keep_count=10)
        conv = uuid.uuid4()
        for seq in range(1, 6):
            await store.push(None, conv, **_msg(seq))

        recent = await store.recent(None, conv, 3)
        assert [m.sequence for m in recent] == [3, 4, 5]

    @pytest.mark.asyncio
    async def test_window_trims_to_keep_count(self) -> None:
        """超过 keep_count 后只保留最近 keep_count 条。"""
        store = InMemoryShortTermStore(keep_count=3)
        conv = uuid.uuid4()
        for seq in range(1, 7):  # 6 条，窗口 3
            await store.push(None, conv, **_msg(seq))

        recent = await store.recent(None, conv, 10)
        assert [m.sequence for m in recent] == [4, 5, 6]

    @pytest.mark.asyncio
    async def test_drop_clears_then_recovers_from_db(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """drop 清空后，recent 回退 DB 恢复（内存丢失不丢消息）。"""
        store = InMemoryShortTermStore(keep_count=5)
        conv = uuid.uuid4()
        await store.push(None, conv, **_msg(1))

        sentinel = [ShortTermMessage(**_msg(1))]
        await _stub_recover(monkeypatch, sentinel)

        await store.drop(conv)
        result = await store.recent(None, conv, 5)
        assert result == sentinel

    @pytest.mark.asyncio
    async def test_memory_miss_recovers_from_db(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """内存为空（模拟清空）时 recent 回退 DB 恢复。"""
        store = InMemoryShortTermStore(keep_count=5)
        conv = uuid.uuid4()
        sentinel = [ShortTermMessage(**_msg(1))]
        await _stub_recover(monkeypatch, sentinel)

        result = await store.recent(None, conv, 5)
        assert result == sentinel


# ========================================================================
# RedisShortTermStore（脚本化假 Redis）
# ========================================================================


class TestRedisShortTermStore:
    """Redis 实现：写路径调用、TTL、key 格式、读取失败降级。"""

    @pytest.mark.asyncio
    async def test_push_writes_rpush_ltrim_expire(self) -> None:
        """push 依次调用 rpush + ltrim + expire。"""
        fake = _FakeRedis()
        store = RedisShortTermStore(keep_count=12, ttl_seconds=3600, redis_client=fake)
        conv = uuid.uuid4()

        await store.push(None, conv, **_msg(1))
        assert fake.calls == ["rpush", "ltrim", "expire"]

    @pytest.mark.asyncio
    async def test_push_sets_configured_ttl(self) -> None:
        """expire 使用构造时传入的 TTL（配置滑动窗口）。"""
        fake = _FakeRedis()
        store = RedisShortTermStore(keep_count=12, ttl_seconds=604800, redis_client=fake)
        conv = uuid.uuid4()

        await store.push(None, conv, **_msg(1))
        key = RedisShortTermStore._key(conv)
        assert fake.ttls[key] == 604800

    @pytest.mark.asyncio
    async def test_push_trims_list_to_keep_count(self) -> None:
        """push 超过窗口后 list 只保留最近 keep_count 条。"""
        fake = _FakeRedis()
        store = RedisShortTermStore(keep_count=3, ttl_seconds=100, redis_client=fake)
        conv = uuid.uuid4()
        for seq in range(1, 6):
            await store.push(None, conv, **_msg(seq))

        key = RedisShortTermStore._key(conv)
        assert len(fake.store[key]) == 3

    @pytest.mark.asyncio
    async def test_key_format(self) -> None:
        """key 格式为 short_term:{conversation_id}。"""
        conv = uuid.uuid4()
        assert RedisShortTermStore._key(conv) == f"short_term:{conv}"

    @pytest.mark.asyncio
    async def test_recent_reads_from_redis(self) -> None:
        """recent 命中 Redis 时解析 ShortTermMessage 并取最近 n 条。"""
        fake = _FakeRedis()
        store = RedisShortTermStore(keep_count=12, ttl_seconds=100, redis_client=fake)
        conv = uuid.uuid4()
        for seq in range(1, 6):
            await store.push(None, conv, **_msg(seq))

        recent = await store.recent(None, conv, 2)
        assert [m.sequence for m in recent] == [4, 5]
        assert fake.calls.count("lrange") == 1

    @pytest.mark.asyncio
    async def test_recent_redis_error_recovers_from_db(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Redis 读取失败（degrade）→ 回退 DB 恢复，不抛异常。"""
        fake = _FakeRedis()
        fake.raise_on_read = True
        store = RedisShortTermStore(keep_count=3, ttl_seconds=100, redis_client=fake)
        conv = uuid.uuid4()

        sentinel = [ShortTermMessage(**_msg(1))]
        await _stub_recover(monkeypatch, sentinel)

        result = await store.recent(None, conv, 3)
        assert result == sentinel

    @pytest.mark.asyncio
    async def test_drop_deletes_key(self) -> None:
        """drop 调用 delete 清空 key。"""
        fake = _FakeRedis()
        store = RedisShortTermStore(keep_count=3, ttl_seconds=100, redis_client=fake)
        conv = uuid.uuid4()
        await store.push(None, conv, **_msg(1))

        await store.drop(conv)
        assert "delete" in fake.calls
        key = RedisShortTermStore._key(conv)
        assert key not in fake.store
