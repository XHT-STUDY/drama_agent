# DramaAgent

面向中文短剧创作的对话型 Agent 系统。不是单次 Prompt 生成器，而是一个具备状态、记忆、检索、评估、修订、版本与导出能力的多阶段工作流。

## 5 分钟快速启动

### 前置要求

- Python 3.11+
- Node.js 22+
- [uv](https://docs.astral.sh/uv/) — Python 包管理器
- [pnpm](https://pnpm.io/) — Node.js 包管理器
- Docker Desktop（本地运行 PostgreSQL + Redis）

### 安装与启动

```bash
# 1. 克隆仓库
git clone <repo-url> drama-agent
cd drama-agent

# 2. 复制环境变量（按需编辑）
cp .env.example .env

# 3. 安装全部依赖
make install

# 4. 启动本地基础设施
make up

# 5. 验证环境
make doctor

# 6. 运行测试
make test
```

### 常用命令

| 命令 | 说明 |
|---|---|
| `make install` | 安装全部依赖（uv + pnpm） |
| `make lint` | 代码风格检查（Ruff + ESLint） |
| `make typecheck` | 类型检查（mypy + tsc） |
| `make test` | 运行全部测试（pytest + Vitest） |
| `make cov` | 覆盖率门禁（总体 ≥75% + 核心 domain/workflows/artifacts ≥85%） |
| `make perf` | 性能测试（需 `make up`；普通 API p95 / 并发 SSE / 1000 Artifact） |
| `make ci` | CI 流水线（lint + typecheck + 覆盖率门禁） |
| `make e2e` | Playwright 端到端全链路（`make e2e REPEAT=5` 连续 5 次验收） |
| `make up` | 启动 PostgreSQL + Redis |
| `make down` | 停止 PostgreSQL + Redis |
| `make doctor` | 检查开发环境健康状态 |

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python、FastAPI、Pydantic v2 |
| 工作流 | LangGraph |
| ORM/迁移 | SQLAlchemy 2、Alembic |
| 数据库 | PostgreSQL + pgvector |
| 缓存 | Redis |
| 前端 | Next.js、React、TypeScript |
| 样式 | Tailwind CSS |
| 测试 | pytest、Vitest、Playwright |
| 工程 | uv、Ruff、mypy、pnpm |

## 项目结构

```
drama-agent/
├── backend/          # Python 后端服务
│   ├── app/          # 应用代码
│   └── tests/        # 后端测试
├── frontend/         # Next.js 前端工作台
│   ├── src/          # 源码
│   └── tests/        # 前端测试
├── docs/             # 项目文档（DEV_PLAN.md 是开发依据）
├── knowledge/        # 短剧知识库素材
├── e2e/              # Playwright 端到端测试
├── Makefile          # 统一开发命令
├── docker-compose.yml
└── .env.example
```

## 开发指南

本项目采用任务驱动开发，所有工作按 [docs/DEV_PLAN.md](docs/DEV_PLAN.md) 中定义的任务 ID（A-01 到 I-06）组织。

每个任务包含：
- 预估工时与依赖关系
- 需修改的文件清单
- 具体实现要求
- 验收条件（checkbox）
- 测试命令

开发时请遵循 DEV_PLAN.md §0.1 中的执行原则，每次只实现一个任务 ID。

## 当前状态

Phase A ~ Phase I 全部完成（MVP 发布候选 v0.1.0-rc1）。

| 阶段 | 任务 | 状态 |
|---|---|---|
| A | 工程基线 | ✅ DONE |
| B | 核心基础设施 | ✅ DONE |
| C | 创作链路 | ✅ DONE |
| D | RAG 检索（MVP 之外，backlog） | ⏸ 未开始 |
| E | 评估 | ✅ DONE |
| F | 修订 | ✅ DONE |
| G | 记忆 / 导入 / 导出 | ✅ DONE |
| H | 前端工作台 + E2E | ✅ DONE |
| I | 稳定性 / 可观测 / 安全 / 扩展 / 发布 | ✅ DONE |

**最新验证**（2026-08-16，见 [docs/TEST_REPORT.md](docs/TEST_REPORT.md)）：后端全量 **974 passed / 0 failed**；总体覆盖率 **88%**（≥75%）、核心（domain/workflows/artifacts）**92%**（≥85%）；性能 p95 达标（普通 API 28–31ms / 100 并发 SSE 702ms / 1000 Artifact 45ms）；`make e2e REPEAT=5` **5 次连续通过**。

### 已交付能力总览

- **MVP 主链路**：Idea / Outline / TXT / DOCX → 需求归一化 → StoryBible → 10 集大纲 → 前 3 集剧本 → 逐集评估 → 自动修订最低分集（最多 1 轮）→ 连续性检查 + 重评 → 版本 Diff → Markdown / DOCX 导出
- **记忆与检索**：短期 / 中期 / 项目记忆 + Context Builder；RAG 检索（Phase D 预留，MVP 外）
- **稳定与成本保护（I-01）**：LLM 统一重试（429 / timeout / 5xx 退避 + Retry-After）、per-run 调用数 / Token 预算（软 / 硬上限）、协作式取消、失败从 checkpoint 恢复（不重调已完成节点）、所有失败带 `error_code`
- **可观测（I-02）**：进程内 Prometheus 指标 `GET /metrics`、Run 诊断接口、日志脱敏
- **安全（I-03）**：Prompt 注入内容边界隔离、上传 / 存储 / 导出路径与归属防护、输出转义、CORS 配置
- **扩展（I-04）**：Skill 插件契约 + MCP 外部工具适配（默认关闭，主流程零影响），见 [docs/EXTENSIONS.md](docs/EXTENSIONS.md)
- **前端工作台（H）**：项目列表 → 创作 → SSE 进度 → 工作台 → 修订 → Diff → 导出中心全链路

详见 [docs/DEV_PLAN.md](docs/DEV_PLAN.md) §13 进度总表与 [docs/DEV_LOG.md](docs/DEV_LOG.md) 开发日志。
