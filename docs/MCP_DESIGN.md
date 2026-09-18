# DramaAgent MCP 能力设计

> 状态：已批准，待实施  
> 日期：2026-09-18  
> 适用范围：外部 MCP Tool 发现、确认、调用、结果处理与运行诊断

## 1. 结论

DramaAgent 将建设为 Tools-only 的 MCP Host/Client。系统可以配置可信的远程 MCP Server，使用官方协议发现和调用 Tool，并把 Tool 接入现有 Agent 的规划、确认、Run、SSE 和结果回写链路。MCP 是可选扩展，任何 MCP Server 故障都不能阻止应用启动，也不能让核心创作链路失效。

本轮不把 DramaAgent 建设为 MCP Server。向 Claude Desktop、Codex 或其他 MCP Host 暴露 DramaAgent 的创作能力，属于另一项产品需求，需要单独设计授权、长任务和资源边界。

## 2. 文档关系

- [DEV_PLAN.md](DEV_PLAN.md) 仍是项目开发契约和状态记录的权威来源。
- [EXTENSIONS.md](EXTENSIONS.md) 记录当前 I-04 Adapter/Registry 契约。本设计实施后，该文档同步更新为标准 MCP 接入说明。
- [MCP_IMPLEMENTATION_PLAN.md](MCP_IMPLEMENTATION_PLAN.md) 给出任务顺序、文件范围、验收和回滚方式。
- [DESIGN.md](../DESIGN.md) 中“聊天是控制面，不是内容事实源”“Planner 只产出选择器，不产出执行权限”“写动作先确认”等约束继续成立。

若文档冲突，优先级为：`DEV_PLAN.md`、本设计、MCP 开发计划、`EXTENSIONS.md`、历史开发日志。当前代码和测试结果优先于历史描述。

## 3. “具备 MCP 能力”的定义

MCP 同时包含 Host、Client 和 Server 三种角色。DramaAgent 本轮承担前两种角色：

- Host 负责管理 MCP Server、工具目录、权限、用户确认、审计和外部内容边界。
- Client 负责协议协商、能力发现、标准传输和远程调用。
- Server 向 Host 暴露 Tool、Resource 或 Prompt。本轮由外部服务承担，DramaAgent 不提供 Server 能力。

本设计完成后，DramaAgent 的 MCP 能力以以下条件为准：

1. 使用官方 Python SDK 与标准 MCP Server 通信。
2. 支持当前协议版本和 SDK 提供的旧版本兼容。
3. 通过 `tools/list` 自动发现工具，通过 `tools/call` 调用工具。
4. 支持多个配置化 Server，并隔离同名 Tool。
5. Agent 只能选择服务端认可且管理员允许的 Tool。
6. 所有 MCP Tool 调用都先生成 AgentAction，用户确认后进入 WorkflowRun。
7. 输入、输出、权限、超时、取消、审计和外部内容边界都有明确语义。
8. MCP 关闭或不可用时，非 MCP 功能保持可用。

只完成协议客户端但未接入 Agent 时，能力描述为“标准 MCP Client 基础”。完成上述八项后，才对外描述为“DramaAgent 支持 MCP Tool”。

## 4. 当前实现快照

| 能力 | 当前实现 | 已有价值 | 主要缺口 |
| --- | --- | --- | --- |
| 外部调用 | `MCPToolAdapter` 手写 HTTP JSON-RPC | 已有超时、错误泛化和重试测试 | 请求格式不是标准 `tools/call`，没有协议协商 |
| 工具描述 | 调用方手工构造 `MCPToolSpec` | 能映射输入输出 Schema | 没有 `tools/list`，存在双重事实源 |
| 注册 | `register_mcp_tools` 注册到 `ToolRegistry` | 已有前缀和重名策略 | 未接应用启动、依赖注入和工作流 |
| 配置 | 单个 `MCP_BASE_URL` 和超时 | 默认关闭，不影响主流程 | 配置未被消费，只支持单 Server |
| Agent 调用 | `BaseAgent.call_tool` 可执行已注册 Tool | 内外工具可复用一个入口 | Planner 禁止输出工具信息，没有 MCP 意图 |
| 安全 | 外部错误不泄漏连接信息 | 18 个契约测试通过 | 无 allowlist、审批策略、SSRF 防护和结果注入边界 |
| 运行管理 | 无 | 无新增基础设施 | 无生命周期、健康状态、取消、审计和指标 |

