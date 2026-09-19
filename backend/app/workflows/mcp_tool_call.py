"""MCPToolCallWorkflow — 单工具外部调用工作流（MCP-03）。

单节点工作流：经 MCPExecutionService 调用一个 MCP Tool。
- 调用前二次校验 definition digest（计划后工具变化 → MCP_TOOL_STALE）；
- 一个 Run 只调用一个 Tool，不做工具循环；
- 协作式取消：轮询取消标记，取消时中断在途 SDK 调用（RunCancelledError）；
- 结果写入 state.mcp_result（MCPToolResult 快照），不创建 Artifact、
  不改变采用工作集。

State 只存轻量字段；工具结果受 256 KiB 上限（execution 内截断）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, TypedDict

from langgraph.config import get_config
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core.errors import AppError
from app.integrations.mcp.execution import (
    MCPExecutionService,
    MCPToolCallCommand,
)
from app.integrations.mcp.runtime import get_manager
from app.workflows.checkpoint import (
    RunCancelledError,
    cancel_requested,
    raise_if_cancelled,
)
from app.workflows.node_timing import timed_node

logger = logging.getLogger(__name__)

# 取消轮询间隔（秒）：兼顾响应速度与空转开销
_CANCEL_POLL_SECONDS = 0.25


class MCPToolCallState(TypedDict, total=False):
    """外部工具调用工作流状态。"""

    run_id: str
    project_id: str
    action: str
    status: str
    mcp_result: dict[str, Any] | None
    error_node: str | None
    error_code: str | None
    error_detail: str | None
    completed_nodes: list[str]
    prompt_versions: dict[str, str]


def _get_node_context() -> dict[str, Any]:
    return get_config()["configurable"]


async def _await_with_cancel(run_id: str, coro: Any) -> Any:
    """在取消标记与协程之间竞速：取消时中断在途 SDK 调用。"""
    task = asyncio.ensure_future(coro)
    try:
        while True:
            done, _pending = await asyncio.wait(
                {task}, timeout=_CANCEL_POLL_SECONDS
            )
            if done:
                return task.result()
            if cancel_requested(run_id):
                task.cancel()
                with contextlib.suppress(BaseException):
                    # 取消后的收尾异常无关紧要
                    await task
                raise RunCancelledError(run_id)
    finally:
        if not task.done():
            task.cancel()


async def _mcp_call_tool_node(state: MCPToolCallState) -> dict[str, Any]:
    """调用单个 MCP Tool 并回写规范化结果。"""
    ctx = _get_node_context()
    run_id = state["run_id"]
    options: dict[str, Any] = ctx.get("options") or {}

    raise_if_cancelled(run_id)

    manager = get_manager()
    if manager is None:
        raise AppError(
            detail="MCP 未启用或不可用，无法执行外部工具调用",
            status_code=503,
            code="MCP_SERVER_UNAVAILABLE",
        )

    command = MCPToolCallCommand(
        server_id=str(options.get("server_id", "")),
        tool_name=str(options.get("tool_name", "")),
        arguments=dict(options.get("arguments") or {}),
        expected_definition_digest=options.get("tool_definition_digest"),
        purpose=str(options.get("purpose", "")),
    )
    service = MCPExecutionService(manager)
    result = await _await_with_cancel(run_id, service.execute(command))

    logger.info(
        "MCP 工作流调用完成: run=%s action_id=%s server=%s tool=%s "
        "duration_ms=%s truncated=%s request_id=%s",
        run_id,
        options.get("agent_action_id"),
        result.server_id,
        result.tool_name,
        result.duration_ms,
        result.truncated,
        result.request_id,
    )
    return {
        "status": "completed",
        "mcp_result": result.model_dump(mode="json"),
        "completed_nodes": [*state.get("completed_nodes", []), "mcp_call_tool"],
    }


def build_mcp_tool_call_workflow(
    checkpointer: Any = None,
) -> CompiledStateGraph[MCPToolCallState, Any, Any, Any]:
    """构建单节点外部工具调用工作流。"""
    graph = StateGraph(MCPToolCallState)
    graph.add_node("mcp_call_tool", timed_node("mcp_call_tool", _mcp_call_tool_node))
    graph.set_entry_point("mcp_call_tool")
    graph.add_edge("mcp_call_tool", END)
    return graph.compile(checkpointer=checkpointer)
