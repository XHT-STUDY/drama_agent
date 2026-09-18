# DramaAgent 测试计划

> 文档版本：v1.0  
> 适用阶段：MVP（阶段 A–I）  
> 依据文档：[DEV_PLAN.md](./DEV_PLAN.md) §10

---

## 1. 测试分层

DramaAgent 按隔离程度和速度将测试分为六层：

| 层 | 标记 / 工具 | 内容 | 是否调用真实 LLM | 运行频率 |
|---|---|---|---|---|
| **unit** | `pytest -m unit` | 纯领域规则、Schema、工具函数、选择器 | 否 | 每次提交 |
| **contract** | `pytest -m contract` | API 响应、Event 结构、Prompt 输出 Schema 快照、Golden fixture 回归 | 否 | 每次提交 |
| **integration** | `pytest -m integration` | API + PostgreSQL + Redis + 文件系统 纵切 | 否 | 每次提交 |
| **workflow** | `pytest -m workflow` | FakeLLM 驱动的 LangGraph 节点、条件分支、失败恢复 | 否 | 每次提交 |
| **e2e** | `pnpm playwright test` | Playwright 浏览器端到端完整 Demo | 否 | 每次 PR / main 分支 |
| **smoke** | `pytest -m smoke` | 手工触发真实模型最小链路验证 | **是** | 发布前人工执行 |

### 分层原则

- **越下层越稳定**：unit/contract 测试不依赖外部服务，可毫秒级完成；
- **越上层越慢**：integration/workflow 需要 PostgreSQL/Redis 容器，e2e 还需要浏览器；
- **smoke 不进 CI**：真实 LLM 调用成本高、结果不幂等，仅在发布前人工执行。

---

## 2. 各层运行时机

| 场景 | 运行内容 | 命令 |
|---|---|---|
| 本地开发（每次保存） | unit + contract | `pytest -m "unit or contract"` |
| 本地提交前 | unit + contract + lint + typecheck | `make ci` |
| CI（push/PR） | unit + contract + integration + workflow（不含 smoke） | GitHub Actions `.github/workflows/ci.yml` |
| 合并到 main 前 | 全部（含 e2e） | `make e2e` |
| 发布候选 | 全部 + smoke | `make ci && make e2e && make perf` |

---

## 3. 测试工具链

### 后端

