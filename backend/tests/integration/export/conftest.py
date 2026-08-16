"""Export 集成测试共享 fixtures (G-05)。

复用 tests/integration/conftest.py 的 test_engine / clean_db，
补提供 db_session / test_project / artifact_service
（与 workflow/conftest.py 同款，供导出测试播种 Artifact）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.artifact_service import ArtifactService


@pytest_asyncio.fixture
async def db_session(test_engine: Any) -> AsyncGenerator[Any, None]:
    """为导出测试提供 DB 会话（autocommit 风格，退出时回滚）。"""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def test_project(db_session: Any) -> uuid.UUID:
    """在测试 DB 中创建 Project。"""
    from app.db.models.project import Project

    pid = uuid.uuid4()
    project = Project(id=pid, title="G-05 导出测试项目", status="draft")
    db_session.add(project)
    await db_session.flush()
    return pid


@pytest.fixture
def artifact_service() -> ArtifactService:
    """Artifact 应用服务（播种与断言用）。"""
    return ArtifactService()
