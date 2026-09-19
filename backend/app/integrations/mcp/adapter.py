"""MCP Tool → 内部 Tool 协议适配（MCP-02）。

- ``MCPRemoteTool``（ExternalTool）：一个 catalog 中的 MCP 工具定义对应
  一个内部 Tool。内部注册名 = ``mcp__{server_id}__{tool_name}``；
  execute 只委托 MCPExecutionService（Schema 校验、digest 校验、结果
  规范化与大小限制），不自行管理网络连接。
- ``register_mcp_remote_tools``：把 catalog 中全部 available 工具注册到
  ToolRegistry（allowlist 已在 catalog 构建时过滤）。

弃用（I-04 手写 JSON-RPC Adapter，保留一个发布周期）：
- ``MCPToolAdapter`` / ``register_mcp_tools``：非标准请求格式，无协议
  协商，协议底层已由官方 SDK 取代；确认无调用方后删除。
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from collections.abc import Callable
from typing import Any

import httpx

from app.core.errors import ExternalToolError, ExternalToolTimeoutError
from app.integrations.mcp.catalog import MCPToolDefinition
from app.integrations.mcp.execution import MCPExecutionService, MCPToolCallCommand
from app.integrations.mcp.protocol import MCPAdapterConfig, MCPToolSpec
from app.llm.retry import RetryPolicy, parse_retry_after
from app.tools.protocol import (
    ExternalTool,
    ExternalToolHints,
    Tool,
    ToolMetadata,
)
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class MCPRemoteTool(ExternalTool):
    """catalog 中的 MCP 工具 → 内部 ExternalTool。"""

    def __init__(
        self,
        definition: MCPToolDefinition,
        execution: MCPExecutionService,
    ) -> None:
        self.definition = definition
        self._execution = execution
        hints = definition.annotations
        self.metadata = ToolMetadata(
            name=definition.qualified_tool_name,
            version="1.0",
            description=definition.description or f"MCP 外部工具：{definition.tool_name}",
            input_schema=definition.input_schema,
            output_schema=definition.output_schema or {},
            execution_kind="external",
            source_id=definition.server_id,
            requires_confirmation=True,
            network_access=True,
            external_hints=(
                ExternalToolHints(
                    title=hints.title,
                    read_only_hint=hints.read_only_hint,
                    destructive_hint=hints.destructive_hint,
                    idempotent_hint=hints.idempotent_hint,
                    open_world_hint=hints.open_world_hint,
                )
                if hints is not None
                else None
            ),
        )

    async def execute(self, **kwargs: Any) -> Any:
        """经 MCPExecutionService 执行（前置校验 + 结果规范化）。"""
        command = MCPToolCallCommand(
            server_id=self.definition.server_id,
            tool_name=self.definition.tool_name,
            arguments=dict(kwargs),
            expected_definition_digest=self.definition.definition_digest,
        )
        return await self._execution.execute(command)


def register_mcp_remote_tools(
    registry: ToolRegistry,
    manager: Any,
    *,
    execution: MCPExecutionService | None = None,
) -> list[str]:
    """把 catalog 中全部 available 的 MCP 工具注册到 ToolRegistry。

    Args:
        registry: 内部 ToolRegistry 实例。
        manager: MCPClientManager（提供 catalog）。
        execution: 可选共享执行服务；缺省按 manager 构建。

    Returns:
        实际注册的内部工具名列表（无可用工具时为空）。
    """
    service = execution or MCPExecutionService(manager)
    registered: list[str] = []
    for definition in manager.catalog.list_available():
        tool = MCPRemoteTool(definition, service)
        registry.register(tool)
        registered.append(tool.metadata.name)
    if registered:
        logger.info("MCP 外部工具注册完成: count=%d", len(registered))
    return registered


# ======================================================================
# 弃用：I-04 手写 JSON-RPC Adapter（保留一个发布周期）
# ======================================================================


class MCPToolAdapter(Tool):
    """一个外部 MCP 工具 → 一个内部 Tool（弃用：手写 JSON-RPC）。"""

    def __init__(
        self,
        spec: MCPToolSpec,
        config: MCPAdapterConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
        request_id_fn: Callable[[], int] | None = None,
    ) -> None:
        """初始化适配器（弃用）。"""
        warnings.warn(
            "MCPToolAdapter 为弃用实现（手写 JSON-RPC，非标准 tools/call）；"
            "请迁移到官方 SDK 的 MCPRemoteTool，将在下个发布周期移除",
            DeprecationWarning,
            stacklevel=2,
        )
        self.spec = spec
        self.config = config
        self._transport = transport
        self._retry_policy = retry_policy or RetryPolicy()
        self._request_id = 0
        self._request_id_fn = request_id_fn or self._next_request_id

        metadata_name = f"{config.prefix}{spec.name}"
        description = (
            f"MCP 外部工具：{spec.description}"
            if spec.description
            else f"MCP 外部工具：{spec.name}"
        )
        self.metadata = ToolMetadata(
            name=metadata_name,
            version="1.0",
            description=description,
            input_schema=spec.input_schema,
            output_schema=spec.output_schema,
        )

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def execute(self, **kwargs: Any) -> Any:
        """调用外部工具（JSON-RPC over HTTP，弃用路径）。"""
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": self.spec.name,
            "params": kwargs,
            "id": self._request_id_fn(),
        }
        headers = {"Content-Type": "application/json"}
        policy = self._retry_policy

        last_resp: httpx.Response | None = None
        for attempt in range(1, policy.max_attempts + 1):
            try:
                async with httpx.AsyncClient(
                    base_url=self.config.base_url,
                    timeout=self.config.timeout_seconds,
                    transport=self._transport,
                ) as client:
                    resp = await client.post("/", json=payload, headers=headers)
            except httpx.TimeoutException:
                # 超时直接失败（不做重试），只说明超时，不泄漏服务信息
                raise ExternalToolTimeoutError(
                    f"外部工具 {self.spec.name} 调用超时（>{self.config.timeout_seconds}s）"
                ) from None
            except httpx.HTTPError:
                # 连接失败 / 传输错误：泛化，不泄漏地址与内部异常
                raise ExternalToolError(
                    f"外部工具 {self.spec.name} 调用失败"
                ) from None

            last_resp = resp
            # 429 / 5xx 可重试（复用 I-01 退避 + Retry-After 尊重）
            is_retryable_status = resp.status_code in (429,) or resp.status_code >= 500
            if is_retryable_status and attempt < policy.max_attempts:
                    delay = policy.compute_delay(
                        attempt, parse_retry_after(resp.headers.get("retry-after"))
                    )
                    logger.warning(
                        "MCP 外部工具 %s 返回 %s，第 %d 次尝试，%.2fs 后重试",
                        self.spec.name,
                        resp.status_code,
                        attempt + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
            break

        if last_resp is None:
            raise ExternalToolError(f"外部工具 {self.spec.name} 调用失败")

        if last_resp.status_code >= 400:
            raise ExternalToolError(f"外部工具 {self.spec.name} 返回错误")

        try:
            body: dict[str, Any] = last_resp.json()
        except ValueError:
            raise ExternalToolError(
                f"外部工具 {self.spec.name} 响应无法解析"
            ) from None

        if not isinstance(body, dict) or "error" in body:
            raise ExternalToolError(f"外部工具 {self.spec.name} 执行失败")

        return body.get("result")


def register_mcp_tools(
    registry: ToolRegistry,
    specs: list[MCPToolSpec],
    config: MCPAdapterConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    retry_policy: RetryPolicy | None = None,
) -> list[str]:
    """把一组外部工具注册到内部 ToolRegistry（弃用路径）。"""
    if not config.enabled:
        return []
    registered: list[str] = []
    for spec in specs:
        adapter = MCPToolAdapter(
            spec, config, transport=transport, retry_policy=retry_policy
        )
        registry.register(adapter)
        registered.append(adapter.metadata.name)
    return registered
