# Changelog

本项目采用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 风格，版本语义遵循 [SemVer](https://semver.org/lang/zh-CN/)。任务驱动细节见 [docs/DEV_LOG.md](docs/DEV_LOG.md)。

## [Unreleased]

### Added（Phase J 对话式创作 Agent，进行中：J-01～J-05 / 12 完成，M1、M2 里程碑达成）

- **J-01**：AgentTurn / AgentAction / Message 持久化契约——严格 Schema（`extra="forbid"`）、Turn 幂等收据 + planning lease、Action/Message 状态机与并发约束（0005 迁移）。
- **J-05**：持久化 WorkflowDispatcher 与 checkpoint 恢复——数据库租约领取（0006 迁移）、PostgreSQL checkpoint saver、Run 幂等（`(project_id, action, idempotency_key)` 唯一 + `request_hash` 冲突检测）、进程内幂等字典移除。
- **J-02**：项目上下文组装与预算控制——AgentContextService、活动上下文校验、消息 / Artifact 索引、预算保护分段。
- **J-03**：对话命令 Planner Skill——服务端 `available_intents` 白名单（Wave 2 仅 `create_script | explain | evaluate`）、确定性澄清优先、三轮未解决给出合法命令示例、拒绝工具/API/SQL/Artifact ID 输出。
- **J-04**：Agent Turn/Action 服务与 API——三段式 Turn 执行（短事务 A → 租约内 Planner → 短事务 B）、plan 全服务端模板化、5 个端点（turns 200/202、GET turn/action、confirm/reject）、confirm 行锁内快照过期检测（`ACTION_STALE`）、Run 幂等键 `agent-action:{action_id}`、并发单活跃 Run 保护。

### 计划中
- RAG 检索（Phase D）深入、多用户认证、分布式预算（见 [KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) §4）。
- Phase J 后续：J-06 对话式剧本修订 → J-12（M3 修订链路、M4 工作台与评测）。

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
