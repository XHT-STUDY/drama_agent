"""MCPExecutionService 单元测试（MCP-02）。

Fake Manager 注入（无网络）：校验顺序、Schema 校验、结果规范化、
大小截断、错误映射与 MCPRemoteTool 注册。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.errors import (
    MCPProtocolError,
    MCPToolArgumentInvalidError,
    MCPToolError,
    MCPToolNotAvailableError,
    MCPToolResultInvalidError,
    MCPToolStaleError,
)
from app.integrations.mcp.adapter import MCPRemoteTool, register_mcp_remote_tools
from app.integrations.mcp.catalog import (
    MCPToolCatalog,
)
from app.integrations.mcp.execution import (
    DEFAULT_MAX_RESULT_BYTES,
    MCPExecutionService,
    MCPToolCallCommand,
)
from app.integrations.mcp.protocol import MCPServerState
from app.tools.registry import ToolRegistry

# ======================================================================
# 替身
# ======================================================================


def _sdk_tool(name: str, **kwargs: Any) -> Any:
    class _Tool:
        pass

    tool = _Tool()
    tool.name = name
    tool.description = kwargs.get("description", f"desc of {name}")
    tool.input_schema = kwargs.get("input_schema", {"type": "object"})
    tool.output_schema = kwargs.get("output_schema")
    tool.annotations = kwargs.get("annotations")
    return tool


def _content(kind: str, **kwargs: Any) -> Any:
    class _Block:
        pass

    block = _Block()
    block.type = kind
    for key, value in kwargs.items():
        setattr(block, key, value)
    return block


def _call_result(
    content: list[Any] | None = None,
    structured: dict[str, Any] | None = None,
    is_error: bool = False,
) -> Any:
    class _Result:
        pass

    result = _Result()
    result.content = content or []
    result.structured_content = structured
    result.is_error = is_error
    return result


class _FakeManager:
    """catalog + 可编程 call_tool 的 Manager 替身。"""

    def __init__(self, tools: dict[str, list[Any]], allowed: list[str] | None = None) -> None:
        from app.integrations.mcp.catalog import build_server_definitions

        self.catalog = MCPToolCatalog()
        allowed_names = allowed if allowed is not None else [
            t.name for tools_ in tools.values() for t in tools_
        ]
        for server_id, sdk_tools in tools.items():
            self.catalog.replace_server_tools(
                server_id, build_server_definitions(server_id, sdk_tools, allowed_names)
            )
        self.call_count = 0
        self.last_arguments: dict[str, Any] | None = None
        self.result: Any = _call_result()
        self.error: Exception | None = None

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        self.call_count += 1
        self.last_arguments = arguments
        if self.error is not None:
            raise self.error
        return self.result

    def get_server_state(self, server_id: str) -> MCPServerState:
        return MCPServerState(
            server_id=server_id, status="available", protocol_version="2026-07-28"
        )


def _search_manager() -> _FakeManager:
    return _FakeManager(
        {
            "research": [
                _sdk_tool(
                    "search",
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                    output_schema={
                        "type": "object",
                        "properties": {"result": {"type": "string"}},
                        "required": ["result"],
                    },
                )
            ]
        }
    )


def _command(**overrides: Any) -> MCPToolCallCommand:
    payload: dict[str, Any] = {
        "server_id": "research",
        "tool_name": "search",
        "arguments": {"query": "drama"},
    }
    payload.update(overrides)
    return MCPToolCallCommand(**payload)


# ======================================================================
# 前置校验（失败不发远程请求）
# ======================================================================


class TestPreflightValidation:
    async def test_unknown_tool_rejected_without_remote_call(self) -> None:
        manager = _search_manager()
        service = MCPExecutionService(manager)
        with pytest.raises(MCPToolNotAvailableError) as exc_info:
            await service.execute(_command(tool_name="nope"))
        assert exc_info.value.status_code == 404
        assert manager.call_count == 0

    async def test_allowlist_blocked_tool_rejected(self) -> None:
        manager = _FakeManager(
            {"research": [_sdk_tool("secret")]}, allowed=["other"]
        )
        service = MCPExecutionService(manager)
        with pytest.raises(MCPToolNotAvailableError):
            await service.execute(_command(tool_name="secret"))
        assert manager.call_count == 0

    async def test_stale_definition_rejected(self) -> None:
        manager = _search_manager()
        manager.catalog.mark_server_stale("research")
        service = MCPExecutionService(manager)
        with pytest.raises(MCPToolNotAvailableError):
            await service.execute(_command())
        assert manager.call_count == 0

    async def test_digest_mismatch_rejected(self) -> None:
        manager = _search_manager()
        service = MCPExecutionService(manager)
        with pytest.raises(MCPToolStaleError) as exc_info:
            await service.execute(_command(expected_definition_digest="0" * 64))
        assert exc_info.value.status_code == 409
        assert manager.call_count == 0

    async def test_matching_digest_accepted(self) -> None:
        manager = _search_manager()
        digest = manager.catalog.get_by_names("research", "search").definition_digest
        manager.result = _call_result(structured={"result": "ok"})
        service = MCPExecutionService(manager)
        result = await service.execute(_command(expected_definition_digest=digest))
        assert result.is_error is False

    @pytest.mark.parametrize(
        "arguments",
        [
            {"query": 123},  # 类型错误
            {},  # 缺必填
            {"query": "ok", "extra": 1},  # additionalProperties=false
        ],
    )
    async def test_invalid_arguments_rejected_without_remote_call(
        self, arguments: dict[str, Any]
    ) -> None:
        manager = _search_manager()
        service = MCPExecutionService(manager)
        with pytest.raises(MCPToolArgumentInvalidError) as exc_info:
            await service.execute(_command(arguments=arguments))
        assert exc_info.value.status_code == 422
        assert manager.call_count == 0, "输入校验失败不得发出远程请求"


# ======================================================================
# 成功路径与结果规范化
# ======================================================================


class TestResultNormalization:
    async def test_success_result_fields(self) -> None:
        manager = _search_manager()
        manager.result = _call_result(
            content=[_content("text", text="hit: drama")],
            structured={"result": "hit: drama"},
        )
        service = MCPExecutionService(manager)
        result = await service.execute(_command())
        assert result.server_id == "research"
        assert result.tool_name == "search"
        assert result.content[0].kind == "text"
        assert result.content[0].text == "hit: drama"
        assert result.structured_content == {"result": "hit: drama"}
        assert result.is_error is False
        assert result.protocol_version == "2026-07-28"
        assert result.request_id
        assert result.truncated is False
        assert result.llm_readable_text() == "hit: drama"

    async def test_resource_links_extracted(self) -> None:
        manager = _search_manager()
        manager.result = _call_result(
            content=[
                _content("text", text="see link"),
                _content("resource_link", uri="mcp://assets/1"),
            ]
        )
        result = await MCPExecutionService(manager).execute(_command())
        assert result.resource_links == ["mcp://assets/1"]
        assert result.content[1].kind == "resource_link"
        assert result.content[1].resource_uri == "mcp://assets/1"

    async def test_binary_blocks_not_llm_readable(self) -> None:
        manager = _search_manager()
        manager.result = _call_result(
            content=[
                _content("text", text="answer"),
                _content("image", data="aGk=", mime_type="image/png"),
                _content("audio", data="aGk=", mime_type="audio/wav"),
            ]
        )
        result = await MCPExecutionService(manager).execute(_command())
        assert result.llm_readable_text() == "answer"
        image = result.content[1]
        assert image.kind == "image"
        assert image.mime_type == "image/png"
        assert image.size_bytes == 3 and image.text is None

    async def test_output_schema_violation_rejected(self) -> None:
        manager = _search_manager()
        manager.result = _call_result(structured={"unexpected": 1})
        with pytest.raises(MCPToolResultInvalidError) as exc_info:
            await MCPExecutionService(manager).execute(_command())
        assert exc_info.value.status_code == 502

    async def test_output_schema_valid_passes(self) -> None:
        manager = _search_manager()
        manager.result = _call_result(structured={"result": "ok"})
        result = await MCPExecutionService(manager).execute(_command())
        assert result.structured_content == {"result": "ok"}

    async def test_output_schema_absent_skips_validation(self) -> None:
        manager = _FakeManager(
            {"research": [_sdk_tool("free", input_schema={"type": "object"})]}
        )
        manager.result = _call_result(structured={"anything": [1, 2]})
        result = await MCPExecutionService(manager).execute(
            MCPToolCallCommand(server_id="research", tool_name="free", arguments={})
        )
        assert result.structured_content == {"anything": [1, 2]}


# ======================================================================
# 错误与大小限制
# ======================================================================


class TestErrorsAndLimits:
    async def test_tool_error_raises_with_sanitized_summary(self) -> None:
        manager = _search_manager()
        manager.result = _call_result(
            content=[_content("text", text="外部错误：upstream boom at 10.0.0.1")],
            is_error=True,
        )
        with pytest.raises(MCPToolError) as exc_info:
            await MCPExecutionService(manager).execute(_command())
        assert exc_info.value.status_code == 502
        assert "外部错误" in exc_info.value.detail

    async def test_transport_error_not_retried(self) -> None:
        """tools/call 不自动重试：传输错误只执行一次即失败。"""
        manager = _search_manager()
        manager.error = MCPProtocolError(detail="MCP Tool 传输失败: search")
        with pytest.raises(MCPProtocolError):
            await MCPExecutionService(manager).execute(_command())
        assert manager.call_count == 1

    async def test_result_truncated_at_block_boundary(self) -> None:
        manager = _search_manager()
        # 4 个 100 KiB 文本块 → 上限 256 KiB：保留 2 块，截断标记
        big = "x" * (100 * 1024)
        manager.result = _call_result(
            content=[_content("text", text=big) for _ in range(4)]
        )
        result = await MCPExecutionService(manager).execute(_command())
        assert result.truncated is True
        assert len(result.content) == 2
        total = sum(len(b.text.encode()) for b in result.content if b.text)
        assert total <= DEFAULT_MAX_RESULT_BYTES

    async def test_oversized_structured_dropped(self) -> None:
        manager = _search_manager()
        manager.result = _call_result(
            structured={"blob": "y" * (300 * 1024)},
            content=[_content("text", text="ok")],
        )
        result = await MCPExecutionService(manager).execute(_command())
        assert result.truncated is True
        assert result.structured_content is None
        assert result.content[0].text == "ok"


# ======================================================================
# MCPRemoteTool 与 ToolRegistry 集成
# ======================================================================


class TestMCPRemoteTool:
    def _registered(self) -> tuple[ToolRegistry, _FakeManager]:
        manager = _search_manager()
        registry = ToolRegistry()
        names = register_mcp_remote_tools(registry, manager)
        assert names == ["mcp__research__search"]
        return registry, manager

    def test_metadata_marks_external(self) -> None:
        registry, _ = self._registered()
        metadata = registry.get_metadata("mcp__research__search")
        assert metadata.execution_kind == "external"
        assert metadata.source_id == "research"
        assert metadata.requires_confirmation is True
        assert metadata.network_access is True

    def test_local_and_external_partition(self) -> None:
        registry, _ = self._registered()

        from app.tools.protocol import LocalDeterministicTool

        class EchoTool(LocalDeterministicTool):
            def __init__(self) -> None:
                from app.tools.protocol import ToolMetadata

                self.metadata = ToolMetadata(name="echo")

            async def execute(self, **kwargs: Any) -> Any:
                return kwargs

        registry.register(EchoTool())
        assert [t.metadata.name for t in registry.list_by_execution_kind("external")] == [
            "mcp__research__search"
        ]
        assert [t.metadata.name for t in registry.list_by_execution_kind("local")] == ["echo"]

    async def test_execute_delegates_to_execution_service(self) -> None:
        registry, manager = self._registered()
        manager.result = _call_result(
            content=[_content("text", text="hit")], structured={"result": "hit"}
        )
        tool = registry.get("mcp__research__search")
        assert isinstance(tool, MCPRemoteTool)
        result = await tool.execute(query="drama")
        assert result.structured_content == {"result": "hit"}
        assert manager.last_arguments == {"query": "drama"}

    async def test_execute_invalid_args_no_remote_call(self) -> None:
        registry, manager = self._registered()
        tool = registry.get("mcp__research__search")
        with pytest.raises(MCPToolArgumentInvalidError):
            await tool.execute(query=123)
        assert manager.call_count == 0

    def test_duplicate_registration_conflict(self) -> None:
        from app.core.errors import AppError

        manager = _search_manager()
        registry = ToolRegistry()
        register_mcp_remote_tools(registry, manager)
        with pytest.raises(AppError) as exc_info:
            register_mcp_remote_tools(registry, manager)
        assert exc_info.value.code == "TOOL_ALREADY_REGISTERED"

    def test_no_available_tools_registers_nothing(self) -> None:
        manager = _FakeManager({"research": [_sdk_tool("a")]}, allowed=["other"])
        registry = ToolRegistry()
        assert register_mcp_remote_tools(registry, manager) == []
