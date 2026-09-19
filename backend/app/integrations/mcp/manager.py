"""MCPClientManager — 多 Server 生命周期、发现与并发控制（MCP-01）。

由 FastAPI lifespan 创建并挂到 app.state。Manager：
- 创建、持有和关闭官方 SDK Client（经 MCPServerClient 封装）；
- 启动时逐 Server 校验 URL 安全线、协议协商、tools/list 发现；
- 任一 Server 失败只标记 degraded（错误分类见 MCP_* 错误码），
  不中断应用启动——MCP 是可选依赖；
- 对每个 Server 实施并发上限与单次调用总超时（超时取消在途 SDK 调用）；
- 不保存业务状态、不读写数据库、不决定用户权限。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

from app.core.errors import (
    MCPConfigInvalidError,
    MCPDiscoveryTimeoutError,
    MCPProtocolError,
    MCPServerUnavailableError,
    MCPToolTimeoutError,
)
from app.core.logging import get_logger
from app.integrations.mcp.catalog import (
    MCPToolCatalog,
    MCPToolDefinition,
    build_server_definitions,
)
from app.integrations.mcp.client import HttpClientFactory, MCPServerClient, ServerLike
from app.integrations.mcp.protocol import MCPServerConfig, MCPServerState
from app.integrations.mcp.security import assert_resolved_hosts_public, validate_server_url
from app.observability.metrics import (
    mcp_call_duration_seconds,
    mcp_call_total,
    mcp_discovery_total,
    mcp_server_status,
)

logger = get_logger(__name__)


def _iter_exception_tree(exc: BaseException) -> list[BaseException]:
    """展平异常树（anyio TaskGroup 会用 ExceptionGroup 包装子异常）。"""
    tree: list[BaseException] = [exc]
    if isinstance(exc, BaseExceptionGroup):  # noqa: UP038 - 3.11+ 内置
        for sub in exc.exceptions:
            tree.extend(_iter_exception_tree(sub))
    cause = exc.__cause__
    if cause is not None:
        tree.extend(_iter_exception_tree(cause))
    return tree


def _is_connect_error(exc: BaseException) -> bool:
    """连接类异常（不可达 / 断连）判定，供 unavailable 分类。"""
    transport_error: tuple[type[BaseException], ...]
    try:
        from httpx2 import TransportError

        transport_error = (TransportError,)
    except ImportError:  # pragma: no cover - httpx2 随 mcp 安装
        transport_error = ()
    for item in _iter_exception_tree(exc):
        if transport_error and isinstance(item, transport_error):
            return True
        # anyio/SDK 内部包装的断连
        if type(item).__name__ in {
            "BrokenResourceError",
            "ConnectionClosedError",
            "PeerClosedConnectionError",
        }:
            return True
    return False


class _ServerEntry:
    """单个 Server 的运行时句柄：客户端、状态与并发闸。"""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.client: MCPServerClient | None = None
        self.state = MCPServerState(server_id=config.id)
        self.semaphore = asyncio.Semaphore(config.max_concurrency)
        # 最近一次成功发现的原始 Tool 定义（catalog 在 MCP-02 正式化）
        self.tools: list[Any] = []

    def mark(
        self,
        *,
        status: str,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        self.state.status = status
        self.state.error_code = error_code
        self.state.error_detail = error_detail


class MCPClientManager:
    """进程级 MCP Client 管理器（FastAPI lifespan 持有）。"""

    def __init__(
        self,
        configs: list[MCPServerConfig],
        *,
        app_env: str = "local",
        server_factories: Mapping[str, Callable[[], ServerLike]] | None = None,
        http_client_factories: Mapping[str, HttpClientFactory] | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        """初始化 Manager。

        Args:
            configs: 已解析的 Server 配置（enabled=False 的条目标记 disabled）。
            app_env: 运行环境（production 下强制 HTTPS）。
            server_factories: server_id → in-process Server 工厂（官方测试路径）。
            http_client_factories: server_id → httpx AsyncClient 工厂（ASGI 测试注入）。
            environ: 可注入环境变量视图（Bearer Token 读取）。
        """
        self._app_env = app_env
        self._server_factories = dict(server_factories or {})
        self._http_client_factories = dict(http_client_factories or {})
        self._environ = environ
        self._entries: dict[str, _ServerEntry] = {
            config.id: _ServerEntry(config) for config in configs
        }
        self._closed = False
        # 工具目录（allowlist 过滤后的定义 + digest，MCP-02）
        self.catalog = MCPToolCatalog()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """并发连接全部 enabled Server 并执行工具发现。

        单个 Server 失败只标记 degraded；整体永不抛出（MCP 是可选依赖）。
        """
        self._closed = False
        tasks = [
            asyncio.create_task(self._connect_and_discover(entry))
            for entry in self._entries.values()
        ]
        if tasks:
            await asyncio.gather(*tasks)

    async def shutdown(self) -> None:
        """关闭全部客户端（幂等）。"""
        self._closed = True
        for entry in self._entries.values():
            if entry.client is not None:
                try:
                    await entry.client.close()
                except Exception as exc:  # noqa: BLE001 - 关闭失败只记录，不影响其他 Server
                    logger.warning(
                        "MCP Server 关闭异常: server=%s type=%s", entry.config.id, type(exc).__name__
                    )
                entry.client = None
            entry.state.status = "disabled"
            self.catalog.remove_server(entry.config.id)
            mcp_server_status.set(0, server_id=entry.config.id)

    async def _connect_and_discover(self, entry: _ServerEntry) -> None:
        """连接单个 Server：安全校验 → 协商 → 发现，失败标记 degraded。"""
        config = entry.config
        if not config.enabled:
            entry.mark(status="disabled")
            mcp_server_status.set(0, server_id=config.id)
            return
        try:
            validate_server_url(
                config.url,
                allow_private_network=config.allow_private_network,
                app_env=self._app_env,
            )
            assert_resolved_hosts_public(
                config.url,
                allow_private_network=config.allow_private_network,
            )
            injected_server: ServerLike | None = None
            server_factory = self._server_factories.get(config.id)
            if server_factory is not None:
                injected_server = server_factory()
            client = MCPServerClient(
                config,
                server=injected_server,
                http_client_factory=self._http_client_factories.get(config.id),
                environ=self._environ,
            )
            await asyncio.wait_for(
                self._connect_discover(client, entry), timeout=config.timeout_seconds
            )
        except TimeoutError:
            entry.mark(
                status="degraded",
                error_code=MCPDiscoveryTimeoutError.code,
                error_detail=f"连接或工具发现超时（>{config.timeout_seconds}s）",
            )
            mcp_discovery_total.inc(server_id=config.id, status="failed")
            mcp_server_status.set(0, server_id=config.id)
            logger.warning("MCP Server 发现超时: server=%s", config.id)
        except MCPConfigInvalidError as exc:
            entry.mark(
                status="degraded", error_code=exc.code, error_detail=exc.detail
            )
            mcp_discovery_total.inc(server_id=config.id, status="failed")
            mcp_server_status.set(0, server_id=config.id)
            logger.warning("MCP Server 配置非法: server=%s code=%s", config.id, exc.code)
        except BaseException as exc:  # noqa: BLE001 - 启动期分类记录，不中断
            code = (
                MCPServerUnavailableError.code
                if _is_connect_error(exc)
                else MCPProtocolError.code
            )
            entry.mark(
                status="degraded",
                error_code=code,
                error_detail=f"{type(exc).__name__}: {exc}"[:200],
            )
            mcp_discovery_total.inc(server_id=config.id, status="failed")
            mcp_server_status.set(0, server_id=config.id)
            logger.warning(
                "MCP Server 连接失败: server=%s code=%s", config.id, code
            )
        else:
            entry.client = client
            entry.mark(status="available")
            mcp_discovery_total.inc(server_id=config.id, status="ok")
            mcp_server_status.set(1, server_id=config.id)
            # 协商结果写入状态快照（供诊断与后续调用展示）
            entry.state.protocol_version = client.protocol_version
            entry.state.server_name = client.server_name
            entry.state.server_version = client.server_version
            entry.state.capabilities = dict(client.capabilities_summary)
            entry.state.tool_count = len(self.catalog.list_for_server(config.id))
            logger.info(
                "MCP Server 发现完成: server=%s tools=%d（allowlist 过滤后）",
                config.id,
                entry.state.tool_count,
            )

    async def _connect_discover(self, client: MCPServerClient, entry: _ServerEntry) -> None:
        """协商 + tools/list + allowlist 过滤 + catalog 原子发布。"""
        await client.connect()
        entry.tools = await client.list_tools()
        definitions = build_server_definitions(
            entry.config.id, entry.tools, entry.config.allowed_tools
        )
        self.catalog.replace_server_tools(entry.config.id, definitions)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def server_states(self) -> dict[str, MCPServerState]:
        """全部 Server 的状态快照。"""
        return {sid: entry.state.model_copy(deep=True) for sid, entry in self._entries.items()}

    def get_server_state(self, server_id: str) -> MCPServerState | None:
        entry = self._entries.get(server_id)
        return entry.state.model_copy(deep=True) if entry else None

    def available_server_ids(self) -> list[str]:
        """状态为 available 的 Server ID 列表。"""
        return [sid for sid, e in self._entries.items() if e.state.status == "available"]

    def list_tools(self, server_id: str) -> list[Any]:
        """返回该 Server 的 catalog 工具定义（allowlist 过滤后，不发网络请求）。"""
        entry = self._require_entry(server_id)
        return self.catalog.list_for_server(entry.config.id)

    def get_tool(self, server_id: str, tool_name: str) -> MCPToolDefinition | None:
        """按原始工具名查找 catalog 定义；不存在返回 None。"""
        return self.catalog.get_by_names(server_id, tool_name)

    # ------------------------------------------------------------------
    # 调用
    # ------------------------------------------------------------------

    async def call_tool(
        self, server_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        """调用远程 Tool（tools/call 默认不自动重试）。

        总超时通过 asyncio.wait_for 强制，超时取消在途 SDK 调用；
        并发受 Server 级信号量约束。

        Raises:
            MCPServerUnavailableError: Server 未知或非 available。
            MCPToolTimeoutError: 调用超时。
            MCPProtocolError: 传输 / 协议失败。
        """
        entry = self._require_entry(server_id)
        if entry.state.status != "available" or entry.client is None:
            raise MCPServerUnavailableError(
                detail=f"MCP Server 不可用: {server_id}（{entry.state.error_code or '未知状态'}）"
            )
        import time as _time

        started = _time.monotonic()
        async with entry.semaphore:
            try:
                result = await asyncio.wait_for(
                    entry.client.call_tool(tool_name, arguments),
                    timeout=entry.config.timeout_seconds,
                )
            except TimeoutError:
                mcp_call_total.inc(server_id=server_id, status="timeout")
                raise MCPToolTimeoutError(
                    detail=f"MCP Tool 调用超时（>{entry.config.timeout_seconds}s）: {tool_name}"
                ) from None
            except MCPServerUnavailableError:
                raise
            except MCPConfigInvalidError:
                raise
            except BaseException as exc:  # noqa: BLE001 - 传输/协议错误统一泛化
                mcp_call_total.inc(server_id=server_id, status="error")
                if _is_connect_error(exc):
                    raise MCPProtocolError(
                        detail=f"MCP Tool 传输失败: {tool_name}"
                    ) from None
                raise MCPProtocolError(detail=f"MCP Tool 协议错误: {tool_name}") from exc
        elapsed = _time.monotonic() - started
        mcp_call_total.inc(server_id=server_id, status="ok")
        mcp_call_duration_seconds.observe(elapsed, server_id=server_id)
        return result

    async def refresh_tools(self, server_id: str) -> list[Any]:
        """重新执行 tools/list 并原子更新 catalog（stale 恢复入口）。

        刷新失败：catalog 中该 Server 全部工具转 stale（不允许新调用），
        异常分类后抛出。

        Raises:
            MCPServerUnavailableError: Server 非 available。
            MCPToolTimeoutError / MCPProtocolError: 发现失败（分类同调用）。
        """
        entry = self._require_entry(server_id)
        if entry.state.status != "available" or entry.client is None:
            raise MCPServerUnavailableError(
                detail=f"MCP Server 不可用: {server_id}（{entry.state.error_code or '未知状态'}）"
            )
        try:
            tools = await asyncio.wait_for(
                entry.client.list_tools(), timeout=entry.config.timeout_seconds
            )
        except TimeoutError:
            self.catalog.mark_server_stale(server_id)
            raise MCPToolTimeoutError(
                detail=f"MCP 工具列表刷新超时（>{entry.config.timeout_seconds}s）"
            ) from None
        except BaseException as exc:  # noqa: BLE001
            self.catalog.mark_server_stale(server_id)
            raise MCPProtocolError(detail="MCP 工具列表刷新失败") from exc
        entry.tools = tools
        self.catalog.replace_server_tools(
            server_id,
            build_server_definitions(server_id, tools, entry.config.allowed_tools),
        )
        entry.state.tool_count = len(self.catalog.list_for_server(server_id))
        return self.catalog.list_for_server(server_id)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _require_entry(self, server_id: str) -> _ServerEntry:
        entry = self._entries.get(server_id)
        if entry is None:
            raise MCPServerUnavailableError(detail=f"未配置的 MCP Server: {server_id}")
        return entry


def build_mcp_manager(settings: Any) -> MCPClientManager | None:
    """按应用配置构建 Manager；MCP 关闭或无 Server 配置时返回 None。"""
    from app.integrations.mcp.protocol import load_mcp_server_configs

    configs = load_mcp_server_configs(settings)
    if not configs:
        return None
    return MCPClientManager(configs, app_env=settings.app_env)
