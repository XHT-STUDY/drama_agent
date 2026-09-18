# DramaAgent MCP 开发计划

> 状态：已批准，待实施  
> 日期：2026-09-18  
> 设计依据：[MCP_DESIGN.md](MCP_DESIGN.md)  
> 关联任务：I-04 MCP Adapter 与 Skill 插件契约

## 1. 交付范围

本计划把当前 MCP 风格 JSON-RPC Adapter 改造成标准、Tools-only 的 MCP Host/Client，并接入现有对话 Agent。系统使用官方 Python SDK 完成协议协商、工具发现和调用，业务层负责 allowlist、确认、Schema 校验、审计和外部内容隔离。

预计改动 15 至 25 个代码、测试和文档文件，涉及 core、integrations、tools、domain、application、workflow、observability、frontend 和 docs。实施时一次只推进一张任务卡，每张任务卡都能独立合并和回滚。

本轮不新增数据库表、任务队列或外部常驻服务。自动化测试使用官方 in-process MCP Server 和 FakeLLM，不访问真实互联网。

## 2. 顺序与依赖

```text
MCP-01 官方 SDK 与 ClientManager
        │
        ▼
MCP-02 Tool catalog 与内部执行契约
        │
        ▼
MCP-03 AgentAction / WorkflowRun 接线
        │
        ▼
MCP-04 安全、可观测与发布验收
```

MCP-01 提供可复用的标准 Client；MCP-02 让应用代码通过现有 ToolRegistry 使用标准 MCP Tool；MCP-03 提供用户可见的对话调用路径；MCP-04 完成生产门禁。每个阶段合并后系统都保持可运行，MCP 默认关闭。

总预计为 6 至 9 人日。

## 3. MCP-01：官方 SDK 与 ClientManager

**目标**：用官方 SDK 替换手写 MCP 网络协议，建立多 Server 生命周期和标准工具发现。

**预计**：2 至 3 人日。

**新增文件**：

- `backend/app/integrations/mcp/client.py`
- `backend/app/integrations/mcp/manager.py`
- `backend/app/integrations/mcp/security.py`
- `backend/tests/contract/test_mcp_official_client.py`
- `backend/tests/unit/integrations/mcp/test_config.py`
- `backend/tests/integration/test_mcp_lifecycle.py`

**修改文件**：

- `backend/pyproject.toml`
- `backend/uv.lock`
- `backend/app/core/config.py`
- `backend/app/main.py`
- `backend/app/api/dependencies.py`
- `backend/app/integrations/mcp/protocol.py`
- `backend/app/integrations/mcp/__init__.py`
- `.env.example`

**实现要求**：

1. 增加生产依赖 `mcp>=2,<3`，由 lock 文件锁定具体版本。
2. 在 `protocol.py` 定义 `MCPServerConfig`、`MCPServerStatus` 和 catalog 所需模型，全部使用 Pydantic v2 且 `extra=forbid`。
3. `MCP_SERVERS_JSON` 解析为多个 Server。Server ID 必须唯一并符合小写字母、数字和下划线规则。
4. `MCP_ENABLED=false` 时不创建 ClientManager，不连接网络，不注册工具。
5. `MCP_SERVERS_JSON` 缺失时，将旧 `MCP_BASE_URL` 和 `MCP_TIMEOUT_SECONDS` 映射成 `id=default` 的兼容配置，并记录一次弃用警告。
6. 生产环境 URL 默认要求 HTTPS。loopback、link-local 和私网地址只有 `allow_private_network=true` 才允许。
7. Bearer Token 通过 `bearer_token_env` 引用环境变量。配置对象、日志和异常不得包含 Token 值。
8. `MCPClientManager` 使用官方 SDK Client，进入 FastAPI lifespan 后连接，退出 lifespan 时关闭。
9. 每个 Server 分别记录协议版本、Server 信息、capabilities、连接状态和最近错误分类。
10. 启动连接或发现失败只把 Server 标为 degraded，不中断 FastAPI startup。
11. 生产只启用 Streamable HTTP。测试用官方 in-process Server，不启用旧 SSE。
12. `tools/list` 遵守 SDK 解析的缓存信息。刷新失败时 catalog 标为 stale。

**错误语义**：

