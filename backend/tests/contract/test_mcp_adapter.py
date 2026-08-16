"""MCP Adapter 契约测试（I-04）。

覆盖 I-04 任务卡验收：
- 无 MCP 配置时主流程完全可用（register_mcp_tools(enabled=False) 返回空、不触碰注册表）
- Fake MCP Tool 可注册、调用、超时（httpx MockTransport，无真实网络）
- 外部错误不会泄露内部连接信息（detail 泛化）
- 重名策略明确（409 TOOL_ALREADY_REGISTERED）
- Skill / Tool 注册表元数据查询入口

MockTransport 由 httpx 提供，进程内完成请求/响应，不发起真实网络调用。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.errors import AppError, ExternalToolError, ExternalToolTimeoutError
from app.integrations.mcp.adapter import MCPToolAdapter, register_mcp_tools
from app.integrations.mcp.protocol import MCPAdapterConfig, MCPToolSpec
from app.llm.retry import RetryPolicy
from app.skills.protocol import Skill, SkillMetadata
from app.skills.registry import SkillRegistry
from app.tools.registry import ToolRegistry

# 固定 base_url：测试中不发起真实网络，仅用于构建请求
_BASE_URL = "http://mcp.test.local"


def _spec(name: str = "web_search", **kwargs: Any) -> MCPToolSpec:
    """构造一个最小外部工具描述。"""
    defaults = {
        "name": name,
        "description": f"外部工具 {name}",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "output_schema": {"type": "object", "properties": {"items": {"type": "array"}}},
    }
    defaults.update(kwargs)
    return MCPToolSpec(**defaults)


def _config(**kwargs: Any) -> MCPAdapterConfig:
    """构造 MCP 连接配置（默认开启）。"""
    defaults = {
        "enabled": True,
        "base_url": _BASE_URL,
        "timeout_seconds": 5.0,
        "prefix": "mcp_",
    }
    defaults.update(kwargs)
    return MCPAdapterConfig(**defaults)


def _fast_retry() -> RetryPolicy:
    """测试用快速退避策略（不拖慢测试）。"""
    return RetryPolicy(base_delay=0.001, factor=1.0, max_retries=1, max_delay=0.01)


def _handler(response: httpx.Response) -> httpx.Response:
    """返回固定响应的同步 handler（配合 MockTransport）。"""
    return response


# ========================================================================
# MCPToolAdapter
# ========================================================================


class TestMCPToolAdapterMetadata:
    """元数据映射与注册名。"""

    def test_name_uses_prefix(self) -> None:
        """注册名 = config.prefix + 外部工具名。"""
        adapter = MCPToolAdapter(_spec("web_search"), _config())
        assert adapter.metadata.name == "mcp_web_search"

    def test_schemas_passthrough(self) -> None:
        """input_schema / output_schema 原样透传到内部元数据。"""
        spec = _spec("search", input_schema={"a": 1}, output_schema={"b": 2})
        adapter = MCPToolAdapter(spec, _config())
        assert adapter.metadata.input_schema == {"a": 1}
        assert adapter.metadata.output_schema == {"b": 2}

    def test_custom_prefix(self) -> None:
        """自定义前缀生效。"""
        adapter = MCPToolAdapter(_spec("tool_a"), _config(prefix="ext_"))
        assert adapter.metadata.name == "ext_tool_a"


class TestMCPToolAdapterExecute:
    """JSON-RPC 调用与错误处理。"""

    @pytest.mark.asyncio
    async def test_execute_success_returns_result(self) -> None:
        """成功调用返回外部工具的 result。"""
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "result": {"items": ["战神逆袭"]}, "id": 1}
            )

        adapter = MCPToolAdapter(
            _spec("web_search"),
            _config(),
            transport=httpx.MockTransport(handler),
            retry_policy=_fast_retry(),
        )
        result = await adapter.execute(query="都市赘婿")
        assert result == {"items": ["战神逆袭"]}

    @pytest.mark.asyncio
    async def test_execute_builds_jsonrpc_payload(self) -> None:
        """请求体符合 JSON-RPC 2.0：method / params / id / jsonrpc。"""
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            captured["content_type"] = request.headers.get("content-type", "")
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": 42, "id": 1})

        adapter = MCPToolAdapter(
            _spec("compute"),
            _config(),
            transport=httpx.MockTransport(handler),
            retry_policy=_fast_retry(),
        )
        await adapter.execute(a=1, b="x")
        payload = captured["payload"]
        assert payload["jsonrpc"] == "2.0"
        assert payload["method"] == "compute"
        assert payload["params"] == {"a": 1, "b": "x"}
        assert isinstance(payload["id"], int)
        assert captured["content_type"].startswith("application/json")

    @pytest.mark.asyncio
    async def test_execute_timeout_raises_external_timeout(self) -> None:
        """超时 → EXTERNAL_TOOL_TIMEOUT（504），不重试。"""

        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Read timed out")

        adapter = MCPToolAdapter(
            _spec("web_search"),
            _config(),
            transport=httpx.MockTransport(handler),
            retry_policy=_fast_retry(),
        )
        with pytest.raises(ExternalToolTimeoutError) as exc_info:
            await adapter.execute(query="x")
        assert exc_info.value.status_code == 504
        assert exc_info.value.code == "EXTERNAL_TOOL_TIMEOUT"

    @pytest.mark.asyncio
    async def test_execute_http_500_retries_then_generalizes(self) -> None:
        """5xx 重试耗尽后 → EXTERNAL_TOOL_ERROR（502），detail 泛化。"""
        calls: list[int] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(500, json={"detail": "db connection refused"})

        adapter = MCPToolAdapter(
            _spec("web_search"),
            _config(),
            transport=httpx.MockTransport(handler),
            retry_policy=_fast_retry(),  # max_retries=1 → 共 2 次尝试
        )
        with pytest.raises(ExternalToolError) as exc_info:
            await adapter.execute(query="x")
        assert exc_info.value.status_code == 502
        assert exc_info.value.code == "EXTERNAL_TOOL_ERROR"
        assert len(calls) == 2  # 首次 + 1 次重试

    @pytest.mark.asyncio
    async def test_execute_429_then_success_with_retry_after(self) -> None:
        """429 + Retry-After 退避后重试成功。"""
        calls: list[int] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(429, headers={"retry-after": "0"}, json={})
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": "ok", "id": 1})

        adapter = MCPToolAdapter(
            _spec("web_search"),
            _config(),
            transport=httpx.MockTransport(handler),
            retry_policy=_fast_retry(),
        )
        result = await adapter.execute(query="x")
        assert result == "ok"
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_execute_http_400_not_retried(self) -> None:
        """4xx（非 429）不可重试，立即失败。"""
        calls: list[int] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(400, json={"detail": "bad request"})

        adapter = MCPToolAdapter(
            _spec("web_search"),
            _config(),
            transport=httpx.MockTransport(handler),
            retry_policy=_fast_retry(),
        )
        with pytest.raises(ExternalToolError):
            await adapter.execute(query="x")
        assert len(calls) == 1  # 不重试

    @pytest.mark.asyncio
    async def test_execute_jsonrpc_error_body(self) -> None:
        """响应体带 error 字段 → EXTERNAL_TOOL_ERROR。"""
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": 1},
            )

        adapter = MCPToolAdapter(
            _spec("nope"),
            _config(),
            transport=httpx.MockTransport(handler),
            retry_policy=_fast_retry(),
        )
        with pytest.raises(ExternalToolError) as exc_info:
            await adapter.execute()
        assert exc_info.value.code == "EXTERNAL_TOOL_ERROR"

    @pytest.mark.asyncio
    async def test_execute_non_json_response(self) -> None:
        """200 但响应体不是合法 JSON → EXTERNAL_TOOL_ERROR。"""
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>gateway error</html>")

        adapter = MCPToolAdapter(
            _spec("web_search"),
            _config(),
            transport=httpx.MockTransport(handler),
            retry_policy=_fast_retry(),
        )
        with pytest.raises(ExternalToolError) as exc_info:
            await adapter.execute(query="x")
        assert exc_info.value.code == "EXTERNAL_TOOL_ERROR"

    @pytest.mark.asyncio
    async def test_error_does_not_leak_internal_info(self) -> None:
        """外部错误 detail 不包含 base_url、内部地址或异常文本。"""
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused to 10.0.0.5:8080")

        adapter = MCPToolAdapter(
            _spec("web_search"),
            _config(base_url="http://internal-secret-host:1234"),
            transport=httpx.MockTransport(handler),
            retry_policy=_fast_retry(),
        )
        with pytest.raises(ExternalToolError) as exc_info:
            await adapter.execute(query="x")
        detail = str(exc_info.value)
        assert "internal-secret-host" not in detail
        assert "10.0.0.5" not in detail
        assert "Connection refused" not in detail
        assert detail == "外部工具 web_search 调用失败"  # 完全泛化


# ========================================================================
# register_mcp_tools
# ========================================================================


class TestRegisterMCPTools:
    """批量注册与开关。"""

    def test_disabled_returns_empty_and_untouched(self) -> None:
        """无 MCP 配置（enabled=False）→ 返回空列表，注册表不受影响。"""
        registry = ToolRegistry()
        registered = register_mcp_tools(
            registry, [_spec("web_search")], _config(enabled=False)
        )
        assert registered == []
        assert registry.list_all() == []

    def test_register_adds_prefixed_names(self) -> None:
        """启用时把全部外部工具注册为 prefix+name。"""
        registry = ToolRegistry()
        registered = register_mcp_tools(
            registry,
            [_spec("web_search"), _spec("fetch_page")],
            _config(),
            transport=httpx.MockTransport(_handler(httpx.Response(200, json={}))),
            retry_policy=_fast_retry(),
        )
        assert registered == ["mcp_web_search", "mcp_fetch_page"]
        assert registry.get("mcp_web_search").metadata.name == "mcp_web_search"

    def test_duplicate_registration_409(self) -> None:
        """重名注册 → 409 TOOL_ALREADY_REGISTERED（策略明确）。"""
        registry = ToolRegistry()
        register_mcp_tools(
            registry, [_spec("web_search")], _config(), retry_policy=_fast_retry()
        )
        # 再次注册同名外部工具（同一前缀冲突）→ 409
        with pytest.raises(AppError) as exc_info:
            register_mcp_tools(
                registry, [_spec("web_search")], _config(), retry_policy=_fast_retry()
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == "TOOL_ALREADY_REGISTERED"

    def test_registry_list_metadata(self) -> None:
        """ToolRegistry 元数据查询：list_metadata / get_metadata。"""
        registry = ToolRegistry()
        register_mcp_tools(
            registry, [_spec("web_search")], _config(), retry_policy=_fast_retry()
        )
        metas = registry.list_metadata()
        assert [m.name for m in metas] == ["mcp_web_search"]
        assert registry.get_metadata("mcp_web_search").name == "mcp_web_search"
        # 内部工具也能查到（word_count 样例 schema）
        with pytest.raises(AppError) as exc_info:
            registry.get_metadata("does_not_exist")
        assert exc_info.value.code == "TOOL_NOT_FOUND"


# ========================================================================
# SkillRegistry 元数据查询（I-04 扩展入口）
# ========================================================================


class _StubSkill(Skill):
    """最小 Skill 桩：仅用于验证注册表元数据查询。"""

    metadata = SkillMetadata(
        name="stub_skill", version="1.0", description="最小示例技能"
    )

    async def execute(self, context: dict[str, Any]) -> Any:
        return context


class TestSkillRegistryMetadata:
    """Skill 注册表元数据查询入口。"""

    def test_get_and_list_metadata(self) -> None:
        """get_metadata / list_metadata 返回可序列化元数据。"""
        registry = SkillRegistry()
        registry.register(_StubSkill())
        assert registry.get_metadata("stub_skill").name == "stub_skill"
        metas = registry.list_metadata()
        assert len(metas) == 1
        assert metas[0].description == "最小示例技能"

    def test_get_metadata_missing_raises_404(self) -> None:
        """未注册技能 → 404 SKILL_NOT_FOUND。"""
        registry = SkillRegistry()
        with pytest.raises(AppError) as exc_info:
            registry.get_metadata("missing")
        assert exc_info.value.code == "SKILL_NOT_FOUND"
