# Changelog

本项目采用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 风格，版本语义遵循 [SemVer](https://semver.org/lang/zh-CN/)。任务驱动细节见 [docs/DEV_LOG.md](docs/DEV_LOG.md)。

## [Unreleased]

### Fixed（用户实测反馈）
- **目标集数未传入需求归一化**：normalize 节点构造 `RequirementInput` 时未传集数，吃领域模型默认 10——用户选 3 集时需求/StoryBible 仍按 10 集规划（Planner 随后正确地发现矛盾并澄清"3 还是 10"）。修复：从 Run config 的 `outline_count`/`script_count` 透传。
- **LLM 超时配置未生效**（真实 bug）：`generate_structured` 的 `timeout_seconds` 硬编码默认 180s，逐请求覆盖了 `.env` 的 `LLM_TIMEOUT_SECONDS`——改为未传参时回退 Settings 值（显式传参仍优先）；outline skill `max_tokens` 4096→8192（10 集大纲 JSON ≈3.5-5k tokens，默认值有截断→重试→更慢的恶性循环）。

### Added（Phase L 进行中）
- **L-5**：分阶段成为默认创作体验 + Planner v1.1——`staged` 默认 true（对话工作台默认"先 SB+大纲确认再写剧本"，一口气生成 = 门上点"写全部"；legacy 直连 API 行为不变）；Composer 移除分阶段开关；Planner prompt v1.1：典型创作请求（"我想写 XX 故事"）直接判 create_script 不澄清、"先搭大纲/分阶段"与默认一致（修复用户实测暴露的两轮连环澄清）；门 UI 与 RunProgress 改由 Run 查询驱动（`gated-run` 共享缓存，续跑后按钮消失/批门复现）；评测集增至 60 条（补愿望式创作与分阶段表达用例）。
- **L-4**：剧本分批生成——continue 请求支持 `batch_size`（1 集 / 5 集 / 剩余全部）；批模式本批写完并评估后停在 `scripts` 门（事件含已写/目标集数），可逐批推进或先聊天改稿；`RunResponse` 暴露 `stage_gate`；计划卡按门类型显示批次按钮组（大纲门同样提供"先写第 1 集 / 前 5 集 / 写全部"起批方式）；write_episodes 复用已有集跳过续写、Artifact 幂等不重算。
- **L-3**：确认门续跑——`POST /runs/{id}/continue`（仅 stage_gate=outline 可续；剥离门字段；大纲刷新为最新 valid 版本，暂停期间聊天改版生效；从 checkpoint 恢复不重算）；前端分段门 needs_review 计划卡显示「继续创作剧本」主按钮。
- **L-2**：分段创作确认门——Turn 请求 `staged=true` 时创作计划带 `stop_after=outline`，Run 在 StoryBible 与大纲就绪后转 `needs_review`（事件携带 `stage_gate` 与 Artifact ID），不写剧本；创作图 outline 节点后新增条件边；前端 Composer「创作设置」新增分阶段开关。
- **L-1**：目标集数端到端生效——Turn 请求新增结构化 `target_episode_count`（1-50），create_script 计划的大纲与剧本集数按用户选择生成，替代此前「文本提示被系统默认覆盖」的行为；集数参与幂等 request_hash；前端 Composer「创作设置」改为结构化传递，仅显式调整时携带。

### Added（Phase K 进行中）
- **K-4**：知识库 E2E 与冒烟——`e2e/knowledge.spec.ts`（上传参考资料 → 导入自动入库 → 知识库页列表/检索试算命中 → 删除清空，FakeEmbedder 确定性）；`scripts/embedding_smoke.py`（真实 provider 连通性/维度/语义性冒烟，需真实 Key，未执行）；KNOWN_LIMITATIONS V1 backlog 第 1 项（RAG 深入）出列。
- **K-3**：前端知识库页——`/projects/{id}/knowledge`：文档列表（作用域徽标/块数/向量化状态/来源、scope 过滤）、项目自有文档软删除（全局语料标"平台维护"不可删）、检索试算框（命中含相似度/作用域/来源 + trace 元数据）；AgentWorkspace 次级导航新增知识库入口。
- **K-2**：知识库管理 API——`GET /projects/{id}/knowledge`（自有 + 全局语料列表，含块数/向量化状态/作用域过滤）、`DELETE`（软删，0008 迁移 `deleted_at`；全局语料与其他项目文档不可删，幂等 404）、`POST .../knowledge/search`（检索试算，命中含 score/scope/来源 + trace 元数据）；检索自动排除已删除文档。
- **K-1**：上传联动摄取——导入分类 `route=hold`（reference）的参考资料自动切块 + 向量化入 knowledge corpus；0007 迁移为 `knowledge_documents` 增加项目作用域 `project_id`（NULL = 全局语料）；检索按“项目自有 + 全局”过滤，项目间互不可见；`reference` 分类进入创作三阶段检索；幂等（同 upload 的 document_hash 命中跳过）；摄取失败降级为告警事件不阻断导入归档（先向量化后落库，无半截文档）。

### 计划中
- 成本可视化、多用户认证（见 [KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) §4）。

## [0.2.0] - 2026-08-23

对话式创作 Agent（Phase J）：12/12 任务完成，M1～M4 里程碑达成。质量门禁：后端 1099 passed、前端 181 passed、E2E 7 用例 × 5 轮全绿、mypy/Ruff 零错误。真实模型评测（`pytest -m eval_real`）需 API Key，尚未执行（见 [AGENT_EVAL_REPORT.md](docs/AGENT_EVAL_REPORT.md)）。

### Added（Phase J 对话式创作 Agent）

- **J-01**：AgentTurn / AgentAction / Message 持久化契约——严格 Schema（`extra="forbid"`）、Turn 幂等收据 + planning lease、Action/Message 状态机与并发约束（0005 迁移）。
- **J-05**：持久化 WorkflowDispatcher 与 checkpoint 恢复——数据库租约领取（0006 迁移）、PostgreSQL checkpoint saver、Run 幂等（`(project_id, action, idempotency_key)` 唯一 + `request_hash` 冲突检测）、进程内幂等字典移除。
- **J-02**：项目上下文组装与预算控制——AgentContextService、活动上下文校验、消息 / Artifact 索引、预算保护分段。
- **J-03**：对话命令 Planner Skill——服务端 `available_intents` 白名单（Wave 2 仅 `create_script | explain | evaluate`）、确定性澄清优先、三轮未解决给出合法命令示例、拒绝工具/API/SQL/Artifact ID 输出。
- **J-04**：Agent Turn/Action 服务与 API——三段式 Turn 执行（短事务 A → 租约内 Planner → 短事务 B）、plan 全服务端模板化、5 个端点（turns 200/202、GET turn/action、confirm/reject）、confirm 行锁内快照过期检测（`ACTION_STALE`）、Run 幂等键 `agent-action:{action_id}`、并发单活跃 Run 保护。
- **J-06**：对话式剧本修订工作流——`revise_script` 子图（prepare_target → ensure_evaluation → revise → continuity_check → re_evaluate）、目标由服务端解析的 source script ID 决定、目标缺评估时先仅评估目标集、用户约束写入 RevisionPlan、单轮修订不进自动循环、`revise_script` intent 开放进 Planner 白名单与确认执行（目标集无有效剧本 → `SCRIPT_NOT_FOUND`）。
- **J-07**：大纲修订 Skill 与影响分析 Tool——`OutlineRevisionInput`（旧大纲 / Story Bible / 用户约束 / source outline ID），输出完整 `EpisodeOutlineSet` 不接受 patch；服务端不变量（集数不变、集号唯一连续、required_characters 可追溯、locked_facts 否定窗口检测）；`OutlineImpactTool` 确定性逐字段比较，输出变更集、字段明细、依赖旧大纲的剧本 ID 与 follow-up 建议（空白差异不算变化，不调用 LLM）。
- **J-08**：大纲修订工作流与版本落库——`revise_outline` 单节点工作流（加载 → Skill → 落库 → 影响分析）；合法输出成为 latest valid（sources 绑定旧大纲 `revises` + Story Bible `references`），旧 Artifact 内容/checksum 不变；不变量失败保存 invalid 诊断版本且 Run failed；`GET /artifacts/{id}/references` 反向引用查询可指出仍引用旧大纲的剧本；`revise_outline` intent 开放进 Planner 白名单与确认执行。
- **J-09**：AgentAction 生命周期、Outcome 与一次后续计划——Dispatcher 在 Run 状态变化时同步 Action（queued→running→终态）；AgentOutcomeService 确定性证据优先生成 `goal_status`/`evidence_artifact_ids`/`score_delta`/`remaining_constraints`，仅语义约束交由 Evaluator Skill（模型无从改写确定性结论）；部分达成时用最新 Artifact 重新解析目标，创建深度 1 的 proposed 子 Action（唯一约束 + SAVEPOINT 幂等，深度 1 不再延伸）；GET Action 触发 reconciliation 补写 Outcome/结果消息/后续计划；新事件 `agent_action.updated` 携带 `agent_action_id` 与 `goal_status`。
- **J-10**：前端 Agent API 契约与数据 Hooks——`agentApi`（createTurn 200/202/getTurn/getAction/confirm/reject）与 `conversationsApi`（create/list/messages）；`useAgentConversation`（会话管理、消息分页、发送幂等 key 失败重发复用、202 planning 轮询、输入保留）；`useAgentAction`（非终态轮询/卸载停止、确认防重复点击、重复确认复用 Run、ACTION_STALE 可恢复错误、query invalidation）与 `useAgentActionEvents`（SSE `agent_action.updated`）。
- **J-11**：对话式创作工作台 UI——双栏布局（会话/消息/Composer/内嵌 RunProgress + 产物索引/active context/计划卡），平板抽屉、移动端单栏与底部 Composer；ActionPlanCard 确认唯一主按钮 + stale/needs_review/failed 恢复入口；结果消息展示 goal_status/评分变化/剩余约束/证据链接；设计令牌与可访问性（aria-live、44px 触控、reduced motion、可见焦点）；`NEXT_PUBLIC_AGENT_WORKSPACE_ENABLED` 回滚开关（false 恢复旧布局，两模式共用 API 与 Artifact）。
- **J-12**：E2E、Agent 评测与退出门禁——`e2e/agent-workspace.spec.ts`（首次创作计划→确认→完成+刷新恢复、澄清无 Run、重复发送不重复消息、第 3 集修订→版本 Diff、大纲修订→部分达成→一次后续计划→再确认、重复确认单 Run）；`FAKE_LLM_SCENARIO=agent_e2e` 内容感知 planner 桩（注入边界提取用户原文，五意图+explain 路由）；`tests/evals/` 命令评测集 55 条 + Outcome 评测集 32 条与双模式 harness（CI 确定性契约 100%，`eval_real` marker 真实模型评测落盘不写模拟数字）；RunResponse 暴露 `agent_action_id`；`docs/AGENT_EVAL_REPORT.md` 与 TEST_PLAN §11。

### Fixed（J-12 顺带修复）

- 评估 Artifact 行集数错位（自 Phase E 潜伏的存量缺陷）：`evaluate_script` 改为以剧本 Artifact 行集数为权威（outline 查找 / 报告生成 / 落库 / 内容回填），此前 FakeLLM 场景下三集评估全部落入 episode=1 桶导致集数错位。
- 前端并发发送守卫：`useAgentConversation.sendTurn` 增加 in-flight ref，双击 / 双 Enter 不再产生并发提交。
- E2E 编排：e2e.sh 以 setsid 进程组启停前端，修复 next-server 孤儿进程占用端口导致后续运行 EADDRINUSE 连环失败。

## [0.1.0-rc1] - 2026-08-16

MVP 发布候选：Phase A–I 全部完成。

### Added（Phase I 稳定性 / 扩展 / 发布）

- **韧性（I-01）**：LLM 统一重试（429/timeout/5xx 指数退避 + 尊重 Retry-After）；per-run 调用数 / Token 预算（软上限发 warning 事件，硬上限 → `RUN_BUDGET_EXCEEDED`）；协作式取消（`POST /runs/{id}/cancel`，cancel 后不创建新 Artifact）；失败从 checkpoint 恢复（`POST /runs/{id}/retry`，不重调已完成节点 / 不重复建 Artifact / 不重复推进 revision_round）；WorkflowRun 落库 `error_code`/`error_detail`（所有失败带机器可读错误码）。
- **可观测（I-02）**：进程内 Prometheus 指标（`GET /metrics`，开关 `metrics_enabled`）；`GET /runs/{id}/diagnostics` 聚合事件表输出节点时间线 / LLM 调用与 Token 统计 / 失败信息；日志脱敏（掩 sk-*、api_key、Bearer，超长截断）。
- **安全（I-03）**：集中 `core/security.py`；Prompt 注入内容边界隔离（loader 层，manifest 声明 user_content_vars → render 包裹定界 + 固定指令句）；上传 / 存储 / 导出路径与归属防护去重复用；日志 / 导出转义回归测试。
- **扩展（I-04）**：`MCPToolAdapter` 外部 HTTP JSON-RPC 工具映射（超时 → `EXTERNAL_TOOL_TIMEOUT`，错误 → 泛化 `EXTERNAL_TOOL_ERROR` 不泄漏内部信息；默认关闭，主流程零影响）；Tool/Skill 注册表 `get_metadata`/`list_metadata`；`docs/EXTENSIONS.md` 新增 Skill 最小示例。
- **性能与回归（I-05）**：`tests/performance/`（普通 API p95<300ms / 100 并发 SSE / 1000 Artifact 分页）；覆盖率双门禁（总体 ≥75% 实测 88%，核心 domain/workflows/artifacts ≥85% 实测 92%）；`make perf` / `make cov`；E2E 选择器竞态与 compose 项目名隔离修复。

### Changed
- `pyproject.toml`：覆盖率 `fail_under` 70 → 75；新增 `performance` marker（默认排除）。
- CI：测试排除 `not smoke and not performance`，新增核心覆盖率门禁步骤。
- `.env.example`：补充 `EXPORT_FILE_ROOT`、`SHORT_TERM_TTL_SECONDS`、`CONVERSATION_SUMMARY_THRESHOLD`（此前只有代码默认值）。

### Fixed
- 结构化日志 JSON 键名与契约不一致（存量 2 失败）。
- E2E「创建项目」strict-mode 选择器在空态过渡帧的竞态。
- e2e 与开发 compose 共享 project name 导致清理误删开发库容器。

### Docs
- 新增 `OPERATIONS.md` / `SECURITY.md` / `EXTENSIONS.md` / `TEST_REPORT.md` / `DEMO.md` / `KNOWN_LIMITATIONS.md`。

### Security
- 见 [docs/SECURITY.md](docs/SECURITY.md)（威胁模型 / 输入卫生 / 输出转义 / 注入隔离 / 日志脱敏 / 数据删除 / MVP 局限）。

## [0.0.x] — 2026-06 ~ 2026-08（Phase A–H，开发期）

- **Phase H**：Next.js 前端工作台（项目 / 创作 / SSE 进度 / 工作台 / 修订 / Diff / 导出中心）+ Playwright E2E 全链路。
- **Phase G**：短期 / 中期 / 项目记忆 + Context Builder + 安全上传（TXT/DOCX Parser）+ 导入分类路由 + Markdown/DOCX Exporter + Export API 与集成。
- **Phase F**：自动修订最低分集（F-01~F-05，确定性选集 + 版本化修订 + 连续性检查 + 重评）。
- **Phase E**：逐集评估（Rubric / 维度评分 / 质量门禁）。
- **Phase C**：创作链路（Prompt 管理系统 / 需求归一化 / StoryBible / 分集大纲 / 剧本写作 / Creation API）。
- **Phase B**：FastAPI + SQLAlchemy + Alembic + Repository + 项目/会话/消息 API + LLM 抽象 + LangGraph 工作流引擎。
- **Phase A**：工程基线（uv / Ruff / mypy / pytest / Docker Compose / Makefile / 文档契约）。

---

- [docs/DEV_PLAN.md](docs/DEV_PLAN.md) — 权威开发计划与任务状态
- [docs/DEV_LOG.md](docs/DEV_LOG.md) — 逐任务开发日志
