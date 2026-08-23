"""unit/application 测试的数据库 fixture（J-09）。

AgentOutcomeService 的确定性规则需要真实 ORM 行（AgentAction/WorkflowRun），
这里复用 integration 的 test_engine 实现方式（同一测试库，NullPool）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import app.db.models  # noqa: F401  # 确保 create_all 发现所有模型
from app.db.base import Base


@pytest_asyncio.fixture(scope="session")
async def test_engine() -> AsyncGenerator[Any, None]:
    """会话级测试数据库引擎（与 tests/integration/conftest 同构）。"""
    db_url = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://drama:drama@localhost:5432/drama_test",
    )
    os.environ.setdefault("TEST_DATABASE_URL", db_url)
    engine = create_async_engine(db_url)

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
            await sys_conn.execute('CREATE DATABASE drama_test"'.replace('"', ""))
        except Exception:
            pass  # 已存在（并发创建竞态）
        finally:
            await sys_conn.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine
    await engine.dispose()
