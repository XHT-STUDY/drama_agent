"""MCP 协议模型（I-04）——外部工具描述与连接配置。

纯数据模型，不依赖 DB / 网络。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MCPToolSpec(BaseModel):
    """外部 MCP 工具描述。

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
    """MCP 适配器连接配置。

    enabled=False（默认）时适配器惰性可用但不主动连接，
    主流程（无 MCP 配置）完全不受影响。
    """

    model_config = {"extra": "forbid"}

    enabled: bool = Field(default=False, description="是否启用 MCP 外部工具")
    base_url: str = Field(default="", description="外部工具服务地址（JSON-RPC over HTTP）")
    timeout_seconds: float = Field(default=10.0, description="单次外部调用超时（秒）")
    prefix: str = Field(default="mcp_", description="内部注册名前缀，避免与内部工具重名")
