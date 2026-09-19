"""MCPToolCatalog 单元测试（MCP-02）——digest、原子替换、allowlist 过滤。"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.errors import MCPConfigInvalidError
from app.integrations.mcp.catalog import (
    MCPToolCatalog,
    MCPToolDefinition,
    MCPToolHints,
    build_server_definitions,
    compute_definition_digest,
    qualified_tool_name,
    tool_definition_from_sdk,
)


def _sdk_tool(name: str, **kwargs: Any) -> Any:
    """构造官方 SDK Tool 的最小替身。"""

    class _Tool:
        pass

    tool = _Tool()
    tool.name = name  # type: ignore[attr-defined]
    tool.description = kwargs.get("description", f"desc of {name}")  # type: ignore[attr-defined]
    tool.input_schema = kwargs.get("input_schema", {"type": "object"})  # type: ignore[attr-defined]
    tool.output_schema = kwargs.get("output_schema")  # type: ignore[attr-defined]
    tool.annotations = kwargs.get("annotations")  # type: ignore[attr-defined]
    return tool


class TestQualifiedToolName:
    def test_format(self) -> None:
        assert qualified_tool_name("research", "web_search") == "mcp__research__web_search"

    @pytest.mark.parametrize("bad", ["", "has__double", "with space", "a\nb"])
    def test_illegal_tool_name_rejected(self, bad: str) -> None:
        with pytest.raises(MCPConfigInvalidError):
            qualified_tool_name("research", bad)

    def test_illegal_server_id_rejected(self) -> None:
        with pytest.raises(MCPConfigInvalidError):
            qualified_tool_name("Bad-ID", "tool")


class TestDefinitionDigest:
    def test_digest_covers_name_schema_annotations(self) -> None:
        base: dict[str, Any] = {
            "tool_name": "t",
            "input_schema": {"type": "object"},
            "output_schema": None,
            "annotations": None,
        }
        d1 = compute_definition_digest(**base)
        assert len(d1) == 64
        # 任一维度变化 → digest 变化
        assert compute_definition_digest(**{**base, "tool_name": "t2"}) != d1
        assert (
            compute_definition_digest(**{**base, "input_schema": {"type": "array"}}) != d1
        )
        assert (
            compute_definition_digest(**{**base, "output_schema": {"type": "object"}}) != d1
        )
        assert (
            compute_definition_digest(
                **{**base, "annotations": MCPToolHints(read_only_hint=True)}
            )
            != d1
        )

    def test_digest_stable_regardless_of_key_order(self) -> None:
        a = compute_definition_digest(
            tool_name="t",
            input_schema={"type": "object", "properties": {"x": {"type": "int"}}},
            output_schema=None,
            annotations=None,
        )
        b = compute_definition_digest(
            tool_name="t",
            input_schema={"properties": {"x": {"type": "int"}}, "type": "object"},
            output_schema=None,
            annotations=None,
        )
        assert a == b


class TestToolDefinitionFromSdk:
    def test_maps_fields_and_annotations(self) -> None:
        annotations = MCPToolHints(
            title="Search", read_only_hint=True, destructive_hint=False
        )
        definition = tool_definition_from_sdk(
            "research",
            _sdk_tool(
                "search",
                input_schema={"type": "object", "required": ["q"]},
                output_schema={"type": "object"},
                annotations=annotations,
            ),
        )
        assert definition.server_id == "research"
        assert definition.tool_name == "search"
        assert definition.qualified_tool_name == "mcp__research__search"
        assert definition.input_schema.get("required") == ["q"]
        assert definition.output_schema == {"type": "object"}
        assert definition.annotations is not None
        assert definition.annotations.read_only_hint is True
        assert definition.definition_digest


class TestBuildServerDefinitions:
    def test_allowlist_filters(self) -> None:
        tools = [_sdk_tool("a"), _sdk_tool("b"), _sdk_tool("c")]
        definitions = build_server_definitions("s", tools, ["a", "c"])
        assert [d.tool_name for d in definitions] == ["a", "c"]

    def test_empty_allowlist_blocks_all(self) -> None:
        """空 allowed_tools = 全部不可调用（不进入 catalog）。"""
        definitions = build_server_definitions("s", [_sdk_tool("a")], [])
        assert definitions == []

    def test_illegal_tool_name_skipped_not_fatal(self) -> None:
        tools = [_sdk_tool("bad__name"), _sdk_tool("good")]
        definitions = build_server_definitions("s", tools, ["bad__name", "good"])
        assert [d.tool_name for d in definitions] == ["good"]


class TestMCPToolCatalog:
    def _definition(self, server_id: str, tool_name: str) -> MCPToolDefinition:
        return tool_definition_from_sdk(server_id, _sdk_tool(tool_name))

    def test_replace_server_tools_atomic(self) -> None:
        catalog = MCPToolCatalog()
        catalog.replace_server_tools("s", [self._definition("s", "a"), self._definition("s", "b")])
        assert len(catalog) == 2
        # 替换后旧工具消失（整 Server 原子替换）
        catalog.replace_server_tools("s", [self._definition("s", "b"), self._definition("s", "c")])
        assert catalog.get("mcp__s__a") is None
        assert catalog.get("mcp__s__b") is not None
        assert catalog.get("mcp__s__c") is not None

    def test_same_tool_name_two_servers_no_conflict(self) -> None:
        catalog = MCPToolCatalog()
        catalog.replace_server_tools("s1", [self._definition("s1", "search")])
        catalog.replace_server_tools("s2", [self._definition("s2", "search")])
        assert catalog.get("mcp__s1__search") is not None
        assert catalog.get("mcp__s2__search") is not None
        assert len(catalog.list_available()) == 2

    def test_mark_server_stale_only_affects_that_server(self) -> None:
        catalog = MCPToolCatalog()
        catalog.replace_server_tools("s1", [self._definition("s1", "a")])
        catalog.replace_server_tools("s2", [self._definition("s2", "a")])
        catalog.mark_server_stale("s1")
        assert catalog.get("mcp__s1__a").status == "stale"  # type: ignore[union-attr]
        assert catalog.get("mcp__s2__a").status == "available"  # type: ignore[union-attr]
        assert catalog.list_available() == [catalog.get("mcp__s2__a")]

    def test_remove_server(self) -> None:
        catalog = MCPToolCatalog()
        catalog.replace_server_tools("s", [self._definition("s", "a")])
        catalog.remove_server("s")
        assert catalog.get("mcp__s__a") is None
        assert len(catalog) == 0

    def test_stale_not_re_marked_after_removal(self) -> None:
        catalog = MCPToolCatalog()
        catalog.replace_server_tools("s", [self._definition("s", "a")])
        catalog.mark_server_stale("s")
        catalog.mark_server_stale("s")  # 幂等
        assert catalog.get("mcp__s__a").status == "stale"  # type: ignore[union-attr]
