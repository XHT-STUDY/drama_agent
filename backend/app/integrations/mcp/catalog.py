"""MCPToolCatalog — 工具目录、definition digest 与原子替换（MCP-02）。

Catalog 保存最近一次验证通过的工具定义：
- 内部 ID 固定为 ``mcp__{server_id}__{tool_name}``（原始名仅供 SDK 调用）；
- 定义 digest 覆盖名称、输入 Schema、输出 Schema 与 annotations，
  供确认/执行时二次校验（定义变化 → MCP_TOOL_STALE）；
- 更新按 Server 原子替换；刷新失败标 stale（不允许发起新调用），
  旧快照保留供已有 Action 做过期判断。
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.errors import MCPConfigInvalidError
from app.integrations.mcp.protocol import SERVER_ID_RE

# 内部 ID 前缀（区分于本地工具与其他来源）
TOOL_ID_PREFIX = "mcp__"

MCPToolStatus = Literal["available", "stale", "unavailable"]


class MCPToolHints(BaseModel):
    """MCP annotation 的风险提示（外部声明，仅展示用，不可作为授权依据）。"""

    model_config = {"extra": "forbid"}

    title: str | None = None
    read_only_hint: bool | None = None
    destructive_hint: bool | None = None
    idempotent_hint: bool | None = None
    open_world_hint: bool | None = None


class MCPToolDefinition(BaseModel):
    """Catalog 中的单个工具定义（最近一次发现并通过 allowlist 的版本）。"""

    model_config = {"extra": "forbid"}

    server_id: str
    tool_name: str = Field(..., min_length=1, description="Server 上的原始工具名")
    qualified_tool_name: str = Field(..., min_length=1)
    title: str | None = None
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    annotations: MCPToolHints | None = None
    definition_digest: str = Field(..., min_length=64, max_length=64)
    status: MCPToolStatus = "available"


def qualified_tool_name(server_id: str, tool_name: str) -> str:
    """生成内部工具 ID：mcp__{server_id}__{tool_name}。

    Raises:
        MCPConfigInvalidError: server_id 或 tool_name 无法形成合法内部 ID。
    """
    if not SERVER_ID_RE.match(server_id or ""):
        raise MCPConfigInvalidError(detail=f"MCP Server ID 非法: {server_id!r}")
    if not tool_name or "__" in tool_name or any(ch.isspace() for ch in tool_name):
        raise MCPConfigInvalidError(
            detail=f"MCP 工具名无法形成合法内部 ID（非空、不含 __ 与空白）: {tool_name!r}"
        )
    return f"{TOOL_ID_PREFIX}{server_id}__{tool_name}"


def compute_definition_digest(
    *,
    tool_name: str,
    input_schema: dict[str, Any] | None,
    output_schema: dict[str, Any] | None,
    annotations: MCPToolHints | None,
) -> str:
    """计算稳定 definition digest（SHA-256，规范化 JSON 序列化）。"""
    payload = json.dumps(
        {
            "name": tool_name,
            "input_schema": input_schema or {},
            "output_schema": output_schema or {},
            "annotations": annotations.model_dump(exclude_none=True) if annotations else {},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def tool_definition_from_sdk(
    server_id: str,
    tool: Any,
) -> MCPToolDefinition:
    """把官方 SDK 的 Tool 定义转换为内部 Catalog 定义。"""
    name = str(getattr(tool, "name", ""))
    input_schema = dict(getattr(tool, "input_schema", None) or {})
    output_schema = getattr(tool, "output_schema", None)
    raw_annotations = getattr(tool, "annotations", None)
    annotations = (
        MCPToolHints(
            title=getattr(raw_annotations, "title", None),
            read_only_hint=getattr(raw_annotations, "read_only_hint", None),
            destructive_hint=getattr(raw_annotations, "destructive_hint", None),
            idempotent_hint=getattr(raw_annotations, "idempotent_hint", None),
            open_world_hint=getattr(raw_annotations, "open_world_hint", None),
        )
        if raw_annotations is not None
        else None
    )
    digest = compute_definition_digest(
        tool_name=name,
        input_schema=input_schema,
        output_schema=dict(output_schema) if output_schema else None,
        annotations=annotations,
    )
    return MCPToolDefinition(
        server_id=server_id,
        tool_name=name,
        qualified_tool_name=qualified_tool_name(server_id, name),
        title=getattr(tool, "title", None),
        description=str(getattr(tool, "description", "") or ""),
        input_schema=input_schema,
        output_schema=dict(output_schema) if output_schema else None,
        annotations=annotations,
        definition_digest=digest,
    )


def build_server_definitions(
    server_id: str,
    sdk_tools: list[Any],
    allowed_tools: list[str],
) -> list[MCPToolDefinition]:
    """SDK 工具列表 → allowlist 过滤后的 catalog 定义列表。

    - ``allowed_tools`` 为硬白名单：空列表 → 全部不可调用（返回空）；
    - 单个工具名无法形成合法内部 ID 时跳过该工具（拒绝注册，不影响其他
      工具与 Server 状态）。
    """
    allowed = set(allowed_tools)
    definitions: list[MCPToolDefinition] = []
    for tool in sdk_tools:
        try:
            definition = tool_definition_from_sdk(server_id, tool)
        except MCPConfigInvalidError:
            continue  # 工具名非法：拒绝注册该工具，不中断发现
        if definition.tool_name not in allowed:
            continue  # 硬白名单：不在列表内（含空列表）一律不可调用
        definitions.append(definition)
    return definitions


class MCPToolCatalog:
    """进程内工具目录（线程安全，按 Server 原子替换）。

    不做权限决策：allowed_tools 过滤由构建方（Manager）在入库前完成。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_qualified: dict[str, MCPToolDefinition] = {}
        self._server_tools: dict[str, list[str]] = {}

    def replace_server_tools(
        self, server_id: str, definitions: list[MCPToolDefinition]
    ) -> None:
        """整 Server 原子替换（旧定义全部移除，包括已消失的工具）。"""
        with self._lock:
            for qualified in self._server_tools.pop(server_id, []):
                self._by_qualified.pop(qualified, None)
            qualified_names = [d.qualified_tool_name for d in definitions]
            for definition in definitions:
                self._by_qualified[definition.qualified_tool_name] = definition
            self._server_tools[server_id] = qualified_names

    def mark_server_stale(self, server_id: str) -> None:
        """Server 刷新失败：该 Server 全部工具转 stale（不允许新调用）。"""
        with self._lock:
            for qualified in self._server_tools.get(server_id, []):
                definition = self._by_qualified.get(qualified)
                if definition is not None and definition.status == "available":
                    self._by_qualified[qualified] = definition.model_copy(
                        update={"status": "stale"}
                    )

    def remove_server(self, server_id: str) -> None:
        """Server 下线：移除其全部工具定义。"""
        with self._lock:
            for qualified in self._server_tools.pop(server_id, []):
                self._by_qualified.pop(qualified, None)

    def get(self, qualified_tool_name: str) -> MCPToolDefinition | None:
        with self._lock:
            definition = self._by_qualified.get(qualified_tool_name)
            return definition.model_copy(deep=True) if definition else None

    def get_by_names(self, server_id: str, tool_name: str) -> MCPToolDefinition | None:
        return self.get(qualified_tool_name(server_id, tool_name))

    def list_available(self) -> list[MCPToolDefinition]:
        with self._lock:
            return [
                d.model_copy(deep=True)
                for d in self._by_qualified.values()
                if d.status == "available"
            ]

    def list_for_server(self, server_id: str) -> list[MCPToolDefinition]:
        with self._lock:
            return [
                self._by_qualified[q].model_copy(deep=True)
                for q in self._server_tools.get(server_id, [])
                if q in self._by_qualified
            ]

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_qualified)