当前实现是经过测试的 MCP 风格 JSON-RPC 适配器，不是完整 MCP Client。实施时保留它的内部 Tool 映射和错误语义，底层协议交给官方 SDK。

## 5. 目标与非目标

### 5.1 目标

- 使用官方 `mcp` Python SDK v2，锁定 `mcp>=2,<3`。
- 生产传输只使用 Streamable HTTP；测试使用官方 in-process Server。
- 自动协商协议、发现 Server capabilities 并维护 Tool catalog。
- 支持多个管理员配置的 Server，工具内部名为 `mcp__{server_id}__{tool_name}`。
- 输入在远程调用前通过 JSON Schema 校验，结构化输出按 `outputSchema` 校验。
- 所有 MCP Tool 调用复用现有 AgentAction、WorkflowRun、SSE、幂等、取消和结果回写。
- 外部工具描述和结果按不可信内容处理，不得直接改变 Artifact 或控制流。
- MCP Server 故障局部降级，应用启动和核心创作不受影响。
- 调用可观测、可审计，认证信息和敏感内容不进入日志。

### 5.2 非目标

- 不建设 DramaAgent MCP Server。
- 不支持 Resources、Prompts、MCP Apps、Tasks 和自主多工具循环。
- 不接入旧 SSE 传输，不建设新的 stdio Server 安装管理。
- 不支持普通用户提交任意 MCP URL。
- 不实现交互式 OAuth 授权、回调和 Token 持久化。
- 不允许 MCP Tool 直接创建、更新或废弃 Artifact。
- 不根据未经信任的 Tool annotation 自动跳过用户确认。
- 不新增数据库表、任务队列或常驻外部服务。

## 6. 设计原则

### 6.1 官方 SDK 负责协议

协议版本、生命周期、Streamable HTTP、标准请求头和返回内容类型由官方 SDK 处理。项目代码只负责 Server 配置、内部 Tool 映射、策略、审计和业务接线。SDK 的协议升级通过依赖更新和契约测试进入项目，不在业务代码中复制协议实现。

### 6.2 MCP 是可选依赖

MCP Server 不进入应用 readiness 硬门禁。启动时连接或发现失败只把该 Server 标为 `degraded`，其工具不进入可调用目录。其他 Server 和核心创作继续工作。

### 6.3 模型没有执行权限

Planner 只能从服务端下发的工具目录中选择工具并提出参数。服务端重新检查 Server 状态、allowlist、Schema、项目状态和确认结果。模型输出中的 URL、认证信息、未知工具名和任意 API 描述一律拒绝。

### 6.4 所有外部调用先确认

首版不区分只读和写 Tool，全部生成 `AgentAction(status=proposed)`。Tool annotation 只用于风险提示，不作为跳过确认的依据。管理员以后可以针对可信 Server 增加只读免确认策略，但不属于本轮范围。

### 6.5 外部结果默认不可信

Tool 描述、Schema description、文本结果和资源内容都可能包含 Prompt Injection。它们进入 Planner 或 LLM 上下文时必须使用现有内容边界，并受字符数、Token 和内容类型限制。Tool 结果不能自动触发第二个 Tool。

### 6.6 非幂等调用不自动重试

Server 发现和 `tools/list` 可以退避重试。`tools/call` 默认不自动重试，避免响应丢失后重复执行外部写操作。`idempotentHint` 是不可信提示，不能单独开启自动重试。用户手动重试产生新的审计记录。

## 7. 目标架构

```text
用户
 │
 ▼
AgentCommandPlannerSkill
 │  只选择服务端提供的 MCP Tool ID
 ▼
AgentCommandService
 │  allowlist + Schema + 项目状态校验
 ▼
AgentAction(proposed)
 │
 │ 用户确认
 ▼
WorkflowRun(action=mcp_tool_call)
 │
 ▼
WorkflowDispatcher
 │  取消、超时、幂等、SSE
 ▼
MCPExecutionService
 ├── 调用前策略检查
 ├── 输入 Schema 校验
 ├── 结果大小和类型限制
 ├── 输出 Schema 校验
 └── 审计与指标
 │
 ▼
MCPClientManager
 ├── Server client: research
 ├── Server client: assets
 └── Server status + Tool catalog
 │
 ▼
标准 MCP Server
```

