"""Tool 协议 — 确定性工具抽象。

Tool 是纯函数式能力，不可隐式调用 LLM。
每个 Tool 必须声明 input_schema 和 output_schema（供 MCP Adapter 使用）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class ToolMetadata(BaseModel):
    """Tool 元数据——可序列化供 MCP Adapter 使用。"""

    model_config = {"extra": "forbid"}

    name: str = Field(..., description="工具唯一名称，如 'compute_word_count'")
    version: str = Field(default="1.0", description="工具版本号")
    description: str = Field(default="", description="工具功能描述")
    input_schema: dict[str, Any] = Field(default_factory=dict, description="输入 JSON Schema")
    output_schema: dict[str, Any] = Field(default_factory=dict, description="输出 JSON Schema")


class Tool(ABC):
    """确定性工具抽象基类。

    execute 必须是纯 Python 实现，不调用 LLM、不访问网络。
    """

    metadata: ToolMetadata

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """执行工具逻辑。"""
        ...