| 工具 | 用途 | 配置文件 |
|---|---|---|
| [pytest](https://docs.pytest.org/) | 测试运行器 | `backend/pyproject.toml` → `[tool.pytest.ini_options]` |
| [pytest-cov](https://pytest-cov.readthedocs.io/) | 覆盖率收集 | `backend/pyproject.toml` → `[tool.coverage.*]` |
| [Ruff](https://docs.astral.sh/ruff/) | 代码风格与静态检查 | `backend/pyproject.toml` → `[tool.ruff]` |
| [mypy](https://mypy-lang.org/) | 类型检查（strict mode） | `backend/pyproject.toml` → `[tool.mypy]` |

### 前端

| 工具 | 用途 | 配置文件 |
|---|---|---|
| [Vitest](https://vitest.dev/) | 测试运行器 + 覆盖率 | `frontend/vitest.config.ts` |
| [ESLint](https://eslint.org/) | 代码风格检查 | `frontend/eslint.config.mjs` |
| [TypeScript](https://www.typescriptlang.org/) | 类型检查 | `frontend/tsconfig.json` |
| [Playwright](https://playwright.dev/) | E2E 浏览器测试 | `e2e/`（阶段 H 正式加入） |

---

## 4. 覆盖率目标

| 阶段 | 后端 domain/workflow/artifact | 后端 总体 | 前端 |
|---|---|---|---|
| **MVP 前期（阶段 A–B）** | ≥ 70% | ≥ 70% | 覆盖率收集，无强制阈值 |
| **MVP 中期（阶段 C–H）** | ≥ 70% | ≥ 70% | 随业务代码增长逐步提升 |
| **发布（阶段 I）** | ≥ 85% | ≥ 75% | ≥ 70% |

覆盖率失败阈值在 CI 中自动执行：
- 后端：`[tool.coverage.report] fail_under = 70`
- 前端：`vitest.config.ts` → `coverage.thresholds`

本地查看覆盖率：

```bash
# 后端
cd backend && uv run pytest --cov=app --cov-report=html -m "not smoke"
# 打开 backend/htmlcov/index.html

# 前端
cd frontend && pnpm test:coverage
# 打开 frontend/coverage/index.html
```

---

## 5. FakeLLM 规则

- 所有自动化测试默认使用 **FakeLLM**，按 `prompt_name` 返回 fixtures/golden 中的合法对象；
- CI 环境变量 `APP_ENV=test` 强制 `llm_provider="fake"`（见 `backend/app/core/config.py`）；
- FakeLLM 支持故障注入：可配置第 N 次调用超时、限流或输出非法 JSON；
- smoke 测试是唯一允许调用真实 LLM 的层级，**禁止进入普通 CI pipeline**。

---

## 6. 本地运行命令

完整命令参考仓库根目录 `Makefile`：

```bash
# 安装全部依赖
make install

# 启动本地基础设施（PostgreSQL + Redis）
make up

# 检查环境健康
make doctor

# 代码风格检查（后端 Ruff + 前端 ESLint）
make lint

# 类型检查（后端 mypy + 前端 tsc）
make typecheck

# 运行全部测试
make test

# 完整 CI 流水线（lint + typecheck + test）
make ci

# E2E 测试（需要 make up）
make e2e

# 性能测试
make perf
```

按测试子集运行：

```bash
# 后端
pytest -m unit                     # 仅单元测试
pytest -m integration              # 仅集成测试
pytest -m workflow                 # 仅工作流测试
pytest -m contract                 # 仅契约测试
pytest backend/tests/unit/skills/test_story_bible.py  # 单个文件

# 前端
pnpm test                          # 全部 Vitest 测试
pnpm test -- projects              # 按名称过滤
pnpm playwright test               # E2E 测试
```

---

## 7. CI 流程

GitHub Actions 工作流定义在 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)：

1. **触发条件**：push 到 `main` 或 PR 到 `main`
2. **并发控制**：同一分支的新运行自动取消旧运行
3. **后端 Job**：
   - Python 3.11 + uv
   - PostgreSQL + pgvector + Redis 作为 service 容器
   - Ruff → Mypy → Pytest（`-m "not smoke"`，含覆盖率）
   - 覆盖率 HTML/XML 上传为 Artifact（保留 7 天）
4. **前端 Job**：
   - Node.js LTS + pnpm
   - ESLint → tsc → Vitest（含覆盖率）
   - 覆盖率报告上传为 Artifact（保留 7 天）
5. **环境变量**：CI 设置 `APP_ENV=test`，不读取开发者 `.env` 文件

---

## 8. 测试编写规范

### Pytest Marker 使用

```python
import pytest

# 标记测试类型
pytestmark = pytest.mark.contract  # 整个模块标记

@pytest.mark.unit
def test_compute_overall_score() -> None: ...

@pytest.mark.integration
async def test_create_project_api() -> None: ...

@pytest.mark.smoke  # 仅人工执行
def test_real_llm_story_bible() -> None: ...
```

### Fixture 与 Golden 文件

- **Golden fixtures**：存放在 `backend/tests/golden/`，用于 contract 测试的输入输出快照；
- **conftest.py**：共享 fixture 和工具函数放在最近的 `conftest.py` 中；
- **FakeLLM fixtures**：存放在 `backend/tests/fixtures/`，按 `prompt_name` 组织。

### 命名约定

- 测试文件：`test_<模块名>.py`
- 测试类：`Test<被测对象>`
- 测试函数：`test_<行为>_<预期结果>`
- 中文 docstring 解释测试意图和验收条件

### 断言风格

- 使用 pytest 原生 `assert`，不引入额外断言库；
- 异常断言使用 `pytest.raises(ValidationError, match="...")`；
- 异步测试使用 `pytest-asyncio` + `async def`。

---

## 9. 关键测试场景（来自 §10.3）

这些场景覆盖 MVP 核心风险点，随对应阶段逐步实现：

1. 10 集大纲缺第 7 集时校验失败（C-04）
2. ScriptDraft 自报 word_count 错误时以 Tool 结果覆盖（C-05）
3. 三集同分时选择 episode_number 最小者（F-01）
4. revision_round=1 时不再进入修订（F-05）
5. 修订稿改变 locked fact 时进入 needs_manual_review（F-03）
6. LLM 两次输出非法结构时 Run 失败且无 valid Artifact（B-06）
7. Redis 清空后仍能从 workflow_events 补发 SSE（B-05）
8. 相同 Idempotency-Key 不重复创建 Run（B-05）
9. 并发创建同类 Artifact 时版本不重复（B-04）
10. 上传伪装成 DOCX 的文件被拒绝（G-03）
11. 导出失败不影响原有剧本资产（G-05）
12. checkpoint 后重试不重复生成已完成的前两集（C-07）
13. 评估报告 9 维齐全且 overall_score 由服务端计算，不被 LLM 自报带偏（E-05）
14. 低分维度（<70）必有对应 issue，缺口由服务端自动补全（E-02/E-05）
15. 低分剧本触发 need_revision → 工作流进入修订决策点（E-04）
16. 同一剧本版本重复评估复用已有报告，不产生新版本（E-03）
17. 修订后新剧本版本生成新评估，原稿评估不被覆盖（E-03）
18. 跨项目评估其他项目的 Artifact 被拒绝（E-03）

## 10. 评估（Phase E）专项说明

- **契约不变量**：`backend/tests/contract/test_evaluation_invariants.py` 对 high/medium/low 三个固定剧本 case 验证评估结构不变量（9 维齐全、overall/need_revision 服务端回填、低分维度必有 issue、FakeLLM 确定性）。
- **Golden case**：`backend/tests/golden/evaluation_cases/{high,medium,low}.json` 每个含 `expected` 字段声明预期分支（need_revision）。
- **真实模型 smoke**：`backend/scripts/evaluate_rubric_smoke.py` 对三个 case 重复调用真实 evaluator，输出各维度分均值/标准差与问题交集，用于人工诊断评估稳定性。**不进 CI**，发布前人工执行；脚本从 `.env` 读取密钥且不打印 API Key。

## 11. Agent（Phase J）专项说明（J-12）

### 11.1 评测分层

| 层 | 运行方式 | 内容 |
|---|---|---|
| CI 契约评测 | `make test`（默认） | `tests/evals/`：数据集契约（commands 240 + holdout 120 = 360 且合并分布恰为 §7.2 配额、交叉覆盖下限、ID 全局唯一、盲测原文不进 Prompt 模板、preflight 标注与真实行为一致；v1.4 期望语义——explain 属 answer 分支、plan 不得带 explain）、preflight 澄清召回 100%（14 条确定性歧义/越界/冲突用例，零模型调用）、评分器单测（伪造结果验证 target/batch 指标、失败分类与门槛判定）、Outcome 确定性规则一致率 100%（26 条） |
| 真实模型评测 | `pytest -m eval_real`（需 `EVAL_LLM_ENABLED=1` + 真实 Key，默认被 addopts 排除） | commands 联合指标（turn_type → intent → target → batch，micro/macro、混淆矩阵、失败分类）+ §3.3 发版门槛判定（EVAL_SPLIT=dev/holdout/all × EVAL_REPEATS，门槛按最差一次判定；任一不达标即非零退出；`EVAL_REPORT_ONLY=1` 只产报告）；outcomes 语义约束 goal_status 一致率；结果落盘 `tests/evals/results/`（写入 dataset 版本、git commit、Prompt 版本、模型、运行时间），报告见 `docs/AGENT_EVAL_REPORT.md`（不得写模拟数字；旧结果自动归档 `results/archive/`，新鲜度由契约测试守护） |
| E2E | `make e2e REPEAT=5` | `e2e/agent-workspace.spec.ts`（FAKE_LLM_SCENARIO=agent_e2e） |

### 11.2 CI 恢复/并发契约与用例映射

| 契约 | 用例 |
|---|---|
| Turn 幂等（同 key 复用收据 / 异载荷 409） | `tests/integration/api/test_agent_turns.py` |
| 并发确认单活跃 Run / 重复确认复用 Run / ACTION_STALE | `tests/integration/api/test_agent_actions.py` |
| lease 接管与 checkpoint 恢复 | `tests/integration/workflow/test_dispatcher_recovery.py`、`test_recovery_matrix.py` |
| 一次后续计划不重复（reconciliation 幂等 / 深度 1 不延伸） | `tests/integration/events/test_agent_action_events.py` |
| Outcome / 子提案 / GET reconcile | 同上 + `tests/unit/application/test_agent_outcome_service.py` |

### 11.3 FAKE_LLM_SCENARIO 场景

| 场景 | evaluate_episode | planner | 用途 |
|---|---|---|---|
| 默认 | 高分 | 固定 create_script 计划 | API 集成测试 |
| `revision` | 低分 | 固定 create_script | H-07 修订链路（保留） |
| `agent_e2e` | 低分 | **内容感知桩**（修改+集数→revise_script；大纲→revise_outline；评估→evaluate；解释→explain；其余→create_script），另注册 outline_reviser | J-12 Agent E2E（单后端服务全部意图） |

E2E 场景断言（`tests/integration/api/test_fake_scenario.py`）保证 fixtures 与桩行为不被无意识改动。

## 12. Memory 评测专项说明（M-01）

### 12.1 数据集与指标

- **对话集**：`backend/tests/golden/memory/dialogue_cases.json`——8 题材 × 4 长度档（24/48/96/192）= 32 组，覆盖偏好、否决、改口、未决问题、跨项目近似设定与无关闲聊；关键事实以【设定】【改口】【否决】【要求】【问题】标签嵌入可审查消息。
- **剧情集**：`backend/tests/golden/memory/story_cases.json`——10 组 × 10 集，每组含 typed delta 真值（作者事实/角色知识/伏笔/道具/关系/时间线/作者未来计划）与确定性探针。
- **四消融组**：`none` / `recent_only` / `current`（复刻生产分段摘要 + 标题摘要路径）/ `structured`（累计摘要 + typed delta 规格，即 M-02/M-03 目标）。
- **评分四层**：写入（覆盖区间连续完整）、召回（最新事实胜率、否决/约束召回、虚构、跨项目泄漏）、使用（经 `ContextBuilder` 组装后的到达率与 Manifest 一致性）、成本（相对完整历史的 token 节省）。全部确定性代码评分，不使用 LLM Judge 总分。

### 12.2 运行方式

| 层 | 命令 | 内容 |
|---|---|---|
| CI（默认） | `uv run pytest tests/evals/test_dialogue_memory_eval.py tests/evals/test_story_memory_eval.py` | 数据集契约、structured 组设计门槛（MEMORY_DESIGN §10.1/§10.2）、current 基线缺口锁定、同 seed 重跑一致、`/agent/turns` 摘要挂载探针 |
| 脚本 | `uv run python scripts/evaluate_memory.py --provider fake` | 全量跑四组，产出 `tests/evals/results/memory_eval_results.json` 与 `docs/MEMORY_EVAL_REPORT.md` |
| 真实模型 | `EVAL_LLM_ENABLED=1 uv run python scripts/evaluate_memory.py --provider real` | 对话摘要由真实 LLM 生成（生产 conversation_summary Prompt），固定子集（每长度档 2 例），记录模型/Prompt 版本/样本/调用数/token/耗时；评分仍为确定性标记匹配 |

### 12.3 结果与门槛

- 报告：`docs/MEMORY_EVAL_REPORT.md`（含 `/agent/turns` 是否实际触发摘要的生产探针结论）。
- 门槛断言（CI）：structured 组跨项目泄漏 0、最新事实 ≥95%、否决召回 ≥95%、96 条后约束召回 ≥90%、虚构 ≤1%；剧情知识越界 0、伏笔 F1 ≥90%、相对 none 违规下降 ≥50%、来源正确率 100%、重放一致。
- 基线锁定断言（CI）：current 组 96 档约束召回 <50%（分段摘要只读最新一段的缺口）——该组描述的是**历史语义**，M-02 后生产已实现累计摘要，断言保留作为消融对照。

### 12.4 M-03/M-04/M-05 用例映射（2026-09-18 更新）

| 契约 | 用例 |
|---|---|
| 累计摘要 v2（链/幂等/迁移/失败不阻断/合并/响应不等待 LLM） | `tests/integration/memory/test_summary.py` |
| 主入口阈值触发累计摘要 | `tests/integration/api/test_agent_turns.py::test_turns_trigger_cumulative_summary_at_threshold` |
| typed delta 校验与 reducer（M-01 数据集消费） | `tests/contract/test_story_state_v2.py` |
| 派生幂等/分账/跨项目/唯一索引 | `tests/integration/memory/test_story_state_service.py`、`tests/integration/db/test_migration.py::TestAlembicMigration0013` |
| 一次 5 集 vs 1+1+3 同前态；重启续写；失败保留正文；fail closed；RAG 可选 | `tests/integration/workflow/test_story_state_recovery.py` |
| 采用变化后缀 stale/前缀复用；GET 零模型调用 | `tests/integration/artifacts/test_story_state_invalidation.py` |
| strict required / PROTECTED_CONTEXT_TOO_LARGE | `tests/unit/memory/test_context_budget.py::TestStrictRequiredSections` |
| 状态面板分账展示/恢复入口/作者语言 | `frontend/tests/story-state-panel.test.tsx` |

