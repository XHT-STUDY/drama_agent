"""MCP catalog 处理性能测试（MCP-04，§6 要求 6）。

验证：1000 个工具定义下完成解析（SDK Tool → 内部定义）、allowlist
过滤与 digest 计算，p95 < 300 ms（不含网络时间）；有界 Planner 目录
构建（匹配 + 裁剪到 20 个）在同类规模下 p95 < 300 ms。

纯计算路径，不依赖 DB / Redis / LLM / 网络；经 `-m performance` 显式运行。
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.integrations.mcp.catalog import (
    MCPToolCatalog,
    build_server_definitions,
    tool_definition_from_sdk,
)

TOOL_COUNT = 1000
ROUNDS = 20
P95_LIMIT_MS = 300.0


def _sdk_tools(count: int) -> list[Any]:
    """构造 count 个官方 SDK Tool 替身（含 Schema 与 annotations）。"""

    class _Annotations:
        def __init__(self, index: int) -> None:
            self.title = f"Tool {index}"
            self.read_only_hint = index % 2 == 0
            self.destructive_hint = False
            self.idempotent_hint = index % 3 == 0
            self.open_world_hint = True

    class _Tool:
        pass

    tools: list[Any] = []
    for i in range(count):
        tool = _Tool()
        tool.name = f"tool_{i:04d}"
        tool.description = (
            f"检索 足球 青训 资料 的第 {i} 号工具，支持关键词与范围过滤"
        )
        tool.input_schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "关键词"},
                "limit": {"type": "integer", "description": "数量上限"},
            },
            "required": ["query"],
        }
        tool.output_schema = {"type": "object", "properties": {"result": {"type": "string"}}}
        tool.annotations = _Annotations(i)
        tools.append(tool)
    return tools


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return ordered[index] * 1000.0  # → ms


@pytest.mark.performance
class TestMCPCatalogPerformance:
    def test_1000_definitions_parse_filter_digest_p95(self) -> None:
        """1000 工具：SDK→内部定义 + 过滤 + digest，p95 < 300ms。"""
        tools = _sdk_tools(TOOL_COUNT)
        allowed = [f"tool_{i:04d}" for i in range(TOOL_COUNT)]
        samples: list[float] = []
        catalog = MCPToolCatalog()
        for _ in range(ROUNDS):
            started = time.monotonic()
            definitions = build_server_definitions("research", tools, allowed)
            catalog.replace_server_tools("research", definitions)
            samples.append(time.monotonic() - started)
        assert len(catalog.list_for_server("research")) == TOOL_COUNT
        p95 = _p95(samples)
        assert p95 < P95_LIMIT_MS, f"catalog 处理 p95={p95:.1f}ms 超过 {P95_LIMIT_MS}ms"

    def test_bounded_planner_catalog_p95(self) -> None:
        """1000 工具目录 → 有界 Planner 目录（≤20），p95 < 300ms。"""
        from app.application.agent_command_service import build_planner_external_tools

        tools = _sdk_tools(TOOL_COUNT)
        definitions = [tool_definition_from_sdk("research", t) for t in tools]
        samples: list[float] = []
        for round_index in range(ROUNDS):
            request = f"帮我检索 足球 青训 资料 第{round_index}号"
            started = time.monotonic()
            bounded = build_planner_external_tools(definitions, request)
            samples.append(time.monotonic() - started)
            assert len(bounded) <= 20
        p95 = _p95(samples)
        assert p95 < P95_LIMIT_MS, f"有界目录构建 p95={p95:.1f}ms 超过 {P95_LIMIT_MS}ms"
