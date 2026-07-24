"""数据库集成测试共享 fixtures。

提供异步引擎、会话和表管理，
每个测试运行在独立事务中，测试结束时回滚。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 导入所有模型确保 create_all 能发现它们
import app.db.models  # noqa: F401
from app.db.base import Base


def _get_test_db_url() -> str:
    """获取测试数据库 URL。

    优先使用 TEST_DATABASE_URL 环境变量；
    否则使用默认的 test 数据库（在同一个 PostgreSQL 实例上）。
    """
    return os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://drama:drama@localhost:5432/drama_test",
    )


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """会话级异步数据库引擎。

    创建所有表，在所有测试结束后销毁。
    使用 NullPool 避免跨测试连接泄漏。
    """
    db_url = _get_test_db_url()
    engine = create_async_engine(db_url, poolclass=None)  # NullPool

    # 手动创建测试数据库（如果不存在）
    # 尝试连接并创建表
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        # 数据库可能不存在，尝试创建
        import asyncpg

        # 连接到默认数据库创建测试库
        sys_conn = await asyncpg.connect(
            dsn=db_url.replace("/drama_test", "/drama").replace("+asyncpg", ""),
            timeout=5,
        )
        try:
            await sys_conn.execute("CREATE DATABASE drama_test")
        except Exception:
            pass  # 数据库可能已存在
        finally:
            await sys_conn.close()

    # 创建所有表
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # 清理
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


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
