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
| `make ci` | CI 流水线（lint + typecheck + test） |
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
