"""Tool 协议 — 确定性本地工具与受控外部工具抽象。

Tool 按执行位置分两类（MCP-02）：
- ``LocalDeterministicTool``：纯 Python 实现，不调用 LLM、不访问网络；
- ``ExternalTool``：execute 委托外部服务（如 MCP Server），必须经过
  确认链路（AgentAction → WorkflowRun），结果按不可信内容处理。

每个 Tool 必须声明 input_schema 和 output_schema。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field

ExecutionKind = Literal["local", "external"]


class ExternalToolHints(BaseModel):
    """外部工具的风险提示（来源声明不可信，仅展示与策略输入）。"""

    model_config = {"extra": "forbid"}

    title: str | None = None
    read_only_hint: bool | None = None
    destructive_hint: bool | None = None
    idempotent_hint: bool | None = None
    open_world_hint: bool | None = None


class ToolMetadata(BaseModel):
    """Tool 元数据——可序列化供扩展协议使用。"""

    model_config = {"extra": "forbid"}

    name: str = Field(..., description="工具唯一名称，如 'compute_word_count'")
    version: str = Field(default="1.0", description="工具版本号")
    description: str = Field(default="", description="工具功能描述")
    input_schema: dict[str, Any] = Field(default_factory=dict, description="输入 JSON Schema")
    output_schema: dict[str, Any] = Field(default_factory=dict, description="输出 JSON Schema")
    # ---- MCP-02：执行位置与外部工具元数据（内部工具保持默认值不变） ----
    execution_kind: ExecutionKind = Field(
        default="local", description="执行位置：local=进程内确定性实现，external=外部服务"
    )
    source_id: str | None = Field(
        default=None, description="外部工具来源标识（MCP server_id）"
    )
    requires_confirmation: bool = Field(
        default=False, description="执行前是否需要用户确认（外部工具恒为 true）"
    )
    network_access: bool = Field(
        default=False, description="执行是否产生网络访问"
    )
    external_hints: ExternalToolHints | None = Field(
        default=None, description="外部工具 annotation 风险提示（不可信展示信息）"
    )


class Tool(ABC):
    """工具抽象基类。"""

    metadata: ToolMetadata

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """执行工具逻辑。"""
        ...


class LocalDeterministicTool(Tool):
    """进程内确定性工具（不调用 LLM、不访问网络）。

    现有内部工具的语义类别；作为标记基类保留，便于注册表与审计区分。
    """

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:  # pragma: no cover - 接口声明
        """执行确定性本地逻辑。"""
        ...


class ExternalTool(Tool):
    """外部工具（execute 委托外部服务，网络访问，结果不可信）。

    外部工具不得被 Planner/LLM 直接调用——必须经确认链路
    （AgentAction → WorkflowRun → MCPExecutionService）。
    """

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:  # pragma: no cover - 接口声明
        """执行外部调用（由具体协议适配器实现）。"""
        ...
