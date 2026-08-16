# DramaAgent 已知限制与 V1 Backlog（MVP v0.1.0-rc1）

> 本文档记录 MVP 明确接受的限制与 V1 backlog 候选。每项标注「MVP 接受」或「backlog」，
> 便于发布说明与后续排期。权威安全模型见 [SECURITY.md](SECURITY.md)，扩展边界见 [EXTENSIONS.md](EXTENSIONS.md)。

## 1. 功能范围限制

| # | 限制 | 类型 | 说明 / 缓解 |
|---|------|------|------|
| 1 | **RAG（Phase D）未实现** | MVP 接受 | 检索类 `retrieve` 节点为占位实现；知识库素材不参与生成。V1 候选。 |
| 2 | **单用户 / 无认证** | MVP 接受 | MVP 无账号系统，`GET /metrics` 等运维端点无鉴权；部署时须置于受控网络。 |
| 3 | **无多工作区 / 项目间共享** | MVP 接受 | 项目、记忆均为单项目隔离。 |
| 4 | **修订仅 1 轮**（`MAX_REVISION_ROUNDS=1`） | MVP 接受 | 按阶段 F 契约；`evaluate` 可手动再触发，等价扩展轮次。 |
| 5 | **自动修订仅选最低分 1 集** | MVP 接受 | 平局取最小 `episode_number`（确定性，F-05）。 |
| 6 | **前 3 集剧本**（`MVP_SCRIPT_COUNT=3`） | MVP 接受 | 大纲 10 集，剧本默认 3 集；前端可配 1/2/3/5/10。 |
| 7 | **MCP 外部工具默认关闭** | MVP 接受 | 无 MCP 配置时主流程零影响；启用需自建 JSON-RPC 服务（见 EXTENSIONS.md）。 |
| 8 | **Prompt 注入仅内容边界隔离** | MVP 接受 | 不改系统/用户角色拆分；模板级边界定界 + 固定指令句（I-03），非强隔离。 |

## 2. 工程 / 运行限制

| # | 限制 | 类型 | 说明 / 缓解 |
|---|------|------|------|
| 9 | **LLM 错误中模型无 per-provider 流控语义** | MVP 接受 | 统一重试（429/timeout/5xx 退避 + Retry-After）+ 软/硬预算兜底（I-01）。 |
| 10 | **per-run 预算为进程内实现** | MVP 接受 | `RunBudgetRegistry` 内存计数，多进程/多实例不共享（已知局限，V1 可换 Redis/DB）。 |
| 11 | **取消为协作式（cooperative）** | MVP 接受 | 节点守卫处中断，无法打断正在进行的单次 LLM 调用（其超时后自然结束）。 |
| 12 | **幂等键为内存级** | MVP 接受 | 相同 `idempotency_key` 仅在进程生命周期内去重；重启后重复提交会新开 Run。 |
| 13 | **短期记忆 TTL / 摘要阈值仅在配置层** | MVP 接受 | `SHORT_TERM_TTL_SECONDS` / `CONVERSATION_SUMMARY_THRESHOLD` 已接配置，未接后台定时回收。 |
| 14 | **Metrics 为进程内计数，无外部时序库** | MVP 接受 | Prometheus 文本格式可被外部采集器拉取；不引入时序存储依赖。 |
| 15 | **SSE 连接数为进程内 gauge** | MVP 接受 | 多副本部署时各自计数。 |
| 16 | **E2E / 性能测试需本地基础设施** | MVP 接受 | `make perf` / `make e2e` 需 `make up`（PostgreSQL + Redis）；普通 CI 排除 performance 标记。 |

## 3. 数据与存储限制

| # | 限制 | 类型 | 说明 / 缓解 |
|---|------|------|------|
| 17 | **导入不支持 DOCX 内嵌图片 / 复杂分页** | MVP 接受 | 纯文本提取为主，详见 PROMPT_GUIDE / 导入实现。 |
| 18 | **导出 DOCX 为单文档**（未分册打包） | MVP 接受 | 每项目一次导出一个 DOCX；V1 可加按集拆分。 |
| 19 | **无自动备份 / 恢复工具** | MVP 接受 | PostgreSQL 备份命令与步骤见 OPERATIONS.md；V1 可加 scheduled backup。 |

## 4. V1 Backlog（按优先级）

1. **RAG 检索（Phase D）**：pgvector 向量检索接入 `retrieve` 节点，知识库素材参与生成。
2. **多用户与认证授权**：登录、项目权限、运维端点鉴权（配合 I-03 安全模型扩展）。
3. **多轮修订与人工介入工作流**：按轮人工选择修订集、对比采纳。
4. **成本可视化**：按 Run / 按项目的 Token 报表 + 预算告警（复用 diagnostics 数据）。
5. **分布式预算 / 幂等**：RunBudget 与 idempotency 下沉到 Redis/DB，支持多实例。
6. **导入增强**：图片 / 表格 / 多文件批量导入；导出分册与封面。
7. **可观测增强**：外部时序库接入、分布式 tracing（OpenTelemetry）。
8. **MCP 服务端能力**：作为工具提供方被其他 Agent 调用（当前仅消费侧）。

## 5. 数据删除策略

- 上传 / 导出临时文件：见 SECURITY.md §数据删除；`var/uploads`、`var/exports` 按需清理。
- Artifact 为**不可变版本**，删除需显式运维操作（V1 未提供 UI 删除）。
