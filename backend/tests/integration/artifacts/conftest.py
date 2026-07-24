"""Artifact 集成测试 fixtures。

复用 DB 测试引擎，提供 ArtifactStore 和 HTTP 客户端。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app


@pytest_asyncio.fixture
async def app(test_engine: Any) -> Any:
    """创建完整的 FastAPI 应用实例。"""
    settings = Settings(app_env="test")
    settings.apply_env_overrides()
    app = create_app(settings=settings)

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    import app.db.session as db_session

    db_session._async_session_factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return app


@pytest_asyncio.fixture
async def async_client(app: Any) -> AsyncGenerator[AsyncClient, None]:
    """异步 HTTP 客户端。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
