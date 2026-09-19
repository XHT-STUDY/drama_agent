"""MCPServerClient — 官方 mcp SDK Client 的单 Server 封装。

职责（MCP-01）：
- 按配置构建官方 SDK Client（生产 Streamable HTTP；测试可注入 in-process
  Server 或自定义 httpx AsyncClient 工厂）；
- 协议协商后记录协议版本、Server 信息与 capabilities；
- 代理 ``tools/list``（自动翻页）与 ``tools/call``。

安全边界：
- Bearer Token 只在连接时从环境变量读取，不保存在实例上；
- 超时不在此层强制（由 MCPClientManager 用总超时包裹，可取消在途调用）。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AsyncExitStack
from typing import Any

from app.core.errors import MCPConfigInvalidError
from app.core.logging import get_logger
from app.integrations.mcp.protocol import MCPServerConfig

logger = get_logger(__name__)

# 官方 SDK 导入收敛在函数内，避免 mcp 未安装时影响其余模块导入
# （mcp>=2 是硬依赖，这里主要为缩短启动导入链）。

# in-process Server 类型（测试注入）：mcp.server.mcpserver.MCPServer 或低层 Server
ServerLike = Any
# httpx AsyncClient 工厂（headers → AsyncClient），测试注入 ASGI transport 用
HttpClientFactory = Callable[[dict[str, str]], Any]


class MCPServerClient:
    """一个远程 MCP Server 的官方 Client 连接。"""

    def __init__(
        self,
        config: MCPServerConfig,
        *,
        server: ServerLike | None = None,
        http_client_factory: HttpClientFactory | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        """初始化连接封装。

        Args:
            config: Server 配置（URL / Token 引用 / 超时）。
            server: 可选 in-process Server（官方 SDK 测试路径，优先于 HTTP）。
            http_client_factory: 可选 httpx AsyncClient 工厂（headers → client）；
                缺省用 SDK 的 create_mcp_http_client。
            environ: 可注入环境变量视图（Bearer Token 读取）。
        """
        self.config = config
        self._server = server
        self._http_client_factory = http_client_factory
        self._environ = environ if environ is not None else dict(os.environ)
        self._stack: AsyncExitStack | None = None
        self._client: Any | None = None
        # 协商结果快照
        self.protocol_version: str | None = None
        self.server_name: str | None = None
        self.server_version: str | None = None
        self.capabilities_summary: dict[str, Any] = {}

    @property
    def connected(self) -> bool:
        return self._client is not None

    def _auth_headers(self) -> dict[str, str]:
        """构建请求头；Bearer Token 从环境变量读取且不落日志。"""
        env_name = self.config.bearer_token_env
        if not env_name:
            return {}
        token = self._environ.get(env_name, "")
        if not token:
            raise MCPConfigInvalidError(
                detail=f"MCP Server {self.config.id} 的 Token 环境变量未设置: {env_name}"
            )
        return {"Authorization": f"Bearer {token}"}

    def _build_client(self, headers: dict[str, str]) -> Any:
        from mcp import Client

        if self._server is not None:
            return Client(self._server)
        from mcp.client.streamable_http import streamable_http_client

        if self._http_client_factory is not None:
            http_client = self._http_client_factory(headers)
        else:
            from mcp.shared._httpx_utils import create_mcp_http_client

            http_client = create_mcp_http_client(headers=headers)
        transport = streamable_http_client(self.config.url, http_client=http_client)
        return Client(transport)

    async def connect(self) -> None:
        """进入 SDK Client 上下文（协议协商），并记录协商结果。

        Bearer Token 环境变量在连接前校验（in-process 注入路径同样要求），
        缺失即 MCPConfigInvalidError，不发起连接。

        Raises:
            MCPConfigInvalidError: Token 环境变量缺失。
            其余异常（网络 / 协议）原样抛出，由调用方分类。
        """
        if self._client is not None:
            return
        headers = self._auth_headers()  # 校验 Token 引用（缺失即失败）
        client = self._build_client(headers)
        stack = AsyncExitStack()
        try:
            entered = await stack.enter_async_context(client)
        except Exception:
            await stack.aclose()
            raise
        self._stack = stack
        self._client = entered
        self.protocol_version = str(getattr(entered, "protocol_version", "") or "") or None
        info = getattr(entered, "server_info", None)
        if info is not None:
            self.server_name = getattr(info, "name", None)
            self.server_version = getattr(info, "version", None) or None
        self.capabilities_summary = _summarize_capabilities(
            getattr(entered, "server_capabilities", None)
        )
        logger.info(
            "MCP Server 连接成功: server=%s protocol=%s name=%s",
            self.config.id,
            self.protocol_version,
            self.server_name,
        )

    async def close(self) -> None:
        """退出 SDK Client 上下文并释放连接。"""
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._client = None

    async def list_tools(self) -> list[Any]:
        """执行 tools/list 并自动翻页，返回全部原始 Tool 定义。"""
        if self._client is None:
            raise RuntimeError("MCPServerClient 未连接")
        tools: list[Any] = []
        cursor: str | None = None
        while True:
            result = await self._client.list_tools(cursor=cursor)
            tools.extend(result.tools)
            cursor = getattr(result, "next_cursor", None) or getattr(result, "nextCursor", None)
            if not cursor:
                return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """执行 tools/call，返回原始 CallToolResult（is_error 由上层解释）。"""
        if self._client is None:
            raise RuntimeError("MCPServerClient 未连接")
        return await self._client.call_tool(name, arguments)


def _summarize_capabilities(capabilities: Any) -> dict[str, Any]:
    """把 capabilities 对象压缩为低基数布尔摘要（可记录、可展示）。"""
    if capabilities is None:
        return {}
    summary: dict[str, Any] = {}
    for field in ("tools", "resources", "prompts", "logging", "completions"):
        value = getattr(capabilities, field, None)
        if value is not None:
            summary[field] = True
            list_changed = getattr(value, "list_changed", None)
            if list_changed is not None:
                summary[f"{field}_list_changed"] = bool(list_changed)
    return summary
