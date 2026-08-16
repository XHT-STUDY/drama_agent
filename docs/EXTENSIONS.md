# EXTENSIONS — 扩展契约（I-04）

本文档说明如何在不改动主流程的前提下扩展 DramaAgent：

1. **新增 Skill**（最小示例见下）—— 单一可复用业务任务单元；
2. **新增内部 Tool** —— 确定性纯函数能力，声明 `input_schema`；
3. **注册 MCP 外部工具** —— 通过 HTTP JSON-RPC 把外部能力映射为内部 Tool。

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

## 3. 注册 MCP 外部工具

`MCPToolAdapter` 把**外部 HTTP JSON-RPC 工具**映射为内部 `Tool`，可复用
I-01 的重试退避（429/5xx）与统一错误码，无需修改内部实现。

### 3.1 配置

`.env`：

```env
MCP_ENABLED=false            # 默认关：主流程完全不受影响
MCP_BASE_URL=http://localhost:9000
MCP_TIMEOUT_SECONDS=10.0
```

等价于 `app.integrations.mcp.protocol.MCPAdapterConfig`：

```python
MCPAdapterConfig(enabled=False, base_url="http://localhost:9000", timeout_seconds=10.0, prefix="mcp_")
```

### 3.2 注册外部工具

```python
from app.integrations.mcp.adapter import register_mcp_tools
from app.integrations.mcp.protocol import MCPAdapterConfig, MCPToolSpec
from app.tools.registry import ToolRegistry

specs = [
    MCPToolSpec(
        name="web_search",
        description="外部搜索引擎工具",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    ),
]
registry = ToolRegistry()
config = MCPAdapterConfig(enabled=True, base_url="http://localhost:9000")
registered = register_mcp_tools(registry, specs, config)  # ["mcp_web_search"]
```

要点：
- **注册名 = `prefix + spec.name`**（默认前缀 `mcp_`），避免与内部工具重名；
- `config.enabled=False`（默认）时 `register_mcp_tools` 返回空列表、不触碰注册表 ——
  **无 MCP 配置时主流程完全可用**；
- **重名策略明确**：同名前缀冲突时注册抛 `409 TOOL_ALREADY_REGISTERED`
  （由 `ToolRegistry.register` 统一裁决）。

### 3.3 调用与错误语义

`MCPToolAdapter.execute(**kwargs)` 发送 JSON-RPC 2.0 POST：

```json
{"jsonrpc": "2.0", "method": "web_search", "params": {"query": "..."}, "id": 1}
```

错误映射（**不泄露内部连接信息**，detail 一律泛化）：

| 场景 | 异常 | 状态码 | 错误码 |
| --- | --- | --- | --- |
| 超时（>timeout_seconds） | `ExternalToolTimeoutError` | 504 | `EXTERNAL_TOOL_TIMEOUT` |
| 连接失败 / 传输错误 | `ExternalToolError` | 502 | `EXTERNAL_TOOL_ERROR` |
| HTTP ≥ 400（429/5xx 先重试耗尽） | `ExternalToolError` | 502 | `EXTERNAL_TOOL_ERROR` |
| JSON-RPC `error` 字段 / 响应不可解析 | `ExternalToolError` | 502 | `EXTERNAL_TOOL_ERROR` |

重试：429 / 5xx 按 `RetryPolicy` 指数退避重试，尊重 `Retry-After` 头
（复用 I-01 `app.llm.retry`）。

### 3.4 契约测试

`backend/tests/contract/test_mcp_adapter.py` 用 `httpx.MockTransport` 在进程内模拟
FakeMCP Server（无真实网络），覆盖注册 / 调用 / 超时 / 错误泛化 / 重名 409 /
`enabled=False` 主流程可用。新增外部工具接入后请回归该文件。

---

## 4. 扩展边界（不要做什么）

- 内部 File / RAG / Export 仍使用内部实现，**不通过 MCP 调用**；
- Tool `execute` 必须保持纯确定性，不得调用 LLM / 访问网络；
- LLM 输出必须过结构化 Pydantic v2 校验后才能写入 Artifact（Artifact 不可变，修订产生新版本）；
- 测试一律用 FakeLLM，禁止真实 LLM 调用。