没有数据流回到 Planner 或 ClientManager，因此组件关系不形成执行环。

## 8. 核心组件

### 8.1 MCPServerConfig

全局新增 `MCP_SERVERS_JSON`。每个 Server 配置包含：

| 字段 | 规则 |
| --- | --- |
| `id` | 小写字母、数字和下划线，进程内唯一 |
| `url` | Streamable HTTP 端点，生产默认只接受 HTTPS |
| `enabled` | 是否加载该 Server |
| `allowed_tools` | 显式 Tool 白名单；空列表表示不允许调用 |
| `bearer_token_env` | Bearer Token 所在环境变量名，不保存 Token 值 |
| `timeout_seconds` | 单次调用总超时，默认 30 秒 |
| `max_concurrency` | 每个 Server 的并发上限，默认 4 |
| `allow_private_network` | 是否允许 loopback 和私网目标，默认 false |

现有 `MCP_ENABLED` 保留。`MCP_SERVERS_JSON` 缺失时，旧 `MCP_BASE_URL` 和 `MCP_TIMEOUT_SECONDS` 映射为 `id=default` 的单 Server，并记录弃用警告。兼容入口保留一个发布周期。

### 8.2 MCPClientManager

Manager 由 FastAPI lifespan 创建并挂到 `app.state`，负责：

- 创建、持有和关闭官方 SDK Client。
- 记录协商后的协议版本、Server 信息和 capabilities。
- 启动时执行工具发现。
- 根据服务端缓存提示刷新 Tool catalog。
- 对每个 Server 实施并发限制。
- 提供 `get_tool`、`list_tools`、`call_tool` 和 `server_status`。
- 连接失败时进入 `degraded`，恢复后刷新 catalog。

Manager 不保存业务状态，不读写数据库，也不决定用户是否有权限调用。

### 8.3 MCPToolCatalog

Catalog 保存最近一次验证通过的工具定义。工具内部 ID 使用 `mcp__{server_id}__{tool_name}`，原始名称继续用于官方 SDK 调用。

工具状态分为：

- `available`：Server 正常，工具仍存在且命中 allowlist。
- `stale`：刷新失败，定义来自旧 catalog，不允许发起新调用。
- `unavailable`：Server 下线、工具被移除或未命中 allowlist。

Catalog 更新采用整 Server 原子替换。刷新过程中旧快照继续供已有 Action 做过期判断，新规划只读取最新 `available` 快照。

### 8.4 MCPRemoteTool

内部 Tool 抽象拆成 `LocalDeterministicTool` 和 `ExternalTool` 两类，`MCPRemoteTool` 属于后者。公共元数据增加：

- `execution_kind`；
- `source_id`；
- `requires_confirmation`；
- `network_access`；
- MCP annotation 的只读、破坏性、幂等和开放世界提示。

`MCPRemoteTool.execute` 不直接管理网络连接，只调用注入的 `MCPClientManager`。

### 8.5 MCPExecutionService

Execution Service 是业务安全边界。它接收服务端持久化的 `MCPToolCallCommand`，执行：

1. 检查 Server 和 Tool 仍为 `available`。
2. 比较计划时保存的 Tool definition digest，变化时把 Action 标为 stale。
3. 检查 `allowed_tools`。
4. 校验输入 Schema。
5. 通过 Manager 调用 Tool。
6. 规范化内容类型并限制结果大小。
7. 校验 structured content 的 `outputSchema`。
8. 返回可持久化、可展示的 `MCPToolResult`。

### 8.6 Agent 与 Run 接线

领域层新增 `use_external_tool` intent、`MCPToolCallCommand` 和 `external_tool` target。Command 保存：

- `server_id`；
- `tool_name`；
- `qualified_tool_name`；
- `arguments`；
- `tool_definition_digest`；
- `purpose`。

