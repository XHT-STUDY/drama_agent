"""mcp_tool_call 工作流测试（MCP-03）。

直接调用单节点工作流（官方 in-process MCP Server 注入 Manager，无网络）：
成功、stale、超时、工具错误、取消与 MCP 不可用降级。
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any

import pytest

from app.core.errors import (
    AppError,
    MCPToolError,
    MCPToolStaleError,
    MCPToolTimeoutError,
)
from app.integrations.mcp.manager import MCPClientManager
from app.integrations.mcp.protocol import MCPServerConfig
from app.integrations.mcp.runtime import set_manager
from app.workflows.checkpoint import RunCancelledError, request_cancel
from app.workflows.mcp_tool_call import build_mcp_tool_call_workflow


def _make_server() -> Any:
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(name="wf-server", version="1.0.0")

    @server.tool()
    def lookup(query: str) -> str:
        """检索 青训 足球 资料。"""
        return f"result for {query}"

    @server.tool()
    def boom() -> str:
        """总是失败。"""
        raise RuntimeError("内部细节")

    @server.tool()
    async def slow(seconds: float) -> str:
        """慢工具。"""
        await asyncio.sleep(seconds)
        return "done"

    return server


def _config(server_id: str = "research", **overrides: Any) -> MCPServerConfig:
    defaults: dict[str, Any] = {
        "id": server_id,
        "url": "http://127.0.0.1:9000/mcp",
        "allowed_tools": ["lookup", "boom", "slow"],
        "allow_private_network": True,
        "timeout_seconds": 5.0,
    }
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


@pytest.fixture
async def mcp_manager() -> Any:
    """已启动的 Manager（in-process Server），结束后清理 runtime 句柄。"""
    manager = MCPClientManager(
        [_config()], app_env="test", server_factories={"research": _make_server}
    )
    await manager.startup()
    set_manager(manager)
    try:
        yield manager
    finally:
        set_manager(None)
        await manager.shutdown()


def _invoke_options(
    tool: str,
    arguments: dict[str, Any],
    digest: str | None = None,
) -> dict[str, Any]:
    return {
        "configurable": {
            "options": {
                "server_id": "research",
                "tool_name": tool,
                "arguments": arguments,
                "tool_definition_digest": digest,
                "purpose": "测试调用",
            }
        }
    }


def _initial_state() -> dict[str, Any]:
    return {
        "run_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "action": "mcp_tool_call",
        "mcp_result": None,
        "status": "running",
        "completed_nodes": [],
        "prompt_versions": {},
    }


class TestMCPToolCallWorkflow:
    async def test_success_writes_normalized_result(self, mcp_manager: Any) -> None:
        digest = (
            mcp_manager.catalog.get_by_names("research", "lookup").definition_digest
        )
        workflow = build_mcp_tool_call_workflow()
        final_state = await workflow.ainvoke(
            _initial_state(), _invoke_options("lookup", {"query": "足球"}, digest)
        )
        assert final_state["status"] == "completed"
        result = final_state["mcp_result"]
        assert result["server_id"] == "research"
        assert result["tool_name"] == "lookup"
        assert result["is_error"] is False
        assert result["content"][0]["text"] == "result for 足球"
        assert result["protocol_version"]
        assert result["request_id"]
        assert final_state["completed_nodes"] == ["mcp_call_tool"]

    async def test_stale_digest_rejected(self, mcp_manager: Any) -> None:
        workflow = build_mcp_tool_call_workflow()
        with pytest.raises(MCPToolStaleError):
            await workflow.ainvoke(
                _initial_state(),
                _invoke_options("lookup", {"query": "x"}, digest="0" * 64),
            )

    async def test_tool_error_raises_mcp_tool_error(self, mcp_manager: Any) -> None:
        digest = mcp_manager.catalog.get_by_names("research", "boom").definition_digest
        workflow = build_mcp_tool_call_workflow()
        with pytest.raises(MCPToolError):
            await workflow.ainvoke(
                _initial_state(), _invoke_options("boom", {}, digest)
            )

    async def test_timeout_cancels_call(self) -> None:
        """总超时取消在途调用（Manager 独立配置 0.3s）。"""
        manager = MCPClientManager(
            [_config(timeout_seconds=0.3)],
            app_env="test",
            server_factories={"research": _make_server},
        )
        await manager.startup()
        set_manager(manager)
        try:
            digest = manager.catalog.get_by_names("research", "slow").definition_digest
            workflow = build_mcp_tool_call_workflow()
            with pytest.raises(MCPToolTimeoutError):
                await workflow.ainvoke(
                    _initial_state(),
                    _invoke_options("slow", {"seconds": 5.0}, digest),
                )
        finally:
            set_manager(None)
            await manager.shutdown()

    async def test_no_manager_fails_unavailable(self) -> None:
        set_manager(None)
        workflow = build_mcp_tool_call_workflow()
        with pytest.raises(AppError) as exc_info:
            await workflow.ainvoke(
                _initial_state(), _invoke_options("lookup", {"query": "x"})
            )
        assert exc_info.value.code == "MCP_SERVER_UNAVAILABLE"

    async def test_cancel_interrupts_inflight_call(self, mcp_manager: Any) -> None:
        """取消标记 → 在途 SDK 调用被中断，RunCancelledError 抛出。"""
        digest = mcp_manager.catalog.get_by_names("research", "slow").definition_digest
        state = _initial_state()
        workflow = build_mcp_tool_call_workflow()
        config = _invoke_options("slow", {"seconds": 5.0}, digest)

        async def _cancel_soon() -> None:
            await asyncio.sleep(0.3)
            request_cancel(state["run_id"])

        cancel_task = asyncio.create_task(_cancel_soon())
        try:
            with pytest.raises(RunCancelledError):
                await workflow.ainvoke(state, config)
        finally:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await cancel_task
