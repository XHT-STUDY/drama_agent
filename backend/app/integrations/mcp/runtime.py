"""进程级 MCP Manager 运行时句柄（MCP-03）。

FastAPI lifespan 把 ClientManager 挂到 app.state；后台 Worker（Workflow
Dispatcher）不经过 HTTP 请求栈，经本模块读取当前进程的 Manager。

语义：
- MCP 关闭或未配置时 ``get_manager()`` 返回 None——调用方必须按
  MCP 不可用降级，不得影响核心链路；
- 测试可直接 ``set_manager`` 注入（in-process / ASGI Server）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.integrations.mcp.manager import MCPClientManager

_manager: MCPClientManager | None = None


def set_manager(manager: MCPClientManager | None) -> None:
    """lifespan / 测试设置当前进程的 Manager。"""
    global _manager
    _manager = manager


def get_manager() -> MCPClientManager | None:
    """读取当前进程的 Manager；MCP 未启用时为 None。"""
    return _manager
