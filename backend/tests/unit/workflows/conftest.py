"""unit/application 测试的数据库 fixture（J-09）。

normalize 节点测试需要真实 DB 会话（Artifact 持久化路径），
复用 integration 的 test_engine 实现方式；另提供 test_project。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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


@pytest_asyncio.fixture
async def test_project(test_engine: Any) -> AsyncGenerator[uuid.UUID, None]:
    from app.db.models.project import Project

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        project = Project(title="normalize 集数测试", target_episode_count=10)
        session.add(project)
        await session.commit()
        yield project.id
        await session.rollback()
