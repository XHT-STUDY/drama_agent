"""MCP 本地互操作 smoke（MCP-04，人工验收脚本）。

在真实 TCP 上启动官方 SDK 的 Streamable HTTP Server（uvicorn），用
DramaAgent 的生产路径（MCPServerClient → streamable_http_client + Bearer
Token）完成：协议协商 → tools/list → tools/call → 结果规范化 → 关闭。

不访问互联网、不使用付费账号或生产 Token；结果记录到 docs/MCP_TEST_REPORT.md。

用法：cd backend && uv run python scripts/mcp_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.integrations.mcp.adapter import register_mcp_remote_tools  # noqa: E402
from app.integrations.mcp.execution import MCPExecutionService, MCPToolCallCommand  # noqa: E402
from app.integrations.mcp.manager import MCPClientManager  # noqa: E402
from app.integrations.mcp.protocol import MCPServerConfig  # noqa: E402
from app.tools.registry import ToolRegistry  # noqa: E402

TOKEN_ENV = "MCP_SMOKE_TOKEN"
TOKEN_VALUE = "smoke-local-token-not-a-secret"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _build_server() -> Any:
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(name="drama-smoke-server", version="1.0.0")

    @server.tool()
    def search(query: str) -> str:
        """检索 足球 青训 资料。"""
        return f"smoke-hit: {query}"

    @server.tool()
    def boom() -> str:
        """总是失败。"""
        raise RuntimeError("boom")

    return server


async def run() -> dict[str, Any]:
    """实际 smoke 主流程（后台 uvicorn + 生产 Client 路径）。"""
    from importlib.metadata import version

    import uvicorn

    port = _free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    server = _build_server()
    asgi = server.streamable_http_app()
    config = uvicorn.Config(asgi, host="127.0.0.1", port=port, log_level="warning")
    uvi = uvicorn.Server(config)

    report: dict[str, Any] = {
        "sdk_version": version("mcp"),
        "server_name": "drama-smoke-server",
        "endpoint": url,
        "bearer_token_env": TOKEN_ENV,
        "tools": [],
    }

    serve_task = asyncio.create_task(uvi.serve())
    for _ in range(100):
        if uvi.started:
            break
        await asyncio.sleep(0.1)

    try:
        manager = MCPClientManager(
            [MCPServerConfig(
                id="smoke",
                url=url,
                allowed_tools=["search", "boom"],
                bearer_token_env=TOKEN_ENV,
                allow_private_network=True,
                timeout_seconds=10.0,
            )],
            app_env="local",
            environ={TOKEN_ENV: TOKEN_VALUE},
        )
        await manager.startup()
        try:
            state = manager.get_server_state("smoke")
            report["status"] = state.status if state else "unknown"
            report["protocol_version"] = state.protocol_version if state else None
            report["server_version"] = state.server_version if state else None
            report["capabilities"] = state.capabilities if state else {}

            # tools/list（catalog）
            tools = manager.list_tools("smoke")
            report["tools"] = [t.tool_name for t in tools]

            # ToolRegistry 注册（生产接线形态）
            registry = ToolRegistry()
            registered = register_mcp_remote_tools(registry, manager)
            report["registered"] = registered

            # tools/call 成功
            service = MCPExecutionService(manager)
            result = await service.execute(
                MCPToolCallCommand(
                    server_id="smoke",
                    tool_name="search",
                    arguments={"query": "足球青训"},
                    purpose="smoke",
                )
            )
            report["call"] = {
                "tool": "search",
                "text": result.llm_readable_text(),
                "structured": result.structured_content,
                "duration_ms": result.duration_ms,
                "request_id": result.request_id,
                "is_error": result.is_error,
            }

            # tools/call 失败（isError → MCP_TOOL_ERROR）
            try:
                await service.execute(
                    MCPToolCallCommand(server_id="smoke", tool_name="boom", arguments={})
                )
                report["error_call"] = "UNEXPECTED-OK"
            except Exception as exc:  # noqa: BLE001
                report["error_call"] = f"{type(exc).__name__}: {getattr(exc, 'code', '')}"
        finally:
            await manager.shutdown()
    finally:
        uvi.should_exit = True
        await serve_task

    report["cleanup"] = "uvicorn exited, manager shutdown"
    return report


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))
