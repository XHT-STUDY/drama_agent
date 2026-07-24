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

## 当前状态

| 阶段 | 任务 | 状态 | 测试 |
|---|---|---|---|
| A | A-01 ~ A-04 工程基线 | ✅ DONE | 69 tests, 97.44% coverage |
| B | B-01 ~ B-07 核心基础设施 | ✅ DONE | 129 tests |
| C | C-01 ~ C-05 创作链路 | ✅ DONE | 219 tests (含 104 Phase C tests) |
| C | C-06 ~ C-08 创作链路 | 🔲 TODO | — |
| D ~ I | 后续阶段 | 🔲 TODO | — |

**最近完成** (2026-07-24): C-01 Prompt Loader → C-05 Episode Writer，详见 [docs/DEV_LOG.md](docs/DEV_LOG.md)。

### 已交付的 Phase C 能力

- **Prompt 管理系统**: 6 个版本化模板，Manifest + PromptLoader + Schema 注册
- **需求归一化**: Idea/Outline/TXT/DOCX → NormalizedRequirement，关键信息缺失自动阻断
- **StoryBible 生成**: 完整故事宝典（世界观/人物/规则/伏笔），含质量门禁校验
- **分集大纲**: 一次生成 10 集，含四要素（开头/冲突/爽点/钩子）+ 角色引用检查
- **剧本写作**: Scene/DialogueLine + plain_text，WordCountTool/DialogueRatioTool 指标覆盖
- **CreationAgent**: 统一创作入口，组合 Skill + BaseAgent + PromptLoader
