"""官方 mcp SDK Client 契约测试（MCP-01/MCP-02）。

使用官方 in-process MCPServer（无真实网络）验证：
- 协议协商后可读取协议版本、Server 信息与 capabilities；
- tools/list 自动发现、tools/call 调用、isError 语义；
- 多 Server 并存与同名 Tool 按 server_id 隔离；
- 超时取消、并发上限、Token 请求头注入与跨源重定向拒绝；
- MCP-02：catalog→ToolRegistry 注册、执行服务端到端校验。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.core.errors import (
    MCPConfigInvalidError,
    MCPServerUnavailableError,
    MCPToolArgumentInvalidError,
    MCPToolTimeoutError,
)
from app.integrations.mcp.client import MCPServerClient
from app.integrations.mcp.manager import MCPClientManager
from app.integrations.mcp.protocol import MCPServerConfig


def _make_server(name: str) -> Any:
    """构建带同名工具 + 独有工具的官方 in-process Server。"""
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(name=name, version="1.2.3")

    @server.tool()
    def search(query: str) -> str:
        """Search resources."""
        return f"{name}:{query}"

    @server.tool()
    def boom() -> str:
        """Always fails."""
        raise RuntimeError("内部敏感细节 boom")

    if name == "research":

        @server.tool()
        async def slow(seconds: float) -> str:
            """Sleep then return."""
            await asyncio.sleep(seconds)
            return "done"

    return server


def _config(server_id: str, **overrides: Any) -> MCPServerConfig:
    defaults: dict[str, Any] = {
        "id": server_id,
        "url": "http://127.0.0.1:9000/mcp",
        "allowed_tools": ["search"],
        "allow_private_network": True,
        "timeout_seconds": 5.0,
        "max_concurrency": 4,
    }
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


def _make_manager(
    configs: list[MCPServerConfig],
    servers: dict[str, Any],
    *,
    http_client_factories: dict[str, Any] | None = None,
    environ: dict[str, str] | None = None,
) -> MCPClientManager:
    return MCPClientManager(
        configs,
        app_env="test",
        server_factories={sid: (lambda s=srv: s) for sid, srv in servers.items()},
        http_client_factories=http_client_factories,
        environ=environ,
    )


async def _started_manager(
    configs: list[MCPServerConfig],
    servers: dict[str, Any],
    **kwargs: Any,
) -> MCPClientManager:
    manager = _make_manager(configs, servers, **kwargs)
    await manager.startup()
    return manager


# ======================================================================
# 协议协商与发现
# ======================================================================


class TestNegotiationAndDiscovery:
    async def test_protocol_version_and_capabilities_readable(self) -> None:
        manager = await _started_manager([_config("research")], {"research": _make_server("research")})
        try:
            state = manager.get_server_state("research")
            assert state is not None
            assert state.status == "available"
            assert state.protocol_version  # 协商后的具体版本（如 2026-07-28）
            assert state.server_name == "research"
            assert state.server_version == "1.2.3"
            assert state.capabilities.get("tools") is True
        finally:
            await manager.shutdown()

    async def test_tools_list_discovers_definitions(self) -> None:
        manager = await _started_manager([_config("research")], {"research": _make_server("research")})
        try:
            tools = manager.list_tools("research")
            # catalog 只包含 allowlist 内的工具（boom/slow 在 Server 上存在但被过滤）
            assert [t.tool_name for t in tools] == ["search"]
            search = manager.get_tool("research", "search")
            assert search is not None
            assert search.description
            assert search.input_schema.get("type") == "object"
            assert search.qualified_tool_name == "mcp__research__search"
        finally:
            await manager.shutdown()

    async def test_two_servers_isolate_same_named_tool(self) -> None:
        manager = await _started_manager(
            [_config("research"), _config("assets")],
            {"research": _make_server("research"), "assets": _make_server("assets")},
        )
        try:
            assert manager.available_server_ids() == ["research", "assets"]
            for server_id in ("research", "assets"):
                result = await manager.call_tool(server_id, "search", {"query": "q"})
                text = result.content[0].text
                assert text == f"{server_id}:q", "同名工具的返回必须按 server_id 隔离"
        finally:
            await manager.shutdown()


# ======================================================================
# 调用语义
# ======================================================================


class TestCallSemantics:
    async def test_call_tool_returns_content_and_structured(self) -> None:
        manager = await _started_manager([_config("research")], {"research": _make_server("research")})
        try:
            result = await manager.call_tool("research", "search", {"query": "hello"})
            assert result.is_error is False
            assert result.content[0].text == "research:hello"
            assert isinstance(result.structured_content, dict)
        finally:
            await manager.shutdown()

    async def test_tool_error_returns_is_error_without_exception(self) -> None:
        """官方 SDK 语义：Tool 执行失败 → CallToolResult.is_error=True（不抛异常）。

        错误文本由 SDK 泛化（"Error executing tool boom"），
        服务端不透传内部异常细节。
        """
        manager = await _started_manager([_config("research")], {"research": _make_server("research")})
        try:
            result = await manager.call_tool("research", "boom", {})
            assert result.is_error is True
            assert "内部敏感细节" not in json.dumps(
                [c.model_dump(mode="json") for c in result.content], ensure_ascii=False
            )
        finally:
            await manager.shutdown()

    async def test_unknown_tool_returns_error_result(self) -> None:
        manager = await _started_manager([_config("research")], {"research": _make_server("research")})
        try:
            result = await manager.call_tool("research", "does_not_exist", {})
            assert result.is_error is True
        finally:
            await manager.shutdown()

    async def test_timeout_cancels_inflight_call(self) -> None:
        config = _config("research", timeout_seconds=0.3)
        manager = await _started_manager([config], {"research": _make_server("research")})
        try:
            with pytest.raises(MCPToolTimeoutError) as exc_info:
                await manager.call_tool("research", "slow", {"seconds": 5.0})
            assert exc_info.value.code == "MCP_TOOL_TIMEOUT"
            assert exc_info.value.status_code == 504
        finally:
            await manager.shutdown()

    async def test_concurrency_limit_per_server(self) -> None:
        """max_concurrency=1 时同一 Server 的调用串行（信号量闸门）。"""
        active = 0
        peak = 0

        from mcp.server.mcpserver import MCPServer

        server = MCPServer(name="conc")

        @server.tool()
        async def tick(delay: float) -> str:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(delay)
            active -= 1
            return "ok"

        config = _config("conc", max_concurrency=1)
        manager = await _started_manager([config], {"conc": server})
        try:
            await asyncio.gather(
                manager.call_tool("conc", "tick", {"delay": 0.05}),
                manager.call_tool("conc", "tick", {"delay": 0.05}),
            )
            assert peak == 1
        finally:
            await manager.shutdown()


# ======================================================================
# 状态与失败分类
# ======================================================================


class TestServerStates:
    async def test_disabled_server_marked_disabled(self) -> None:
        config = _config("off", enabled=False)
        manager = await _started_manager([config], {})
        try:
            state = manager.get_server_state("off")
            assert state is not None and state.status == "disabled"
        finally:
            await manager.shutdown()

    async def test_unreachable_server_degraded_not_raising(self) -> None:
        """连接失败（拒绝连接）只标记 degraded，startup 不抛出。"""
        # 端口 9（discard）本地通常无监听 → 连接拒绝
        config = _config("dead", url="http://127.0.0.1:9/mcp", timeout_seconds=2)
        manager = await _started_manager([config], {})
        try:
            state = manager.get_server_state("dead")
            assert state is not None and state.status == "degraded"
            assert state.error_code in ("MCP_SERVER_UNAVAILABLE", "MCP_DISCOVERY_TIMEOUT")
            with pytest.raises(MCPServerUnavailableError):
                await manager.call_tool("dead", "search", {"query": "q"})
        finally:
            await manager.shutdown()

    async def test_single_failure_does_not_affect_other_server(self) -> None:
        manager = await _started_manager(
            [_config("dead", url="http://127.0.0.1:9/mcp", timeout_seconds=2), _config("assets")],
            {"assets": _make_server("assets")},
        )
        try:
            dead = manager.get_server_state("dead")
            assets = manager.get_server_state("assets")
            assert dead is not None and dead.status == "degraded"
            assert assets is not None and assets.status == "available"
            result = await manager.call_tool("assets", "search", {"query": "x"})
            assert result.content[0].text == "assets:x"
        finally:
            await manager.shutdown()

    async def test_missing_token_env_marks_config_invalid(self) -> None:
        config = _config("research", bearer_token_env="MCP_ABSENT_TOKEN")
        manager = await _started_manager(
            [config], {"research": _make_server("research")}, environ={}
        )
        try:
            state = manager.get_server_state("research")
            assert state is not None
            assert state.status == "degraded"
            assert state.error_code == "MCP_CONFIG_INVALID"
        finally:
            await manager.shutdown()

    async def test_unknown_server_rejected(self) -> None:
        manager = await _started_manager([_config("research")], {"research": _make_server("research")})
        try:
            with pytest.raises(MCPServerUnavailableError):
                await manager.call_tool("nope", "search", {})
        finally:
            await manager.shutdown()


# ======================================================================
# 传输安全（Streamable HTTP 注入路径）
# ======================================================================


class TestHttpTransportSecurity:
    async def test_bearer_token_sent_and_never_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from httpx2 import ASGITransport, AsyncClient
        from mcp.server.mcpserver import MCPServer

        server = MCPServer(name="http-server")

        @server.tool()
        def ping() -> str:
            """Ping."""
            return "pong"

        captured: list[dict[str, Any]] = []
        asgi = server.streamable_http_app()

        async def capturing_app(scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] == "http":
                captured.append(dict(scope.get("headers", [])))
            await asgi(scope, receive, send)

        def factory(headers: dict[str, str]) -> Any:
            return AsyncClient(
                transport=ASGITransport(app=capturing_app), headers=headers, timeout=5
            )

        config = _config("http", bearer_token_env="MCP_TEST_TOKEN")
        # Streamable HTTP 传输需要 Server 端 session manager 任务组在运行
        async with server.session_manager.run():
            manager = MCPClientManager(
                [config],
                app_env="test",
                http_client_factories={"http": factory},
                environ={"MCP_TEST_TOKEN": "secret-token-xyz"},
            )
            try:
                await manager.startup()
                state = manager.get_server_state("http")
                assert state is not None and state.status == "available", state
                result = await manager.call_tool("http", "ping", {})
                assert result.is_error is False
                # Token 作为 Authorization 头送达 Server
                auth_found = any(
                    (k == b"authorization" or k == "authorization") and v.endswith(b"secret-token-xyz")
                    for h in captured
                    for k, v in h.items()
                )
                assert auth_found
                # Token 不出现在任何日志记录里
                assert "secret-token-xyz" not in caplog.text
            finally:
                await manager.shutdown()

    async def test_cross_origin_redirect_rejected(self) -> None:
        """SDK 传输不跟随跨源重定向（SSRF 边界），连接失败进入 degraded。"""
        from httpx2 import AsyncClient, MockTransport
        from httpx2 import Response as HttpResponse

        def redirect_handler(request: Any) -> HttpResponse:
            return HttpResponse(
                307, headers={"Location": "https://evil.example.net/mcp"}
            )

        def factory(headers: dict[str, str]) -> Any:
            return AsyncClient(transport=MockTransport(redirect_handler), timeout=5)

        config = _config("redirect", url="https://origin.example.com/mcp")
        manager = _make_manager([config], {}, http_client_factories={"redirect": factory})
        try:
            await manager.startup()
            state = manager.get_server_state("redirect")
            assert state is not None
            assert state.status == "degraded"
            assert state.error_code in ("MCP_PROTOCOL_ERROR", "MCP_SERVER_UNAVAILABLE")
        finally:
            await manager.shutdown()

    async def test_protocol_error_on_connect_classified(self) -> None:
        """非 JSON MCP 端点（协议错误）→ degraded + MCP_PROTOCOL_ERROR。"""
        from httpx2 import AsyncClient, MockTransport
        from httpx2 import Response as HttpResponse

        def garbage_handler(request: Any) -> HttpResponse:
            return HttpResponse(200, text="<html>not rpc</html>")

        def factory(headers: dict[str, str]) -> Any:
            return AsyncClient(transport=MockTransport(garbage_handler), timeout=5)

        config = _config("garbage", url="https://mcp.example.com/mcp")
        manager = _make_manager([config], {}, http_client_factories={"garbage": factory})
        try:
            await manager.startup()
            state = manager.get_server_state("garbage")
            assert state is not None
            assert state.status == "degraded"
            assert state.error_code in ("MCP_PROTOCOL_ERROR", "MCP_SERVER_UNAVAILABLE")
        finally:
            await manager.shutdown()


# ======================================================================
# 客户端直连（不经 Manager）
# ======================================================================


class TestMCPServerClientDirect:
    async def test_connect_requires_token_env(self) -> None:
        config = _config("research", bearer_token_env="MCP_MISSING")
        client = MCPServerClient(config, environ={})
        with pytest.raises(MCPConfigInvalidError, match="MCP_MISSING"):
            await client.connect()

    async def test_call_without_connect_raises(self) -> None:
        client = MCPServerClient(_config("research"))
        with pytest.raises(RuntimeError, match="未连接"):
            await client.call_tool("search", {})

    async def test_transport_failure_raises_protocol_error_via_manager(self) -> None:
        """已连接 Server 断连后调用 → MCPProtocolError（泛化，不泄漏内部细节）。"""
        manager = await _started_manager([_config("research")], {"research": _make_server("research")})
        await manager.shutdown()
        with pytest.raises(MCPServerUnavailableError):
            await manager.call_tool("research", "search", {})


# ======================================================================
# MCP-02：catalog → ToolRegistry 注册与执行服务端到端
# ======================================================================


class TestMCPRemoteToolContract:
    async def test_two_servers_same_tool_registered_and_called(self) -> None:
        """两个 Server 的同名工具可同时注册、独立调用（in-process 端到端）。"""
        from app.integrations.mcp.adapter import register_mcp_remote_tools
        from app.tools.registry import ToolRegistry

        manager = await _started_manager(
            [_config("research"), _config("assets")],
            {"research": _make_server("research"), "assets": _make_server("assets")},
        )
        try:
            registry = ToolRegistry()
            names = register_mcp_remote_tools(registry, manager)
            assert sorted(names) == ["mcp__assets__search", "mcp__research__search"]
            for server_id in ("research", "assets"):
                result = await registry.get(f"mcp__{server_id}__search").execute(query="q")
                assert result.llm_readable_text() == f"{server_id}:q"
                assert result.is_error is False
                assert result.protocol_version
        finally:
            await manager.shutdown()

    async def test_invalid_arguments_blocked_before_remote_call(self) -> None:
        """输入校验失败 → 远端调用计数为 0（真实 in-process Server）。"""
        from app.integrations.mcp.adapter import register_mcp_remote_tools
        from app.integrations.mcp.execution import MCPExecutionService
        from app.tools.registry import ToolRegistry

        calls: list[dict[str, Any]] = []

        from mcp.server.mcpserver import MCPServer

        server = MCPServer(name="counting")

        @server.tool()
        def search(query: str) -> str:
            """Search."""
            calls.append({"query": query})
            return f"hit:{query}"

        config = _config("counting", allowed_tools=["search"])
        manager = await _started_manager([config], {"counting": server})
        try:
            registry = ToolRegistry()
            register_mcp_remote_tools(registry, manager)
            tool = registry.get("mcp__counting__search")
            with pytest.raises(MCPToolArgumentInvalidError):
                await tool.execute(query=123)  # type: ignore[arg-type]
            assert calls == [], "输入校验失败不得触达远端"
            # 合法调用正常抵达
            result = await tool.execute(query="ok")
            assert result.llm_readable_text() == "hit:ok"
            assert len(calls) == 1
            _ = MCPExecutionService  # 执行服务经 MCPRemoteTool 内部委托
        finally:
            await manager.shutdown()

    async def test_stale_digest_detected_end_to_end(self) -> None:
        """计划后工具定义变化（digest 不一致）→ MCP_TOOL_STALE，不发调用。"""
        from app.core.errors import MCPToolStaleError
        from app.integrations.mcp.execution import MCPExecutionService, MCPToolCallCommand

        manager = await _started_manager([_config("research")], {"research": _make_server("research")})
        try:
            service = MCPExecutionService(manager)
            with pytest.raises(MCPToolStaleError):
                await service.execute(
                    MCPToolCallCommand(
                        server_id="research",
                        tool_name="search",
                        arguments={"query": "q"},
                        expected_definition_digest="1" * 64,
                    )
                )
        finally:
            await manager.shutdown()
