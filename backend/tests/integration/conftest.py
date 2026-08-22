"""集成测试共享 fixtures。

提供 test 环境下的数据库引擎、FastAPI app 和 HTTP 客户端实例。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import app.db.models  # noqa: F401  # 确保 create_all 发现所有模型
from app.core.config import Settings
from app.db.base import Base
from app.main import create_app

# ---- 测试数据库 URL ----


def _get_test_db_url() -> str:
    """获取测试数据库 URL。

    优先使用 TEST_DATABASE_URL 环境变量；
    否则使用默认的 test 数据库（在同一个 PostgreSQL 实例上）。
    """
    return os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://drama:drama@localhost:5432/drama_test",
    )


# ---- 数据库引擎（session 级，所有集成测试共享） ----


@pytest_asyncio.fixture(scope="session")
async def test_engine() -> Any:
    """会话级异步数据库引擎。

    创建所有表，在所有测试结束后销毁。
    使用 NullPool 避免跨测试连接泄漏。
    """
    db_url = _get_test_db_url()
    os.environ.setdefault("TEST_DATABASE_URL", db_url)
    engine = create_async_engine(db_url, poolclass=None)  # NullPool

    # 手动创建测试数据库（如果不存在）
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

    # 创建所有表
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    from app.workflows.persistence import setup_checkpoint_schema

    checkpoint_settings = Settings(app_env="test", database_url=db_url)
    await setup_checkpoint_schema(checkpoint_settings)

    yield engine

    # 清理
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


# ---- 数据库清理（每个测试函数自动执行） ----


@pytest_asyncio.fixture(autouse=True)
async def clean_db(test_engine: Any) -> AsyncGenerator[None, None]:
    """每个测试函数前清空所有数据库表。

    依赖 test_engine 确保只在需要数据库的测试中触发（health 测试不使用 test_engine）。
    按 FK 依赖逆序删除保证无外键冲突。
    """
    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    yield


# ---- HTTP 客户端（不需要数据库的轻量版） ----


@pytest_asyncio.fixture
async def async_client_no_db() -> AsyncGenerator[AsyncClient, None]:
    """异步 HTTP 客户端（不依赖数据库引擎）。

    用于不需要数据库的简单集成测试。
    """
    settings = Settings(app_env="test")
    settings.apply_env_overrides()
    app = create_app(settings=settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---- async_client 别名（供 tests/integration 根级测试使用） ----


@pytest_asyncio.fixture
async def async_client(async_client_no_db: AsyncClient) -> AsyncGenerator[AsyncClient, None]:
    """async_client 别名，复用 async_client_no_db 的逻辑。

    根级 integration 测试（如 test_health.py）不访问子目录 conftest，
    此 fixture 确保它们能使用 async_client。
    """
    yield async_client_no_db