确认后创建 `WorkflowRun(action=mcp_tool_call)`。Run 的 `config_snapshot` 保存 Command 和 `agent_action_id`，Worker 调用 Execution Service。结果通过现有 AgentActionLifecycle 写成 `action_result` 消息。MCP 调用不创建 Artifact，也不修改项目采用工作集。

项目单活动 Run 约束继续生效，避免 MCP 调用与创作、修订同时改变用户看到的执行状态。

### 8.7 MCPToolResult

结果模型至少包含：

- `server_id`；
- `tool_name`；
- `content`；
- `structured_content`；
- `resource_links`；
- `is_error`；
- `duration_ms`；
- `protocol_version`；
- `request_id`；
- `truncated`。

Agent 首版只把 text 和 structured content 放入受限上下文。图片、音频、embedded resource 和 resource link 保留在结果中供 UI 展示，不自动送入 LLM。

## 9. 关键数据流

### 9.1 启动与发现

```text
FastAPI lifespan
  -> 解析 MCP Server 配置
  -> 校验 URL 与认证引用
  -> 创建 MCPClientManager
  -> 逐 Server 连接和协议协商
  -> tools/list
  -> allowlist 过滤
  -> 生成 definition digest
  -> 原子发布 Tool catalog
  -> 注册 MCPRemoteTool
```

任一 Server 失败只记录其状态，不中断 startup。

### 9.2 规划与确认

```text
用户请求
  -> Planner 接收有界 Tool catalog
  -> 输出 use_external_tool + Tool ID + 参数
  -> 服务端验证 Tool、参数和目的
  -> 持久化 AgentAction(proposed)
  -> 前端展示 Server、Tool、参数和风险
  -> 用户确认或拒绝
```

Tool catalog 只包含名称、可信来源、描述、输入 Schema 和风险提示，不包含 URL、Token 或 Server instructions。

### 9.3 执行与结果回写

```text
确认 Action
  -> 创建幂等 WorkflowRun
  -> Worker 领取 mcp_tool_call
  -> 二次校验 definition digest
  -> 调用 MCP Tool
  -> 规范化和校验结果
  -> Run 进入 completed / failed / cancelled
  -> AgentActionLifecycle 回写 Outcome 和消息
```

重复确认复用同一个 Run。用户手动重试 failed Run 时生成新的外部调用审计记录，但继续遵守现有 Run 重试约束。

## 10. 失败、取消与恢复

| 场景 | 系统行为 |
| --- | --- |
| 启动时 Server 不可达 | Server 标为 degraded，应用继续启动 |
| `tools/list` 失败 | catalog 标为 stale，不接受新规划 |
| Tool 在确认前消失或定义变化 | Action 转为 stale，要求重新规划 |
| 输入 Schema 不合法 | 调用前失败，错误码 `MCP_TOOL_ARGUMENT_INVALID` |
| 远程超时 | Run 失败，错误码 `MCP_TOOL_TIMEOUT` |
| MCP 协议错误 | Run 失败，错误码 `MCP_PROTOCOL_ERROR` |
| Tool 返回 `isError=true` | Run 失败，保留经过脱敏的工具错误摘要 |
| 输出 Schema 不合法 | Run 失败，错误码 `MCP_TOOL_RESULT_INVALID` |
| 用户取消 | 取消在途 SDK 调用，Run 和 Action 进入 cancelled |
| Worker 崩溃 | 复用现有 Run 恢复和 AgentAction reconciliation |

`tools/call` 默认不自动重试。发现和 catalog 刷新可以复用 I-01 退避策略。

## 11. 安全边界

### 11.1 Server 信任

- Server 只能由部署配置添加，用户和模型不能提供 URL。
- 生产默认只允许 HTTPS。
- 默认拒绝 loopback、link-local、私网 IP 和跨源重定向。
- 本地开发只有显式 `allow_private_network=true` 才能连接本机 Server。
- Bearer Token 从环境变量读取，不进入配置快照、日志或错误响应。

### 11.2 Tool 权限

- `allowed_tools` 是硬白名单。
- Tool annotation 是展示和策略输入，不是可信授权声明。
- 所有 Tool 调用都需用户确认。
- 一次 AgentAction 只允许一个 Tool 调用。
- Tool 结果不能直接产生另一个 Tool 调用。

### 11.3 内容安全

