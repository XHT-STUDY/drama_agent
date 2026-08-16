"""MCP（Model Context Protocol）工具适配器（I-04）。

把外部 HTTP JSON-RPC 服务暴露的工具映射为内部 Tool 协议：
- `MCPToolSpec`：外部工具描述（名称 / 说明 / 输入输出 Schema）
- `MCPAdapterConfig`：连接配置（开关 / 地址 / 超时 / 名称前缀）
- `MCPToolAdapter`：一个外部工具对应一个内部 Tool，execute 走 JSON-RPC

边界：
- 只做 HTTP 调用与错误泛化，不缓存、不本地执行外部逻辑；
- 超时 → EXTERNAL_TOOL_TIMEOUT；外部错误 → 泛化 EXTERNAL_TOOL_ERROR，
  不泄漏内部连接信息；
- 无 MCP 配置（mcp_enabled=False，默认）主流程完全不受影响。
"""

from app.integrations.mcp.adapter import MCPToolAdapter
from app.integrations.mcp.protocol import MCPAdapterConfig, MCPToolSpec

__all__ = [
    "MCPToolSpec",
    "MCPAdapterConfig",
    "MCPToolAdapter",
]