| 场景 | 错误码 |
| --- | --- |
| Server 配置非法 | `MCP_CONFIG_INVALID` |
| Server 不可达 | `MCP_SERVER_UNAVAILABLE` |
| 协议协商失败 | `MCP_PROTOCOL_ERROR` |
| 发现超时 | `MCP_DISCOVERY_TIMEOUT` |

这些错误只用于诊断和后续调用。应用启动不因可选 Server 失败而失败。

**验收**：

- 官方 in-process Server 可以完成协议协商和 `tools/list`。
- 测试能读取协商后的协议版本和 capabilities。
- 两个 Server 可以同时连接，单个失败不影响另一个。
- 关闭 MCP 后测试断言没有 Client 被创建。
- 私网、跨源重定向和缺失 Token 配置有确定性测试。
- 旧单 Server 配置仍可加载，并产生弃用警告。

**验证命令**：

```bash
cd backend
uv run pytest tests/contract/test_mcp_official_client.py tests/unit/integrations/mcp/test_config.py tests/integration/test_mcp_lifecycle.py
uv run ruff check app tests
uv run mypy app
```

**回滚**：恢复旧依赖和 lifespan 接线，保留原 `MCP_ENABLED=false` 默认值。没有数据库或外部状态需要迁移。

## 4. MCP-02：Tool catalog 与内部执行契约

**目标**：把官方 `tools/list` 结果安全地映射为内部 Tool，并提供可验证的程序化调用能力。

**预计**：1.5 至 2 人日。

**新增文件**：

- `backend/app/integrations/mcp/catalog.py`
- `backend/app/integrations/mcp/execution.py`
- `backend/tests/unit/integrations/mcp/test_catalog.py`
- `backend/tests/unit/integrations/mcp/test_execution.py`

**修改文件**：

- `backend/app/integrations/mcp/adapter.py`
- `backend/app/tools/protocol.py`
- `backend/app/tools/registry.py`
- `backend/app/agents/base.py`
- `backend/app/core/errors.py`
- `backend/tests/contract/test_mcp_adapter.py`
- `backend/tests/unit/registries/test_tools.py`

**实现要求**：

1. Tool 抽象区分 `local` 和 `external` execution kind。现有内部 Tool 行为不变。
2. 外部 Tool 内部 ID 固定为 `mcp__{server_id}__{tool_name}`。Tool 名不做静默改写，无法形成合法内部 ID 时拒绝注册。
3. catalog 按 Server 原子替换，工具定义保存稳定 digest。digest 至少覆盖名称、输入 Schema、输出 Schema 和 annotations。
4. `allowed_tools` 在注册前过滤。空列表表示发现可见但不可调用，不注册到 ToolRegistry。
5. `MCPRemoteTool` 只委托 `MCPClientManager`，不自行创建 HTTP Client。
6. 新增 `MCPToolCallCommand` 和 `MCPToolResult` 的应用层模型。此任务先提供程序化执行，不修改 AgentIntent。
7. 调用前使用完整 JSON Schema 2020-12 校验参数。参数不合法时不发远程请求。
8. Tool 返回 structured content 且声明 `outputSchema` 时必须校验。失败返回 `MCP_TOOL_RESULT_INVALID`。
9. 保存 text、structured content、resource link 和内容类型。图片、音频和 embedded resource 不送入 LLM。
10. 单次结果上限为 256 KiB。超出后按内容块边界截断，设置 `truncated=true`。
11. `tools/call` 默认不自动重试。429、5xx、超时和连接中断都返回当前调用失败。
12. Server 或工具定义在计划后变化时，Execution Service 返回 `MCP_TOOL_STALE`。

**错误语义**：

| 场景 | HTTP 映射 | 错误码 |
| --- | --- | --- |
| Tool 不存在或不允许 | 404 | `MCP_TOOL_NOT_AVAILABLE` |
| Tool 定义已变化 | 409 | `MCP_TOOL_STALE` |
| 输入 Schema 不合法 | 422 | `MCP_TOOL_ARGUMENT_INVALID` |
| 调用超时 | 504 | `MCP_TOOL_TIMEOUT` |
| 协议或传输失败 | 502 | `MCP_PROTOCOL_ERROR` |
| Tool 返回错误 | 502 | `MCP_TOOL_ERROR` |
| 输出 Schema 不合法 | 502 | `MCP_TOOL_RESULT_INVALID` |

所有用户可见 detail 保持泛化，日志使用异常分类，不写入 Token、完整参数和完整返回体。

