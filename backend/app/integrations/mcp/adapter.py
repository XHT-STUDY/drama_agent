"""MCPToolAdapter — 把外部 MCP 工具映射为内部 Tool 协议（I-04）。

通过 HTTP JSON-RPC 调用外部工具：
- 注册名 = `prefix + 外部工具名`（默认前缀 `mcp_`），重名由 ToolRegistry 抛 409；
- 429 / 5xx 按 I-01 退避策略重试（尊重 Retry-After）；
- 超时 → EXTERNAL_TOOL_TIMEOUT（504）；外部错误 / 响应异常 → 泛化
  EXTERNAL_TOOL_ERROR（502），detail 不泄漏内部连接信息；
- 无 MCP 配置（默认关）时本类不被实例化，主流程不受影响。

复用 I-01 的 RetryPolicy 与 parse_retry_after（退避机制一致）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import httpx

from app.core.errors import ExternalToolError, ExternalToolTimeoutError
from app.integrations.mcp.protocol import MCPAdapterConfig, MCPToolSpec
from app.llm.retry import RetryPolicy, parse_retry_after
from app.tools.protocol import Tool, ToolMetadata
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class MCPToolAdapter(Tool):
    """一个外部 MCP 工具 → 一个内部 Tool。"""

    def __init__(
        self,
        spec: MCPToolSpec,
        config: MCPAdapterConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
        request_id_fn: Callable[[], int] | None = None,
    ) -> None:
        """初始化适配器。

        Args:
            spec: 外部工具描述
            config: 连接配置（base_url / timeout / prefix）
            transport: 可选 httpx transport（测试注入 MockTransport，生产为 None）
            retry_policy: 退避策略；默认 RetryPolicy()（I-01）
            request_id_fn: JSON-RPC id 生成器；默认自增计数器
        """
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
        """调用外部工具（JSON-RPC over HTTP）。

        Args:
            **kwargs: 工具入参（对应 JSON-RPC params）

        Returns:
            外部工具返回的 result（原样透传，不本地解析）

        Raises:
            ExternalToolTimeoutError: 超时（504 EXTERNAL_TOOL_TIMEOUT）
            ExternalToolError: 连接失败 / HTTP 错误 / JSON-RPC error /
                响应无法解析（502 EXTERNAL_TOOL_ERROR，泛化不泄漏内部信息）
        """
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
    """把一组外部工具注册到内部 ToolRegistry。

    注册名 = `config.prefix + 外部工具名`；重名注册会抛
    TOOL_ALREADY_REGISTERED（409），由调用方决定是否捕获。

    Args:
        registry: 内部 ToolRegistry 实例
        specs: 外部工具描述列表
        config: MCP 连接配置（enabled=False 时不注册）
        transport: 可选测试注入 transport
        retry_policy: 可选退避策略

    Returns:
        实际注册的内部工具名列表（enabled=False 时返回空列表）
    """
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
