"""API 集成测试共享 fixtures。

组合 DB fixtures 和 HTTP 客户端，
提供完整的 API 级测试环境。
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
    """创建完整的 FastAPI 应用实例（含 DB 初始化）。

    使用 DB 测试引擎而非生产引擎。
    """
    settings = Settings(app_env="test")
    settings.apply_env_overrides()
    app = create_app(settings=settings)

    # 从测试 conftest 传入的 test_engine 初始化 session
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    app.state._test_engine = test_engine

    # 覆盖 session factory 使用测试引擎
    import app.db.session as db_session
    db_session._async_session_factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    return app


@pytest_asyncio.fixture
async def async_client(app: Any) -> AsyncGenerator[AsyncClient, None]:
    """异步 HTTP 客户端，直连 ASGI app（含真实测试 DB）。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    from app.application.workflow_dispatcher import shutdown_dispatcher
    await shutdown_dispatcher()