- Tool 描述、Schema 文本和结果使用现有 Prompt 内容边界。
- 单次结果默认最多 256 KiB，超出后截断并标记 `truncated=true`。
- 送入 LLM 的外部文本受 `agent_context_budget_tokens` 约束。
- 二进制内容不直接进入 Prompt。
- 不从 Tool 输出中提取 URL 后自动访问。
- 不把 Tool 输出直接写入 Artifact。

## 12. 可观测性与审计

日志记录 Server ID、Tool 名、Action ID、Run ID、协议版本、耗时、结果状态和输出大小。参数只记录字段名和脱敏摘要，结果只记录类型和大小。

新增低基数指标：

- `mcp_server_status{server_id}`；
- `mcp_discovery_total{server_id,status}`；
- `mcp_call_total{server_id,status}`；
- `mcp_call_duration_seconds{server_id}`。

Tool 名不进入 Prometheus label，避免外部 Server 动态工具导致高基数。

## 13. 公开契约变化

### 13.1 配置

新增：

- `MCP_SERVERS_JSON`。
- 每个 Server 引用的 Bearer Token 环境变量。

保留一个发布周期后弃用：

- `MCP_BASE_URL`。
- `MCP_TIMEOUT_SECONDS`。

`MCP_ENABLED=false` 继续表示完全不加载 MCP。

### 13.2 领域和 Run

- `AgentIntent` 增加 `use_external_tool`。
- `AgentCommand` 增加 `MCPToolCallCommand`。
- `ActionTarget.target_type` 增加 `external_tool`。
- WorkflowRun action 增加 `mcp_tool_call`。
- AgentAction 和 WorkflowRun 表不增加字段，不需要 Alembic migration。

### 13.3 API 与前端

继续使用现有 Agent Turn、Action confirm/reject、Run、SSE 和消息 API。AgentAction 响应中的计划会包含 Tool 的可展示名称、Server ID、参数摘要和风险提示。前端只需新增对应计划卡片和结果内容渲染，不新增 MCP 管理端点。

## 14. 被拒绝的方案

### 14.1 继续手写 MCP 协议

拒绝。协议已经包含版本协商、两代生命周期、标准传输、内容类型和授权约束。继续维护手写客户端会让业务代码跟随协议细节变化，互操作性也难以由契约测试证明。

### 14.2 一次支持完整 MCP 功能集

拒绝。DramaAgent 当前需求是调用外部 Tool。Resources、Prompts、Apps、Tasks 和 Server 角色没有明确用户路径，会扩大权限、UI 和维护范围。

### 14.3 让 Planner 直接调用 Tool

拒绝。它绕过现有确认、幂等、单活动 Run、取消和审计规则，也与项目既有 Agent 设计冲突。

### 14.4 根据 annotation 自动免确认和重试

拒绝。annotation 由外部 Server 提供，只是提示。未经项目管理员建立信任关系时，它不能作为安全保证。

## 15. 验收标准

- 标准 MCP Server 可以被发现，协议版本和 capabilities 可读取。
- `tools/list` 结果经过 allowlist 后进入 Tool catalog。
- 同名 Tool 在不同 Server 下不会冲突。
- 用户请求可以生成 MCP Action，确认后只调用一次。
- 参数和结构化结果均通过 Schema 校验。
- Server 不可用、Tool 变化、超时、协议错误和取消都有稳定状态与错误码。
- 外部描述和结果不能改变系统指令或触发后续 Tool。
- Token、URL 内敏感信息和外部异常细节不进入用户响应或日志。
- `MCP_ENABLED=false` 时现有功能和测试行为不变。
- 自动化测试不访问真实网络，不调用真实 LLM。

## 16. 前提与回滚

本设计假设产品需求是“DramaAgent 调用外部 MCP Tool”。若需求改为“其他 MCP Host 调用 DramaAgent”，应新建设计文档，不在本方案上追加 Server 角色。

回滚时关闭 `MCP_ENABLED` 即可停止注册和调用外部 Tool。领域新增类型保留兼容读取，旧 Adapter 在一个发布周期内保留为弃用实现，确认无调用方后删除。回滚不修改 Artifact、AgentAction 或 WorkflowRun 历史数据。

