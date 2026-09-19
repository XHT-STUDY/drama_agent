# EXTENSIONS — 扩展契约（I-04）

本文档说明如何在不改动主流程的前提下扩展 DramaAgent：

1. **新增 Skill**（最小示例见下）—— 单一可复用业务任务单元；
2. **新增内部 Tool** —— 确定性纯函数能力，声明 `input_schema`；
3. **接入标准 MCP Server** —— 官方 SDK（Tools-only Client）发现并调用外部工具。

扩展注册表查询入口（I-04 新增）：`ToolRegistry.list_metadata()` / `get_metadata(name)`，
`SkillRegistry.list_metadata()` / `get_metadata(name)`，返回可序列化元数据
（SkillMetadata / ToolMetadata），供扩展面板、MCP 契约测试等使用。

---

## 1. 新增 Skill（最小示例）

### 1.1 协议

Skill 必须实现 `app.skills.protocol.Skill`：

```python
# backend/app/skills/protocol.py（简化）
class SkillMetadata(BaseModel):
    name: str          # 唯一名称，如 "story_bible_writer"
    version: str = "1.0"
    description: str = ""

class Skill(ABC):
    metadata: SkillMetadata
    @abstractmethod
    async def execute(self, context: dict[str, Any]) -> Any: ...
```

约束：
- `execute` 只读 `context`，**不访问 HTTP / 前端 / 直接操作 ORM**；
- 若要调 LLM，通过 `context["agent"].generate_structured(...)`（FakeLLM 契约自动生效），
  输出必须经过 Pydantic 校验；
- 结果必须是可序列化的领域对象（Pydantic model 或基础类型）。

### 1.2 最小实现（确定性 Skill，不调 LLM）

```python
# backend/app/skills/word_stats.py
"""WordStatsSkill — 字数统计 Skill（扩展示例，I-04）。

纯确定性实现，不调 LLM；演示新增 Skill 的最小完整结构。
"""
from __future__ import annotations

from typing import Any

from app.skills.protocol import Skill, SkillMetadata
from app.tools.word_count import count_chinese_chars


class WordStatsSkill(Skill):
    """统计文本中文字符数。"""

    metadata = SkillMetadata(
        name="word_stats",
        version="1.0",
        description="统计文本中文字符数（扩展示例 Skill）",
    )

    async def execute(self, context: dict[str, Any]) -> dict[str, int]:
        """context["text"] → {"chinese_chars": int}。"""
        text: str = context["text"]
        return {"chinese_chars": count_chinese_chars(text)}
```

### 1.3 注册

在应用组装处（如 SkillRegistry 初始化）注册后即可被查询与使用：

```python
from app.skills.registry import SkillRegistry
from app.skills.word_stats import WordStatsSkill

registry = SkillRegistry()
registry.register(WordStatsSkill())

# I-04 元数据查询入口
assert registry.get_metadata("word_stats").name == "word_stats"
assert [m.name for m in registry.list_metadata()] == ["word_stats"]
```

> 若 Skill 需要 LLM：参考 `app/skills/import_classifier.py`（规则先行 + LLM 兜底）与
> `app/skills/story_bible.py`。`execute` 的 `context` 由工作流节点注入
> `agent` / `prompt_loader` 等依赖。

---

## 2. 新增内部 Tool

Tool 是**确定性纯函数**能力（不得隐式调用 LLM、不访问网络）。
每个 Tool 必须声明 `input_schema` / `output_schema`（供 MCP Adapter 与契约测试使用）。

```python
# backend/app/tools/word_count.py（WordCountTool 的 schema 声明，已含样例）
metadata = ToolMetadata(
    name="compute_word_count",
    version="1.0",
    description="统计文本中中文字符 (含标点) 的数量——覆盖 LLM 自报值",
    input_schema={
        "type": "object",
        "properties": {"plain_text": {"type": "string"}},
        "required": ["plain_text"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "chinese_chars": {"type": "integer"},
            "chinese_chars_with_punct": {"type": "integer"},
            "total_chars": {"type": "integer"},
        },
        "required": ["chinese_chars", "chinese_chars_with_punct", "total_chars"],
    },
)
```

注册与查询：

