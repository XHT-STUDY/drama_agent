# DramaAgent 测试报告（v0.2.0）

> 汇总 Phase J（对话式创作 Agent，J-01～J-12）完成时的全部验证结果；
> v0.1.0-rc1（Phase I）的历史数字见 [CHANGELOG.md](CHANGELOG.md)。
> 权威任务状态见 [DEV_PLAN.md](DEV_PLAN.md)；本报告给出可复现的数字与复现命令。

## 1. 测试范围与运行方式

| 类别 | 目录 / 标记 | 运行方式 |
| --- | --- | --- |
| 单元测试 | `tests/unit` | 默认 `pytest` |
| 集成测试 | `tests/integration` | 默认 `pytest` |
| 契约测试 | `tests/contract` | 默认 `pytest` |
| 工作流测试 | `tests/workflow` | 默认 `pytest` |
| 安全回归 | `tests/security` | 默认 `pytest` |
| 性能测试 | `tests/performance` | `make perf`（默认被 `-m not performance` 排除） |
| Agent 契约评测 | `tests/evals` | 默认 `pytest`（FakeLLM / 确定性规则） |
| 真实模型评测 | `eval_real` 标记 | `pytest -m eval_real`（需 `EVAL_LLM_ENABLED=1` + 真实 Key，默认排除） |
| 手工冒烟 | `smoke` 标记 | 不进入 CI，手工触发真实模型 |
| E2E（Playwright） | `e2e/dramaagent.spec.ts` + `e2e/agent-workspace.spec.ts` | `make e2e REPEAT=N` |

所有自动化测试使用 **FakeLLM / FakeEmbedder**，无真实 LLM 调用；真实模型仅用于 `smoke` / `eval_real` 标记的手工验证。

## 2. 测试计数

| 项目 | 结果 | 备注 |
| --- | --- | --- |
| 后端全量 `pytest`（排除 performance / eval_real） | **1099 passed / 0 failed**（8 deselected） | deselected = performance ×6 + eval_real ×2（需真实 Key） |
| Agent 契约评测（`tests/evals`，含于全量） | preflight 澄清召回 **100%**（12 条确定性用例，零模型调用）；Outcome 确定性规则 **100%**（26 条） | 数据集：命令 55 条 / Outcome 32 条 |
| 性能套件 `make perf` | **6 passed / 0 failed**（v0.1.0-rc1 实测） | API 延迟 3 + 并发 SSE 2 + 1000 Artifact 1 |
| E2E `make e2e REPEAT=5` | **35/35 passed**（7 用例 × 5 轮，1.7m） | agent workspace ×6 + H-07 全链路 ×1；`FAKE_LLM_SCENARIO=agent_e2e` |
| 前端 Vitest | **181 passed** | 含 agent hooks/workspace 组件测试（J-10/J-11） |

## 3. 覆盖率门禁（双门禁）

> 复用同一份 `.coverage` 数据：`make cov` 先跑 `pytest --cov`（强制总体门禁），
> 再 `coverage report --include=...`（强制核心门禁）。

| 门禁 | 阈值 | 实测（2026-08-23，`make cov`） | 结果 |
| --- | --- | --- | --- |
| 总体覆盖率 | ≥ 75%（pyproject `fail_under=75`） | **85%** | ✅ |
| 核心覆盖率（domain / workflows / artifacts） | ≥ 85%（CI + `make cov`） | **92%** | ✅ |

复现：`make cov`（含全部测试 + 双门禁）。总体覆盖较 rc1 的 88% 下降 3 个百分点：
Phase J 新增约 100 个测试的同时新增了等量生产代码（Agent 服务/工作流/评测 harness），
核心模块覆盖率保持 92% 不变。

## 4. 性能指标（§1.6 非功能指标）

> 实测于 WSL2 本地（真实 PostgreSQL + Redis，`make up`）；不含 LLM 调用。
> 测试内输出实测 p95，阈值断言在测试代码中。

| 指标 | 阈值 | 实测 | 结果 |
| --- | --- | --- | --- |
| `GET /api/v1/health/ready` p95 | < 300ms | **28.1ms** | ✅ |
| `GET /api/v1/projects`（DB 读 + 序列化）p95 | < 300ms | **30.4ms** | ✅ |
| `POST /api/v1/projects`（DB 写）p95 | < 300ms | **31.2ms** | ✅ |
| 100 并发 SSE 连接首事件块 p95 | < 1s | **701.8ms** | ✅ |
| 1000 Artifact 分页查询（100/页 × 10）p95 | < 300ms | **45.3ms** | ✅ |
| SSE 连接释放（关闭后 gauge 回落基线） | 回落基线 | **通过** | ✅ |

复现：`make up && make perf`。

### 4.1 含 LLM 与不含 LLM 的耗时区分

- **不含 LLM 的普通 API**（§1.6 指标，本报告 §4）：p95 均 < 300ms，实测最低 28ms。
- **含 LLM 的工作流链路**：耗时取决于 LLM 提供商响应与重试策略
  （I-01 统一重试层 + 软/硬预算），**不承诺** p95 指标；
  单集剧本生成耗时为模型延迟 × 调用次数，典型几分钟量级，
  通过 SSE 事件流以 `run_id` 观察进度（见 OPERATIONS.md 诊断接口）。
- E2E Demo（FakeLLM）单轮约 3 分钟，为全链路（10 集大纲 + 前 3 集剧本 + 评估 + 修订 + 连续性 + 导出）的确定性验证。

## 5. E2E 验收

- `make e2e REPEAT=5` → **5 次连续通过**（Playwright `status: passed, failedTests: []`，`5 passed (14.9s)`）。
- E2E 基建与开发基建**隔离**：`docker-compose.e2e.yml` 使用独立项目名 `drama-e2e`、
  独立端口（PostgreSQL:5433 / Redis:6380）、独立卷，与 `make up` 的开发库互不干扰。
- 每次运行前 `DROP DATABASE drama_e2e` + 重建 + Alembic 迁移，保证确定性。

## 6. 安全回归

- `tests/security`：Prompt 注入隔离、转义输出、日志扫描（无 `sk-`/无完整 Prompt）、CORS 配置。
- 前端 `escaping.test.tsx`：剧本含 `<script>` 渲染为纯文本。
- 详见 [SECURITY.md](SECURITY.md)。

## 7. 已知失败与限制

- 无存量失败；2 个历史 TestStructuredLogging 失败已在 I-02 修复。
- **真实模型评测未执行**（`pytest -m eval_real`）：环境无真实 API Key；
  harness/数据集就绪，执行方式与报告模板见 [AGENT_EVAL_REPORT.md](AGENT_EVAL_REPORT.md)
  （DESIGN §2 成功标准中的生产模型准确率指标待此闭环）。
- 性能测试需 `make up`（真实 DB/Redis），不进入普通 CI（默认排除 performance 标记）。
- SSE 测试在客户端断开风暴时会产生 asyncio 连接级日志噪声（uvicorn 记录
  `protocol.data_received() call failed`），属预期行为，不影响结果与连接释放断言。
