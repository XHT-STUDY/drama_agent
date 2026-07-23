"""集成测试共享 fixtures。

提供 test 环境下的 FastAPI app 和 HTTP 客户端实例。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def test_settings() -> Settings:
    """返回 test 环境的配置实例。

    所有字段使用默认值，LLM/Embedding provider 会被 env override 强制设为 fake。
    """
    settings = Settings(app_env="test")
    settings.apply_env_overrides()
    return settings


@pytest.fixture
def app(test_settings: Settings) -> Any:
    """创建 FastAPI 应用实例（test 配置）。

    返回已注册中间件、异常处理器和路由的完整 app。
    """
    return create_app(settings=test_settings)


@pytest_asyncio.fixture
async def async_client(app: Any) -> AsyncGenerator[AsyncClient, None]:
    """异步 HTTP 客户端，直连 ASGI app（不走网络）。

    所有集成测试应使用此 fixture，
    确保测试快速、确定且不依赖网络栈。
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
