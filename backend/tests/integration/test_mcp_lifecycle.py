"""MCP ClientManager 的 FastAPI lifespan 集成测试（MCP-01）。

验证（真实 lifespan，含 DB init / dispatcher）：
- MCP_ENABLED=false：不创建 Manager、不连接网络；
- ASGI 注入的 Streamable HTTP Server：启动后 available、关闭后 disabled；
- 不可达 Server：只标记 degraded，应用启动不受影响；
- 任一 Server 故障不影响其他 Server 与核心链路。

不访问真实互联网：HTTP 路径经 httpx2 ASGITransport 注入，
不可达路径指向本机未监听端口（连接拒绝）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi import FastAPI

import app.integrations.mcp.manager as mcp_manager_module
from app.core.config import Settings
from app.main import create_app


def _test_db_url() -> str:
    return os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://drama:drama@localhost:5432/drama_test",
    )


@pytest.fixture
async def mcp_http_server() -> AsyncGenerator[tuple[Any, Any], None]:
    """官方 in-process Server + 其 Streamable HTTP ASGI app（无网络）。"""
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(name="lifecycle-server", version="0.9.0")

    @server.tool()
    def lookup(keyword: str) -> str:
        """Lookup a keyword."""
        return f"hit:{keyword}"

    # session manager 任务组由各测试在自己的 lifespan 窗口内启动
    yield server, server.streamable_http_app()


async def _run_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """以 async with 语义运行真实 lifespan。"""
    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        yield


class TestMCPLifecycle:
    async def test_disabled_creates_no_manager(self, test_engine: Any) -> None:
        """MCP_ENABLED=false：lifespan 完成且 app.state.mcp_manager 为 None。"""
        settings = Settings(
            app_env="test",
            database_url=_test_db_url(),
            mcp_enabled=False,
            mcp_servers_json='[{"id": "x", "url": "https://mcp.example.com"}]',
        )
        app = create_app(settings)
        async for _ in _run_lifespan(app):
            assert app.state.mcp_manager is None

    async def test_enabled_with_streamable_http_server(
        self, test_engine: Any, mcp_http_server: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ASGI 注入的 Server：startup 发现工具、调用成功、shutdown 关闭。"""
        server, asgi = mcp_http_server

        def patched_build(settings: Any) -> Any:
            from httpx2 import ASGITransport, AsyncClient

            from app.integrations.mcp.manager import MCPClientManager
            from app.integrations.mcp.protocol import load_mcp_server_configs

            configs = load_mcp_server_configs(settings)
            assert configs, "配置了 MCP_SERVERS_JSON，必须产生 Server 配置"

            def factory(headers: dict[str, str]) -> Any:
                return AsyncClient(
                    transport=ASGITransport(app=asgi), headers=headers, timeout=5
                )

            return MCPClientManager(
                configs, app_env="test", http_client_factories={configs[0].id: factory}
            )

        monkeypatch.setattr(mcp_manager_module, "build_mcp_manager", patched_build)

        settings = Settings(
            app_env="test",
            database_url=_test_db_url(),
            mcp_enabled=True,
            mcp_servers_json=(
                '[{"id": "research", "url": "http://127.0.0.1:9000/mcp",'
                ' "allowed_tools": ["lookup"], "allow_private_network": true,'
                ' "timeout_seconds": 5}]'
            ),
        )
        app = create_app(settings)
        manager: Any = None
        # Streamable HTTP 传输需要 Server 端 session manager 任务组在运行
        async with server.session_manager.run():
            async for _ in _run_lifespan(app):
                manager = app.state.mcp_manager
                assert manager is not None
                state = manager.get_server_state("research")
                assert state is not None and state.status == "available", state
                assert state.protocol_version
                assert state.server_name == "lifecycle-server"
                assert state.tool_count >= 1
                result = await manager.call_tool("research", "lookup", {"keyword": "abc"})
                assert result.is_error is False
        # lifespan 退出后 Manager 已关闭
        assert manager is not None
        assert manager.server_states()["research"].status == "disabled"

    async def test_unreachable_server_degraded_startup_ok(self, test_engine: Any) -> None:
        """不可达 Server 只标记 degraded，应用启动与关闭不受影响（真实构建路径）。"""
        settings = Settings(
            app_env="test",
            database_url=_test_db_url(),
            mcp_enabled=True,
            mcp_servers_json=(
                '[{"id": "dead", "url": "http://127.0.0.1:9/mcp",'
                ' "allow_private_network": true, "timeout_seconds": 2}]'
            ),
        )
        app = create_app(settings)
        async for _ in _run_lifespan(app):
            manager = app.state.mcp_manager
            assert manager is not None
            state = manager.get_server_state("dead")
            assert state is not None
            assert state.status == "degraded"
            assert state.error_code in (
                "MCP_SERVER_UNAVAILABLE",
                "MCP_DISCOVERY_TIMEOUT",
                "MCP_PROTOCOL_ERROR",
            )
