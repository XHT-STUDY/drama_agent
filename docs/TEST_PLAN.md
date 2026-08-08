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