**验收**：

- 两个 Server 的同名 Tool 可以同时注册和调用。
- allowlist 外 Tool 不进入 ToolRegistry。
- 输入校验失败时 Fake Server 的调用计数保持 0。
- structured content 通过 output schema 后返回，非法输出被拒绝。
- text、structured content、resource link 和 `isError` 均有契约测试。
- 超大结果按边界截断，二进制内容不进入 LLM 可消费字段。
- 同一次非幂等调用遇到 500 只执行一次。
- 现有本地 Tool 测试行为不变。

**验证命令**：

```bash
cd backend
uv run pytest tests/unit/integrations/mcp tests/contract/test_mcp_adapter.py tests/unit/registries/test_tools.py
uv run ruff check app tests
uv run mypy app
```

**回滚**：关闭 MCP 后外部 Tool 不注册。内部 Tool 的新增元数据字段保留默认值，不影响旧调用方。

## 5. MCP-03：接入 AgentAction 与 WorkflowRun

**目标**：用户可以通过现有对话工作台提出 MCP Tool 调用，确认后执行并看到结果。

**预计**：2 至 3 人日。

**新增文件**：

- `backend/app/workflows/mcp_tool_call.py`
- `backend/tests/workflow/test_mcp_tool_call.py`
- `backend/tests/integration/api/test_agent_mcp_tools.py`
- `frontend/src/features/agent/McpToolActionCard.tsx`
- `frontend/src/features/agent/McpToolResult.tsx`

**修改文件**：

- `backend/app/domain/agent_command.py`
- `backend/app/domain/agent_planner.py`
- `backend/app/skills/agent_command_planner.py`
- `backend/app/application/agent_command_service.py`
- `backend/app/application/agent_action_lifecycle.py`
- `backend/app/application/workflow_dispatcher.py`
- `backend/app/api/dependencies.py`
- `backend/app/prompts/templates/agent_command_planner.md`
- `backend/app/prompts/manifest.yaml`
- `backend/tests/unit/skills/test_agent_command_planner.py`
- `frontend/src/types/api.ts`
- `frontend/src/features/agent/ActionPlanCard.tsx`

**领域变化**：

- `AgentIntent` 增加 `use_external_tool`。
- `ActionTarget.target_type` 增加 `external_tool`。
- `AgentCommand` 增加 `MCPToolCallCommand`。
- WorkflowRun action 增加 `mcp_tool_call`。
- `AgentActionPlan` 和现有表结构继续使用，不新增 migration。

`MCPToolCallCommand` 固定包含 `server_id`、`tool_name`、`qualified_tool_name`、`arguments`、`tool_definition_digest` 和 `purpose`。模型不提供 URL、认证信息、Run ID 或任意执行器名称。

**实现要求**：

1. `available_intents` 只有在至少一个 MCP Tool 为 available 时才包含 `use_external_tool`。
2. Planner 上下文只注入有界 Tool catalog。按描述与用户请求匹配后最多提供 20 个 Tool，整个 catalog 受 Agent 上下文预算约束。
3. Tool 描述和 Schema description 使用现有非可信内容边界，不拼入 system 指令。
4. `_scan_strings` 保留对 URL、SQL、Artifact ID 和未知 API 的拒绝，只允许已知 qualified Tool ID 出现在结构化字段中。
5. 服务端重新验证 Tool ID、allowlist、Schema 和 definition digest，不信任 Planner 返回的描述和风险声明。
6. 每个 MCP 调用创建 `AgentAction(status=proposed, requires_confirmation=true)`。
7. 确认时创建 `WorkflowRun(action=mcp_tool_call)`，幂等键继续使用 `agent-action:{action_id}`。
8. Worker 通过 `MCPExecutionService` 调用一个 Tool。一个 Action 不得包含多个 Tool 调用或 Tool 循环。
9. 用户取消时取消 SDK 调用，并让 Run 和 Action 进入 cancelled。
10. Tool 结果写成 `action_result` 消息。MCP 调用不创建 Artifact，不改变采用工作集。
11. text 和 structured content 进入后续 LLM 上下文时标为外部非可信内容，并受 Token 预算限制。
12. Tool 在规划后变化或消失时，Action 转为 stale，用户需要重新发起。
13. 项目单活动 Run 规则继续生效。

**前端要求**：

