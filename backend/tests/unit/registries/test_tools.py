"""B-07 Tool/ToolRegistry 单元测试。"""

from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.tools.protocol import Tool, ToolMetadata
from app.tools.registry import ToolRegistry


class EchoTool(Tool):
    """测试用 — 原样返回输入。"""
    metadata = ToolMetadata(name="echo", version="1.0", description="Echo tool")

    async def execute(self, **kwargs):
        return kwargs


class TestToolRegistry:
    """ToolRegistry 功能测试。"""

    def test_register_and_get(self) -> None:
        """注册后可按名获取。"""
        registry = ToolRegistry()
        tool = EchoTool()
        registry.register(tool)
        assert registry.get("echo") is tool

    def test_get_nonexistent_raises(self) -> None:
        """查询未注册名抛出错误。"""
        registry = ToolRegistry()
        with pytest.raises(AppError) as exc:
            registry.get("nonexistent")
        assert exc.value.code == "TOOL_NOT_FOUND"

    def test_duplicate_register_raises(self) -> None:
        """重复注册抛出错误。"""
        registry = ToolRegistry()
        registry.register(EchoTool())
        with pytest.raises(AppError) as exc:
            registry.register(EchoTool())
        assert exc.value.code == "TOOL_ALREADY_REGISTERED"

    def test_list_all(self) -> None:
        """list_all 返回全部已注册工具。"""
        registry = ToolRegistry()
        registry.register(EchoTool())

        class CountTool(Tool):
            metadata = ToolMetadata(name="count", version="1.0")
            async def execute(self, **kwargs):
                return len(kwargs)

        registry.register(CountTool())
        assert len(registry.list_all()) == 2

    def test_metadata_serializable(self) -> None:
        """元数据可序列化为 dict。"""
        meta = ToolMetadata(name="test", version="2.0", description="desc")
        d = meta.model_dump()
        assert d["name"] == "test"
        assert d["version"] == "2.0"
