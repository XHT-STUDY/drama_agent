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

    def test_metadata_defaults_keep_local_semantics(self) -> None:
        """MCP-02：默认元数据保持内部工具语义（local / 无需确认 / 无网络）。"""
        meta = ToolMetadata(name="echo").model_dump()
        assert meta["execution_kind"] == "local"
        assert meta["requires_confirmation"] is False
        assert meta["network_access"] is False
        assert meta["source_id"] is None
        assert meta["external_hints"] is None

    def test_remove_registered_tool(self) -> None:
        """remove 移除工具；不存在时幂等（MCP-02 catalog 刷新用）。"""
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.remove("echo")
        registry.remove("echo")  # 幂等
        with pytest.raises(AppError):
            registry.get("echo")

    def test_list_by_execution_kind(self) -> None:
        """按 local / external 分区列出（MCP-02）。"""
        from app.tools.protocol import ExternalTool, LocalDeterministicTool

        class LocalEcho(LocalDeterministicTool):
            metadata = ToolMetadata(name="local_echo")

            async def execute(self, **kwargs):
                return kwargs

        class RemoteThing(ExternalTool):
            metadata = ToolMetadata(
                name="mcp__s__thing",
                execution_kind="external",
                source_id="s",
                requires_confirmation=True,
                network_access=True,
            )

            async def execute(self, **kwargs):
                return kwargs

        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(LocalEcho())
        registry.register(RemoteThing())
        assert {t.metadata.name for t in registry.list_by_execution_kind("local")} == {
            "echo",
            "local_echo",
        }
        assert [t.metadata.name for t in registry.list_by_execution_kind("external")] == [
            "mcp__s__thing"
        ]