```python
from app.tools.registry import ToolRegistry
from app.tools.word_count import WordCountTool

registry = ToolRegistry()
registry.register(WordCountTool())
assert registry.get_metadata("compute_word_count").input_schema["required"] == ["plain_text"]
```

---

## 3. 接入标准 MCP Server（MCP-01+）

DramaAgent 是 **Tools-only 的 MCP Host/Client**：使用官方 `mcp` Python SDK
（`mcp>=2,<3`，见 [MCP_DESIGN.md](MCP_DESIGN.md)）连接管理员配置的标准
MCP Server，经 `tools/list` 发现工具、`tools/call` 调用工具。生产传输只使用
Streamable HTTP；`MCP_ENABLED=false`（默认）时完全不加载。

### 3.1 配置（MCP_SERVERS_JSON）

`.env`（完整样例见 `.env.example`）：

```env
MCP_ENABLED=true
MCP_SERVERS_JSON=[
  {"id": "research",
   "url": "https://mcp-research.example.com/mcp",
   "enabled": true,
   "allowed_tools": ["web_search"],
   "bearer_token_env": "MCP_RESEARCH_TOKEN",
   "timeout_seconds": 30,
   "max_concurrency": 4,
   "allow_private_network": false}
]
MCP_RESEARCH_TOKEN=   # Token 只在环境变量，不进配置/日志
```

字段语义（`app.integrations.mcp.protocol.MCPServerConfig`）：

| 字段 | 规则 |
| --- | --- |
| `id` | 小写字母/数字/下划线，进程内唯一 |
| `url` | Streamable HTTP 端点；生产默认只接受 HTTPS |
| `enabled` | 是否加载该 Server |
| `allowed_tools` | **硬白名单**（原始工具名）；空列表 = 全部不可调用 |
| `bearer_token_env` | Bearer Token 环境变量名（不保存值） |
| `timeout_seconds` | 单次调用总超时（默认 30s，超时取消在途调用） |
| `max_concurrency` | 该 Server 并发上限（默认 4） |
| `allow_private_network` | 显式允许 loopback/私网目标（默认拒绝，本地开发用） |

旧 `MCP_BASE_URL`/`MCP_TIMEOUT_SECONDS` 映射为 `id=default` 的单 Server
（弃用，保留一个发布周期）。

### 3.2 启动发现与工具目录

FastAPI lifespan → `MCPClientManager.startup()`：逐 Server 校验 URL 安全线
（HTTPS 强制、回环/私网/link-local 拒绝、DNS 解析校验）→ 官方 SDK 协议协商 →
`tools/list`（自动翻页）→ allowlist 过滤 → 计算 definition digest →
`MCPToolCatalog` 按 Server 原子发布。任一 Server 失败只标记 `degraded`
（错误码 `MCP_CONFIG_INVALID` / `MCP_SERVER_UNAVAILABLE` / `MCP_PROTOCOL_ERROR`
/ `MCP_DISCOVERY_TIMEOUT`），**不阻断应用启动**。

内部工具 ID 固定为 `mcp__{server_id}__{tool_name}`；同名工具在不同 Server 下
不冲突。对话链路（MCP-03）直接读 catalog 与执行服务；
`register_mcp_remote_tools(registry, manager)` 把 catalog 中 available 的工具
注册到 `ToolRegistry`（元数据标记 `execution_kind="external"`、
`requires_confirmation=True`、`network_access=True`），供程序化场景使用
（本项目无进程级全局 ToolRegistry，故不在 lifespan 自动注册）。

### 3.3 调用链路与安全边界

所有 MCP 调用走对话确认链路（MCP-03）：

```text
用户请求 → Planner（只从有界目录选 qualified_tool_name，≤20 个候选）
  → 服务端校验（目录成员 + allowlist + JSON Schema + digest 快照）
  → AgentAction(proposed) → 用户确认 → WorkflowRun(action=mcp_tool_call)
  → MCPExecutionService（再次校验 digest/Schema → Manager 调用 →
     结果规范化 + 256KiB 截断 + outputSchema 校验）→ action_result 消息
```

边界（详见 MCP_DESIGN §11）：

