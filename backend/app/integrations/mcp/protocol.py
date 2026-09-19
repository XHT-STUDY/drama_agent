"""MCP 协议模型——Server 配置、连接状态与外部工具描述。

纯数据模型与配置解析，不依赖 DB / 网络（DNS 解析类校验见 security.py）。

MCP-01 起系统使用官方 mcp SDK（Tools-only Client）：
- ``MCPServerConfig``：管理员配置的单个远程 MCP Server；
- ``load_mcp_server_configs``：解析 ``MCP_SERVERS_JSON``，缺失时回退到
  旧 ``MCP_BASE_URL`` 单 Server 配置（兼容一个发布周期，记录弃用警告）；
- ``MCPServerState``：运行期连接状态（协议版本、capabilities、错误分类）。

旧 ``MCPToolSpec`` / ``MCPAdapterConfig`` 保留为弃用实现（I-04 手写
JSON-RPC Adapter 使用），确认无调用方后删除。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# Server ID：小写字母、数字、下划线（进程内唯一）
SERVER_ID_RE = re.compile(r"^[a-z0-9_]+$")

# 兼容期警告只记录一次（load_mcp_server_configs 可能被多次调用）
_legacy_warned = False


class MCPServerConfig(BaseModel):
    """单个远程 MCP Server 的管理员配置（MCP_SERVERS_JSON 条目）。

    安全语义：
    - ``url`` 生产默认只接受 HTTPS（见 security.validate_server_url）；
    - ``bearer_token_env`` 只保存环境变量名，Token 值在连接时读取，
      不进入配置快照、日志或错误响应；
    - ``allowed_tools`` 是硬白名单：空列表 = 发现可见但全部不可调用；
    - ``allow_private_network`` 显式允许 loopback / 私网目标（本地开发）。
    """

    model_config = {"extra": "forbid"}

    id: str = Field(..., description="Server ID，进程内唯一，[a-z0-9_]+")
    url: str = Field(..., min_length=1, description="Streamable HTTP 端点")
    enabled: bool = Field(default=True, description="是否加载该 Server")
    allowed_tools: list[str] = Field(
        default_factory=list,
        description="允许调用的原始工具名白名单；空列表表示不允许调用",
    )
    bearer_token_env: str | None = Field(
        default=None,
        description="Bearer Token 所在环境变量名（不保存 Token 值）",
    )
    timeout_seconds: float = Field(
        default=30.0, gt=0, le=600, description="单次调用总超时（秒）"
    )
    max_concurrency: int = Field(
        default=4, ge=1, le=64, description="该 Server 的并发调用上限"
    )
    allow_private_network: bool = Field(
        default=False, description="是否允许 loopback / 私网目标（默认拒绝）"
    )

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not SERVER_ID_RE.match(value):
            raise ValueError(
                f"Server ID 只允许小写字母、数字和下划线: {value!r}"
            )
        return value

    @field_validator("bearer_token_env")
    @classmethod
    def _validate_bearer_env(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("bearer_token_env 不能为空白字符串")
        return value

    def safe_summary(self) -> dict[str, Any]:
        """不含 Token 与 URL 凭证信息的可记录摘要。"""
        return {
            "id": self.id,
            "url_origin": _url_origin(self.url),
            "enabled": self.enabled,
            "allowed_tools_count": len(self.allowed_tools),
            "bearer_token_env": self.bearer_token_env,
            "timeout_seconds": self.timeout_seconds,
            "max_concurrency": self.max_concurrency,
            "allow_private_network": self.allow_private_network,
        }


def _url_origin(url: str) -> str:
    """提取 URL 的 scheme://host[:port]（剥离 userinfo 与路径，不泄漏凭证）。"""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.hostname:
            port = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme}://{parsed.hostname}{port}"
    except ValueError:
        pass
    return "<invalid-url>"


# 连接状态（低基数，供日志与指标 label 使用）
MCPServerStatus = str  # "available" | "degraded" | "disabled"


