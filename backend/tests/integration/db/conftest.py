"""数据库集成测试共享 fixtures。

提供异步会话管理，每个测试运行在独立事务中，测试结束时回滚。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest_asyncio.fixture
async def test_session(test_engine: Any) -> AsyncGenerator[AsyncSession, None]:
    """每个测试函数的独立数据库会话。

    测试结束时自动回滚事务，不污染数据库。
    """
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
