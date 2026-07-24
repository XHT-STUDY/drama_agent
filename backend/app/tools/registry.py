"""ToolRegistry — 工具注册与发现。"""

from __future__ import annotations

from app.core.errors import AppError
from app.tools.protocol import Tool


class ToolRegistry:
    """Tool 注册表。

    注册、查询、列表工具。重名注册抛出 TOOL_ALREADY_REGISTERED。
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册工具。重名时抛出 409 错误。"""
        if tool.metadata.name in self._tools:
            raise AppError(
                detail=f"工具已注册: {tool.metadata.name}",
                status_code=409,
                code="TOOL_ALREADY_REGISTERED",
            )
        self._tools[tool.metadata.name] = tool

    def get(self, name: str) -> Tool:
        """按名称获取工具。未找到抛出 TOOL_NOT_FOUND。"""
        if name not in self._tools:
            raise AppError(
                detail=f"工具未注册: {name}",
                status_code=404,
                code="TOOL_NOT_FOUND",
            )
        return self._tools[name]

    def list_all(self) -> list[Tool]:
        """列出所有已注册工具。"""
        return list(self._tools.values())