class MCPServerState(BaseModel):
    """单个 Server 的运行期连接状态快照。"""

    model_config = {"extra": "forbid"}

    server_id: str
    status: MCPServerStatus = "degraded"
    # 协商结果（连接成功后填）
    protocol_version: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    capabilities: dict[str, Any] = Field(
        default_factory=dict, description="capabilities 的扁平摘要（tools 等）"
    )
    # 最近一次失败分类（MCP_* 错误码）与可读摘要（不含敏感信息）
    error_code: str | None = None
    error_detail: str | None = None
    tool_count: int = 0


def parse_mcp_servers_json(raw: str) -> list[MCPServerConfig]:
    """解析 MCP_SERVERS_JSON 为 Server 配置列表。

    Raises:
        ValueError: JSON 非法、不是数组、条目非法或 ID 重复。
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"MCP_SERVERS_JSON 不是合法 JSON: {exc.msg}") from exc
    if not isinstance(data, list):
        raise ValueError("MCP_SERVERS_JSON 必须是 Server 配置数组")
    configs = [MCPServerConfig.model_validate(item) for item in data]
    seen: set[str] = set()
    for config in configs:
        if config.id in seen:
            raise ValueError(f"MCP Server ID 重复: {config.id}")
        seen.add(config.id)
    return configs


def load_mcp_server_configs(settings: Any) -> list[MCPServerConfig]:
    """按配置加载 MCP Server 列表。

    - ``mcp_enabled=false``：返回空列表（不创建 ClientManager）；
    - ``MCP_SERVERS_JSON`` 缺失且旧 ``MCP_BASE_URL`` 非空：映射为
      ``id=default`` 的单 Server 兼容配置并记录一次弃用警告；
    - 两者都缺：返回空列表（MCP 完全不加载，主流程不受影响）。

    只做结构校验；URL 安全校验（HTTPS/私网）在 ClientManager 启动时
    逐 Server 执行，非法 Server 标记 degraded，不中断应用启动。
    """
    if not settings.mcp_enabled:
        return []
    raw = (settings.mcp_servers_json or "").strip()
    if raw:
        return parse_mcp_servers_json(raw)
    legacy_url = (settings.mcp_base_url or "").strip()
    if legacy_url:
        global _legacy_warned
        if not _legacy_warned:
            logger.warning(
                "MCP_BASE_URL/MCP_TIMEOUT_SECONDS 已弃用，将在下个发布周期移除；"
                "请迁移到 MCP_SERVERS_JSON（当前映射为 id=default 的单 Server）"
            )
            _legacy_warned = True
        return [
            MCPServerConfig(
                id="default",
                url=legacy_url,
                enabled=True,
                allowed_tools=[],
                timeout_seconds=float(settings.mcp_timeout_seconds),
            )
        ]
    return []


# ======================================================================
# 旧 I-04 手写 Adapter 模型（弃用，兼容一个发布周期）
# ======================================================================


class MCPToolSpec(BaseModel):
    """外部 MCP 工具描述（I-04 旧模型，弃用）。

    对应一次注册到内部 ToolRegistry 的声明：
    - name 为外部服务的工具名（在内部注册时会加前缀，见 MCPAdapterConfig.prefix）
    - input_schema / output_schema 为 JSON Schema（可空，容忍未声明）
    """

    model_config = {"extra": "forbid"}

    name: str = Field(..., description="外部工具名，如 'web_search'", min_length=1)
    description: str = Field(default="", description="外部工具功能描述")
    input_schema: dict[str, Any] = Field(default_factory=dict, description="输入 JSON Schema")
    output_schema: dict[str, Any] = Field(default_factory=dict, description="输出 JSON Schema")


class MCPAdapterConfig(BaseModel):
    """MCP 适配器连接配置（I-04 旧模型，弃用）。

    enabled=False（默认）时适配器惰性可用但不主动连接，
    主流程（无 MCP 配置）完全不受影响。
    """

    model_config = {"extra": "forbid"}

    enabled: bool = Field(default=False, description="是否启用 MCP 外部工具")
    base_url: str = Field(default="", description="外部工具服务地址（JSON-RPC over HTTP）")
    timeout_seconds: float = Field(default=10.0, description="单次外部调用超时（秒）")
    prefix: str = Field(default="mcp_", description="内部注册名前缀，避免与内部工具重名")
