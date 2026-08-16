"""RAG 集成测试共享 fixtures。

复用父级 test_engine / clean_db（见 tests/integration/conftest.py），
并提供每个测试独立事务的 test_session。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest_asyncio.fixture
async def test_session(test_engine: Any) -> AsyncGenerator[AsyncSession, None]:
    """每个测试函数的独立数据库会话（结束自动回滚）。"""
    session_factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        await session.begin()
        try:
            yield session
        finally:
            await session.rollback()
