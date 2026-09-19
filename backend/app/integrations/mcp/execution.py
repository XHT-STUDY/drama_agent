"""MCPExecutionService — MCP Tool 调用的业务安全边界（MCP-02）。

执行序列（MCP_DESIGN §8.5）：
1. Server 与 Tool 仍为 available（catalog 查询）；
2. 比对计划时保存的 definition digest（变化 → MCP_TOOL_STALE）；
3. allowed_tools 已由 catalog 构建时过滤（命中定义即可调用）；
4. 输入 JSON Schema 2020-12 校验（失败不发远程请求）；
5. 经 MCPClientManager 调用（总超时、并发闸、无自动重试）；
6. 内容规范化 + 256 KiB 上限（按内容块边界截断）；
7. structured content 的 outputSchema 校验；
8. 返回可持久化、可展示的 MCPToolResult。

外部内容默认不可信：text / structured 只进入受限上下文，
图片、音频与 embedded resource 不进入 LLM 可消费字段。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.errors import (
    AppError,
    MCPToolArgumentInvalidError,
    MCPToolError,
    MCPToolNotAvailableError,
    MCPToolResultInvalidError,
    MCPToolStaleError,
)
from app.core.logging import get_logger
from app.integrations.mcp.catalog import MCPToolCatalog, MCPToolDefinition
from app.integrations.mcp.manager import MCPClientManager

logger = get_logger(__name__)

# 单次结果默认上限（256 KiB，按内容块边界截断）
DEFAULT_MAX_RESULT_BYTES = 256 * 1024
# is_error 结果摘要上限（脱敏：只保留有限文本）
_ERROR_SUMMARY_MAX_CHARS = 500


class MCPToolCallCommand(BaseModel):
    """程序化 MCP Tool 调用请求（应用层模型，MCP-02）。

    MCP-03 的领域命令（AgentCommand）会转换为该请求；执行侧只信任
    服务端持久化的字段。
    """

    model_config = {"extra": "forbid"}

    server_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    expected_definition_digest: str | None = Field(
        default=None,
        description="计划构建时的定义 digest；执行时二次校验，不一致 → stale",
    )
    purpose: str = Field(default="", max_length=2000, description="调用目的（审计展示）")


class MCPContentBlock(BaseModel):
    """规范化后的单个内容块（不含二进制本体）。"""

    model_config = {"extra": "forbid"}

    kind: Literal["text", "image", "audio", "resource_link", "embedded_resource"]
    text: str | None = Field(default=None, description="text 块内容（LLM 可消费）")
    mime_type: str | None = None
    resource_uri: str | None = Field(
        default=None, description="resource link / embedded resource 的 URI"
    )
    size_bytes: int | None = Field(default=None, description="原始内容大小（二进制不保留本体）")


class MCPToolResult(BaseModel):
    """可持久化、可展示的 MCP Tool 调用结果（MCP_DESIGN §8.7）。"""

    model_config = {"extra": "forbid"}

    server_id: str
    tool_name: str
    content: list[MCPContentBlock] = Field(default_factory=list)
    structured_content: dict[str, Any] | None = None
    resource_links: list[str] = Field(default_factory=list)
    is_error: bool = False
    error_summary: str | None = Field(default=None, description="is_error 时的脱敏摘要")
    duration_ms: int = 0
    protocol_version: str | None = None
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    truncated: bool = False

    def llm_readable_text(self, max_chars: int = 20000) -> str:
        """LLM 可消费的文本（仅 text 块，受字符上限约束）。"""
        parts: list[str] = []
        for block in self.content:
            if block.kind == "text" and block.text:
                parts.append(block.text)
        joined = "\n".join(parts)
        return joined[:max_chars]


def validate_json_schema(
    schema: dict[str, Any] | None, instance: Any, *, error_cls: type[AppError]
) -> None:
    """JSON Schema 2020-12 校验（$schema 声明优先）。"""
    if not schema:
        return
    from jsonschema.validators import Draft202012Validator, validator_for

    validator_cls = validator_for(schema) or Draft202012Validator
    try:
        validator_cls.check_schema(schema)
    except Exception as exc:  # noqa: BLE001 - Server 声明的 Schema 自身非法
        raise error_cls(detail="工具声明的 JSON Schema 非法") from exc
    errors = sorted(validator_cls(schema).iter_errors(instance), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        path = "/".join(str(p) for p in first.absolute_path) or "<root>"
        raise error_cls(detail=f"JSON Schema 校验失败（{path}）: {first.message}")


def _block_size(block: MCPContentBlock) -> int:
    if block.text is not None:
        return len(block.text.encode("utf-8"))
    return block.size_bytes or 0


class MCPExecutionService:
    """MCP Tool 执行的安全边界（Schema 校验、结果规范化与大小限制）。"""

    def __init__(
        self,
        manager: MCPClientManager,
        *,
        max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
    ) -> None:
        self._manager = manager
        self._max_result_bytes = max_result_bytes

    @property
    def catalog(self) -> MCPToolCatalog:
        return self._manager.catalog

    async def execute(self, command: MCPToolCallCommand) -> MCPToolResult:
        """执行一次 MCP Tool 调用（前置校验失败不发远程请求）。"""
        definition = self._require_definition(command)
        self._check_digest(command, definition)
        validate_json_schema(
            definition.input_schema,
            command.arguments,
            error_cls=MCPToolArgumentInvalidError,
        )
        started = time.monotonic()
        raw = await self._manager.call_tool(
            command.server_id, command.tool_name, command.arguments
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        state = self._manager.get_server_state(command.server_id)
        result = self._normalize(
            command,
            raw,
            duration_ms=duration_ms,
            protocol_version=state.protocol_version if state else None,
        )
        self._validate_output(command, definition, result)
        if result.is_error:
            raise MCPToolError(
                detail=f"MCP Tool 执行失败: {command.tool_name}"
                + (f"（{result.error_summary}）" if result.error_summary else "")
            )
        # 结构化审计日志：只记低敏字段——参数仅字段名，结果仅类型/大小/状态
        # （MCP-04 §12：Token、完整参数与完整返回体不进日志）
        output_bytes = sum(_block_size(b) for b in result.content) + (
            len(json.dumps(result.structured_content, ensure_ascii=False))
            if result.structured_content is not None
            else 0
        )
        logger.info(
            "MCP Tool 调用完成: server=%s tool=%s protocol=%s duration_ms=%d "
            "arg_fields=%s blocks=%d structured=%s truncated=%s output_bytes=%d "
            "request_id=%s",
            command.server_id,
            command.tool_name,
            result.protocol_version,
            duration_ms,
            ",".join(sorted(command.arguments.keys())) or "-",
            len(result.content),
            result.structured_content is not None,
            result.truncated,
            output_bytes,
            result.request_id,
        )
        return result

    # ------------------------------------------------------------------
    # 前置校验
    # ------------------------------------------------------------------

    def _require_definition(self, command: MCPToolCallCommand) -> MCPToolDefinition:
        definition = self.catalog.get_by_names(command.server_id, command.tool_name)
        if definition is None or definition.status != "available":
            raise MCPToolNotAvailableError(
                detail=f"MCP Tool 不可用: {command.server_id}/{command.tool_name}"
            )
        return definition

    @staticmethod
    def _check_digest(
        command: MCPToolCallCommand, definition: MCPToolDefinition
    ) -> None:
        expected = command.expected_definition_digest
        if expected is not None and expected != definition.definition_digest:
            raise MCPToolStaleError(
                detail=(
                    f"MCP Tool 定义已变化，请重新规划: {command.server_id}/{command.tool_name}"
                )
            )

    # ------------------------------------------------------------------
    # 结果规范化
    # ------------------------------------------------------------------

    def _normalize(
        self,
        command: MCPToolCallCommand,
        raw: Any,
        *,
        duration_ms: int,
        protocol_version: str | None,
    ) -> MCPToolResult:
        blocks: list[MCPContentBlock] = []
        resource_links: list[str] = []
        error_texts: list[str] = []
        for item in getattr(raw, "content", None) or []:
            block = self._normalize_block(item)
            if block is None:
                continue
            if block.kind == "resource_link" and block.resource_uri:
                resource_links.append(block.resource_uri)
            if block.kind == "text" and block.text:
                error_texts.append(block.text)
            blocks.append(block)
        structured = getattr(raw, "structured_content", None)
        structured_content = dict(structured) if isinstance(structured, dict) else None
        is_error = bool(getattr(raw, "is_error", False))
        result = MCPToolResult(
            server_id=command.server_id,
            tool_name=command.tool_name,
            content=blocks,
            structured_content=structured_content,
            resource_links=resource_links,
            is_error=is_error,
            error_summary=(
                "\n".join(error_texts)[:_ERROR_SUMMARY_MAX_CHARS] if is_error else None
            ),
            duration_ms=duration_ms,
            protocol_version=protocol_version,
        )
        return self._enforce_size_limit(result)

    @staticmethod
    def _normalize_block(item: Any) -> MCPContentBlock | None:
        """SDK ContentBlock → 规范化块（二进制只保留类型/大小，不保留本体）。"""
        kind = str(getattr(item, "type", "") or "")
        mime_type = getattr(item, "mime_type", None) or getattr(item, "mime_type_", None)
        if kind == "text":
            return MCPContentBlock(kind="text", text=str(getattr(item, "text", "") or ""))
        if kind == "image":
            data = getattr(item, "data", None) or ""
            return MCPContentBlock(
                kind="image",
                mime_type=mime_type,
                size_bytes=len(str(data)) * 3 // 4,  # base64 → 近似原始大小
            )
        if kind == "audio":
            data = getattr(item, "data", None) or ""
            return MCPContentBlock(
                kind="audio",
                mime_type=mime_type,
                size_bytes=len(str(data)) * 3 // 4,
            )
        if kind == "resource_link":
            return MCPContentBlock(
                kind="resource_link",
                resource_uri=str(getattr(item, "uri", "") or ""),
            )
        if kind == "resource":
            # embedded resource：文本保留摘要信息，不进入 LLM
            uri = getattr(item, "uri", None)
            return MCPContentBlock(
                kind="embedded_resource",
                resource_uri=str(uri) if uri else None,
                mime_type=mime_type,
            )
        return MCPContentBlock(kind="text", text="")  # 未知块：空占位，不丢失计数

    def _enforce_size_limit(self, result: MCPToolResult) -> MCPToolResult:
        """按内容块边界截断到 max_result_bytes（structured 超限单独丢弃）。"""
        truncated = False
        budget = self._max_result_bytes

        if result.structured_content is not None:
            structured_bytes = len(
                json.dumps(result.structured_content, ensure_ascii=False).encode("utf-8")
            )
            if structured_bytes > budget:
                # 结构化内容整体超限：不保留（无法安全截断 JSON），标记截断
                result = result.model_copy(
                    update={"structured_content": None, "truncated": True}
                )
                truncated = True

        kept: list[MCPContentBlock] = []
        used = 0
        for block in result.content:
            size = _block_size(block)
            if used + size <= budget:
                kept.append(block)
                used += size
            else:
                truncated = True
        if truncated:
            result = result.model_copy(update={"content": kept, "truncated": True})
        return result

    @staticmethod
    def _validate_output(
        command: MCPToolCallCommand,
        definition: MCPToolDefinition,
        result: MCPToolResult,
    ) -> None:
        """structured content 存在且工具声明 outputSchema 时强制校验。"""
        if result.structured_content is None or not definition.output_schema:
            return
        validate_json_schema(
                       definition.output_schema,
            result.structured_content,
            error_cls=MCPToolResultInvalidError,
        )