- 计划卡展示 Server ID、Tool 显示名、用途、参数摘要和风险提示。
- 参数值默认折叠，疑似密钥字段完全隐藏。
- 确认和拒绝复用现有按钮与状态。
- 结果组件区分 text、structured content、resource link、truncated 和 error。
- 图片、音频和 embedded resource 显示为暂不支持的内容类型，不自动下载。

**验收**：

- 明确的工具请求生成 `use_external_tool` Action，不直接发网络请求。
- 用户拒绝时 Fake Server 调用计数为 0。
- 重复确认只产生一个 WorkflowRun 和一次 Tool 调用。
- Planner 输出未知 Tool、URL 或非法参数时不创建 Action。
- Tool 定义在确认前变化时 Action 进入 stale。
- 成功、失败、取消和超时通过 SSE 和 Action 查询可见。
- Tool 返回的 Prompt Injection 文本只作为内容，不改变后续控制流。
- MCP Action 与创作 Run 的单活跃约束有集成测试。

**验证命令**：

```bash
cd backend
uv run pytest tests/workflow/test_mcp_tool_call.py tests/integration/api/test_agent_mcp_tools.py tests/unit/skills/test_agent_command_planner.py
cd ../frontend
pnpm test
pnpm lint
pnpm typecheck
```

**回滚**：从 `available_intents` 移除 `use_external_tool` 并关闭 `MCP_ENABLED`。已完成 Action 和 Run 保持可读，未确认 Action 按 unsupported intent 返回重新规划提示。

## 6. MCP-04：安全、可观测与发布验收

**目标**：完成生产安全门禁、运行诊断、文档和端到端验收。

**预计**：1 至 1.5 人日。

**新增文件**：

- `backend/tests/security/test_mcp_security.py`
- `backend/tests/performance/test_mcp_catalog.py`
- `docs/MCP_TEST_REPORT.md`

**修改文件**：

- `backend/app/observability/metrics.py`
- `backend/app/integrations/mcp/manager.py`
- `backend/app/integrations/mcp/execution.py`
- `backend/app/core/logging.py`
- `backend/app/core/security.py`
- `backend/tests/integration/api/test_metrics_endpoint.py`
- `backend/tests/unit/observability/test_log_redaction.py`
- `docs/EXTENSIONS.md`
- `docs/API_CONTRACT.md`
- `docs/DEV_PLAN.md`
- `docs/DEV_LOG.md`
- `docs/TEST_PLAN.md`
- `docs/TEST_REPORT.md`
- `README.md`
- `.env.example`

**实现要求**：

1. 增加 `mcp_server_status`、`mcp_discovery_total`、`mcp_call_total` 和 `mcp_call_duration_seconds`。
2. Prometheus label 只使用低基数 `server_id` 和 `status`，不使用 Tool 名、项目 ID、Action ID 或 Run ID。
3. 结构化日志包含 Server ID、Tool 名、协议版本、Action ID、Run ID、耗时和输出大小。
4. 参数只记录字段名和脱敏摘要，结果只记录类型、大小和状态。
5. 安全测试覆盖 DNS 解析后的私网地址、loopback、link-local、跨源重定向、缺失 Token、Token 脱敏和超大响应。
6. catalog 在 1000 个工具定义下完成解析、过滤和 digest 计算，p95 小于 300 ms，不包含网络时间。
7. MCP Server 故障不影响现有创建、评估、修订和导出 E2E。
8. 使用官方本地 MCP Server 完成人工 smoke，记录 SDK 版本、协议版本、Server 名、工具名、调用结果和清理方式。不得使用付费账号或生产 Token。
9. `EXTENSIONS.md` 删除旧手写请求格式，改为标准 Server 配置、Tool 发现和安全边界说明。
10. `DEV_PLAN.md` 增加本计划任务卡和进度项。每张任务卡只有验收全部完成后才能标记 DONE。

**验收**：

- 安全测试覆盖全部网络和内容边界。
- 指标没有高基数标签，Token 和完整参数不出现在日志快照。
- MCP 关闭、单 Server 故障和全部 Server 故障三种场景均能完成核心创作路径。
- Fake MCP 全量测试稳定通过，人工 smoke 结果写入 `MCP_TEST_REPORT.md`。
- README、配置样例、API 契约、扩展文档和测试报告描述一致。

**验证命令**：