- 工具描述/结果按**不可信内容**处理（user_content_vars 边界注入，不拼
  system 指令）；注入文本不改变控制流，也不能触发第二个工具；
- `tools/call` **不自动重试**（非幂等外部写保护）；发现/刷新可退避；
- Planner 输出目录外工具名、URL、SQL 一律拒绝；
- 一次 AgentAction 只含一个工具调用；结果不直接写 Artifact；
- Token 从环境变量读取，不进配置快照/日志/错误响应；
- 指标只用低基数标签（`server_id`/`status`）：`mcp_server_status`、
  `mcp_discovery_total`、`mcp_call_total`、`mcp_call_duration_seconds`；
- structured content 整体超过 256 KiB 时**整体丢弃**（JSON 无法安全截断，
  标记 `truncated=true`），文本块按内容块边界截断；
- catalog 目前在启动时构建；`refresh_tools` 提供 stale 恢复入口，尚未订阅
  `tools/list_changed` 通知或周期刷新（重启或手动刷新生效；确认/执行时的
  digest 校验保证正确性不受旧定义影响）；
- 旧 `MCP_BASE_URL` 兼容映射的 `allowed_tools=[]` 按新语义=全部不可调用——
  从旧配置迁移必须显式设置白名单。

错误码（MCP-02）：

| 场景 | HTTP | 错误码 |
| --- | --- | --- |
| 工具不存在/不在白名单 | 404 | `MCP_TOOL_NOT_AVAILABLE` |
| 定义在计划后变化 | 409 | `MCP_TOOL_STALE` |
| 输入 Schema 不合法 | 422 | `MCP_TOOL_ARGUMENT_INVALID` |
| 调用超时 | 504 | `MCP_TOOL_TIMEOUT` |
| 协议/传输失败 | 502 | `MCP_PROTOCOL_ERROR` |
| 工具返回错误（isError） | 502 | `MCP_TOOL_ERROR` |
| 输出 Schema 不合法 | 502 | `MCP_TOOL_RESULT_INVALID` |

### 3.4 契约与安全测试

- `backend/tests/contract/test_mcp_official_client.py`：官方 in-process
  Server 契约（协商/发现/调用/超时/并发/Token/重定向）；
- `backend/tests/unit/integrations/mcp/`：配置、URL 安全、catalog、执行
  服务（Fake Manager，零网络）；
- `backend/tests/integration/test_mcp_lifecycle.py`：真实 lifespan 生命周期；
- `backend/tests/integration/workflow/test_mcp_tool_call.py`：工作流
  成功/stale/超时/错误/取消/不可用；
- `backend/tests/integration/api/test_agent_mcp_tools.py`：对话链路端到端
  （提议零调用/拒绝/确认一次/幂等/未知工具/stale/单活跃 Run）；
- `backend/tests/security/test_mcp_security.py`：SSRF/DNS/Token/截断/
  注入内容边界；
- `backend/tests/performance/test_mcp_catalog.py`（`-m performance`）：
  1000 工具 catalog 处理 p95 < 300ms；
- 本地互操作 smoke：`cd backend && uv run python scripts/mcp_smoke.py`
  （真实 TCP 上的官方 Streamable HTTP Server，无互联网/凭证依赖）。

### 3.5 弃用：I-04 手写 JSON-RPC Adapter

`MCPToolAdapter` / `register_mcp_tools`（`MCPToolSpec`/`MCPAdapterConfig`）
为 I-04 手写 JSON-RPC 实现，**非标准 `tools/call` 格式、无协议协商**，
已标记 `DeprecationWarning`，保留一个发布周期后删除；契约测试
`tests/contract/test_mcp_adapter.py` 期间继续回归。

---

## 4. 扩展边界（不要做什么）

- 内部 File / RAG / Export 仍使用内部实现，**不通过 MCP 调用**；
- 内部（local）Tool `execute` 保持纯确定性（不调 LLM / 不访问网络）；外部（external）Tool 必须经 AgentAction 确认链路执行，模型不得直接触发；
- LLM 输出必须过结构化 Pydantic v2 校验后才能写入 Artifact（Artifact 不可变，修订产生新版本）；
- 测试一律用 FakeLLM，禁止真实 LLM 调用。
