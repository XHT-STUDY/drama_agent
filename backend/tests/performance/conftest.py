"""性能测试共享 fixtures（I-05）。

性能测试运行在真实 PostgreSQL + Redis 上（`make up` 后），
需要与 integration 相同的 DB 引擎、清理与应用构造。
本 conftest 自包含复制 integration/conftest 的最小集，
使 tests/performance/ 独立可跑，不依赖 tests/integration/ 的层级。

注意：性能测试用 `@pytest.mark.performance` 标记，默认 `pytest`
经 addopts `-m "not performance"` 跳过；`make perf` 显式运行。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.db.models as _db_models  # noqa: F401  # 确保 create_all 发现所有模型
from app.core.config import Settings
from app.db.base import Base
from app.main import create_app


def _get_test_db_url() -> str:
    """获取测试数据库 URL（同 integration/conftest）。"""
    return os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://drama:drama@localhost:5432/drama_test",
    )


@pytest_asyncio.fixture(scope="session")
async def test_engine() -> Any:
    """会话级异步数据库引擎（NullPool，测试结束 drop_all）。"""
    db_url = _get_test_db_url()
    # 显式 NullPool：SSE 生成器泄漏的 session 连接不会被池复用为"已关闭"，
    # drop_all 每次取全新连接（create_async_engine(poolclass=None) 实际产生
    # AsyncAdaptedQueuePool，会把坏连接复检出来导致 InterfaceError）。
    engine = create_async_engine(db_url, poolclass=NullPool)

    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        import asyncpg

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

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(test_engine: Any) -> AsyncGenerator[None, None]:
    """每个测试函数前清空所有数据库表（FK 逆序删除）。"""
    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    yield


@pytest_asyncio.fixture
async def app(test_engine: Any) -> Any:
    """完整 FastAPI 应用实例（DB 绑定到测试引擎）。"""
    settings = Settings(app_env="test")
    settings.apply_env_overrides()
    app = create_app(settings=settings)
    app.state._test_engine = test_engine

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    import app.db.session as db_session

    db_session._async_session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False,
    )
    return app


@pytest_asyncio.fixture
async def async_client(app: Any) -> AsyncGenerator[AsyncClient, None]:
    """异步 HTTP 客户端，直连 ASGI app。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