```bash
cd backend
uv run pytest tests/security/test_mcp_security.py tests/performance/test_mcp_catalog.py tests/integration/api/test_metrics_endpoint.py tests/unit/observability/test_log_redaction.py
uv run pytest
uv run ruff check app tests
uv run mypy app
cd ../frontend
pnpm test
pnpm lint
pnpm typecheck
cd ..
make cov
```

真实网络 smoke 不进入普通 CI，只在本地标准 MCP Server 启动后执行并记录结果。

**回滚**：关闭 MCP 后停止 ClientManager、工具注册和相关指标更新。监控指标可以保留为零值，文档标记功能停用。无生产数据迁移。

## 7. 测试矩阵

| 层级 | 覆盖内容 | 外部依赖 |
| --- | --- | --- |
| 单元 | 配置、URL 安全、catalog、digest、Schema、结果规范化 | 无 |
| 契约 | 官方 Client 与 in-process Server 的发现和调用 | 官方 SDK in-process |
| 集成 | FastAPI lifespan、ToolRegistry、Agent Action、Run、SSE | PostgreSQL、Redis、FakeLLM |
| 工作流 | 成功、失败、超时、取消、stale、恢复 | Fake MCP Server |
| 安全 | SSRF、Prompt Injection、脱敏、输出大小、未知 Tool | 无真实网络 |
| 性能 | 1000 Tool catalog 处理和有界 Planner catalog | 无真实网络 |
| 人工 smoke | 本地 Streamable HTTP Server 互操作 | 本地标准 MCP Server |

所有自动化测试必须断言：没有真实互联网请求、没有真实 LLM 调用、没有 Token 输出。

## 8. 发布门禁

- `MCP_ENABLED=false` 时全量回归与改造前一致。
- 官方 SDK 版本和 lock 文件已提交。
- 协议兼容由官方 SDK 和契约测试证明，不再维护自定义 wire format 测试。
- 所有 MCP 调用都经过 AgentAction 确认和 WorkflowRun。
- 所有 Tool 输入和结构化输出都有 Schema 校验。
- 非幂等调用没有自动重试。
- 外部结果不能直接写 Artifact 或触发后续 Tool。
- Server 故障不影响应用启动和核心工作流。
- 文档、配置样例、API 类型和前端展示同步。
- DEV_PLAN 进度证据、DEV_LOG 和 MCP_TEST_REPORT 已更新。

## 9. 凭证与外部依赖

开发和自动化验收只需要官方 `mcp` Python SDK，不需要账号、API Key 或付费服务。人工 smoke 使用本地标准 MCP Server，也不需要凭证。

接入真实远程 Server 时，由部署方提供：

- 最终 MCP endpoint URL；
- Server ID；
- 允许调用的 Tool 白名单；
- Bearer Token 环境变量名和 Token 值；
- 是否允许私网访问。

Token 只保存在部署环境，不写入仓库、数据库、Action、Run 或测试报告。

## 10. 风险与应对

| 风险 | 应对 |
| --- | --- |
| SDK 协议升级产生破坏性变化 | 依赖锁定 `>=2,<3`，升级前运行契约测试 |
| Server 不可用拖垮应用 | 可选依赖、独立状态、并发和超时限制 |
| Tool 定义在规划后变化 | definition digest，确认和执行时二次校验 |
| 非幂等 Tool 重复执行 | `tools/call` 默认不重试，确认接口幂等 |
| Tool 描述或结果注入 Prompt | 内容边界、单 Tool Action、禁止自动链式调用 |
| Tool catalog 过大 | allowlist、最多 20 个候选、上下文预算 |
| SSRF 和 Token 泄漏 | 管理员配置、网络目标校验、环境变量引用、集中脱敏 |
| 指标基数失控 | Tool 名和业务 ID 不进入 metric label |

## 11. 完成定义

MCP-01 至 MCP-04 全部达到验收条件，才能把项目能力描述更新为“支持 MCP Tool”。若只完成 MCP-01 和 MCP-02，文档和发布说明必须使用“标准 MCP Client 基础”，不能宣称 Agent 已支持 MCP。

实施完成后，按仓库规则更新 `DEV_PLAN.md` 进度表、`DEV_LOG.md`、相关 API 和测试文档。若过程中解决协议兼容、安全或恢复问题，再同步 `TROUBLESHOOTING.md`。
