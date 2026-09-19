"""MCP（Model Context Protocol）集成——官方 SDK Tools-only Client（MCP-01+）。

标准接入（mcp>=2，Streamable HTTP）：
- `MCPServerConfig` / `load_mcp_server_configs`：管理员配置（MCP_SERVERS_JSON）
- `MCPServerClient`：单 Server 官方 SDK Client 封装（协商 / 发现 / 调用）
- `MCPClientManager`：多 Server 生命周期、状态与并发控制
- `security`：URL 安全校验（HTTPS 强制、私网/回环拒绝、DNS 解析校验）

兼容（弃用，保留一个发布周期）：
- `MCPToolSpec` / `MCPAdapterConfig` / `MCPToolAdapter`：I-04 手写
  JSON-RPC Adapter，协议底层已由官方 SDK 取代，确认无调用方后删除。

边界：MCP 默认关闭；任何 MCP Server 故障不阻止应用启动、不影响核心创作。
"""

from app.integrations.mcp.client import MCPServerClient
from app.integrations.mcp.manager import MCPClientManager
from app.integrations.mcp.protocol import (
    MCPServerConfig,
    MCPServerState,
    load_mcp_server_configs,
)

__all__ = [
    "MCPServerConfig",
    "MCPServerState",
    "MCPServerClient",
    "MCPClientManager",
    "load_mcp_server_configs",
]
