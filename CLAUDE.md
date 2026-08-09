# CLAUDE.md

本文件是 Claude Code 在本仓库工作时的操作手册。**权威开发契约见 [docs/DEV_PLAN.md](docs/DEV_PLAN.md)**,遇到与本文件冲突时以 DEV_PLAN.md 为准。

## 项目简介

DramaAgent 是一个面向中文短剧创作的对话型 Agent 系统 —— 不是单次 Prompt 生成器,而是有状态、多阶段的工作流,含记忆、检索、评估、修订、版本与导出能力。

**MVP 主路径**:Idea / Outline / TXT / DOCX → 需求归一化 → StoryBible → 10 集大纲 → 前 3 集剧本 → 逐集评估 → 自动修订最低分集(最多 1 轮)→ 连续性检查 + 重评 → 版本 Diff → Markdown / DOCX 导出。

## 文档地图

| 文档 | 用途 | 何时写 |
| --- | --- | --- |
| [docs/DEV_PLAN.md](docs/DEV_PLAN.md) | 权威开发计划、任务卡、进度总表(§13) | 每个任务改状态 / 验收时更新 §13 |
| [docs/DEV_LOG.md](docs/DEV_LOG.md) | 开发日志(做了什么 / 为什么 / 学到什么) | 每次开发或修复完成后追加 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 问题排查经验(症状 / 原因 / 解决 / 学习) | 每次 fix bug 后追加 |
| docs/PROMPT_GUIDE.md / API_CONTRACT.md / TEST_PLAN.md | 按需参考 | 相应变更时同步 |

## 开发工作流

所有工作按任务 ID(A-01 ~ I-06)推进,执行原则见 DEV_PLAN.md §0.1。核心规则:

1. 一次只做一个任务;开工前先读 §0.1、当前阶段、任务卡与依赖实现
2. 未经授权不跨模块重构、不提前实现后续阶段
3. CI 与自动化测试一律用 FakeLLM,禁止真实 LLM 调用
4. LLM 输出必须先过结构化 Pydantic v2 校验,才能写入 Artifact
5. Artifact 不可变 —— 修订必须产生新版本,绝不原地覆盖
6. 状态流转仅允许:TODO → DOING → BLOCKED → REVIEW → DONE
7. 只有任务卡验收条件**全部**满足,才能标记 DONE

### ★ 开发收尾清单(每次开发 / 修复完成后必须执行)

1. **更新进度表**:在 DEV_PLAN.md §13 更新对应任务的状态与验收证据
2. **写开发日志**:在 DEV_LOG.md 末尾按模板追加条目,必须覆盖:
   - **做了什么** —— 实现摘要、修改文件、验证命令与结果
   - **为什么这么做** —— 决策动机、备选方案取舍
   - **学到了什么** —— 可复用的经验 / 教训
3. **记录问题排查**:本次若涉及 bug fix 或疑难问题解决,必须在 TROUBLESHOOTING.md 追加一条:
   - 症状 → 产生原因 → 解决方案 → **应该学习到什么**

### Definition of Done

见 DEV_PLAN.md §0.3:新增/修改测试全绿、Ruff / mypy / 前端 lint 零新增错误、DB 变更含 Alembic migration、API 变更同步 OpenAPI 与前端类型、LLM Schema / Prompt 变更带版本号与固定样例、不覆盖既有 Artifact、验收证据写入进度表、无遗留未说明的 TODO。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python, FastAPI, Pydantic v2 |
| 工作流 | LangGraph(有状态节点、条件分支、checkpoint) |
| ORM / 迁移 | SQLAlchemy 2(async), Alembic |
| 主库 | PostgreSQL + pgvector |
| 瞬态 | Redis(SSE pub/sub、短期记忆、限流) |
| 前端 | Next.js, React, TypeScript;TanStack Query;Tailwind CSS |
| 测试 | pytest, Vitest, Playwright |
| 工具 | uv, Ruff, mypy, pnpm |

## 常用命令

```bash
make install      # 安装依赖(uv sync + pnpm install)
make lint         # Ruff + ESLint
make typecheck    # mypy + tsc
make test         # 全部测试
make up / down    # 启动 / 停止 Docker Compose(PostgreSQL + Redis)
make doctor       # 环境健康检查(DB、Redis、配置)
```

常用子集:`pytest -m unit|integration|workflow|contract`(后端按标记)、`pnpm test`(前端)。

## 架构要点

- **分层**(§3.1):Next.js 工作台 → FastAPI → Application Services → Run / Event → LangGraph Workflows → Agents + Skills → Repositories → PostgreSQL + pgvector
- **模块边界**(§4.1):`api` / `application` / `domain` / `workflows` / `agents` / `skills` / `tools` / `repositories` / `artifacts` 各模块的允许与禁止操作见 DEV_PLAN.md,未经授权不跨层
- **关键决策**(核心 5 条,其余见 §2.2):
  1. **PostgreSQL 是唯一事实源**,Redis 丢失不得造成资产损失
  2. **Artifact 不可变版本模型**;LangGraph State 只存 ID 与轻量结构,大文本放 Artifact
  3. Real LLM 与 FakeLLM 实现同一协议,自动化测试默认 FakeLLM
  4. API 立即返回 `run_id`,进度经 SSE 观察
  5. 自动修订按确定性代码选最低分集(平局取最小 `episode_number`)

## 当前进度

- **已验收**:Phase A(工程基线)、B(后端基础设施)、C(创作能力)、E(评估)、F(修订)全部 DONE
- **H 阶段全部 DONE(H-01~07)**:前端工作台 + 导出中心 + Playwright E2E 全链路闭环。`make e2e REPEAT=5` 验收通过(FakeLLM + 低分场景,隔离 postgres/redis)
- **未开始**:Phase D(RAG)、G(记忆 / 导入导出)、I(发布加固)

最新进度以 [docs/DEV_PLAN.md](docs/DEV_PLAN.md) §13 进度总表为准。

## 环境与运行

```bash
cp .env.example .env   # 按需修改,不提交 .env
make install
make up                # PostgreSQL + pgvector、Redis
make doctor
```

## 关键约束

- 集数:默认 10 集大纲 / 前 3 集剧本;前端可配置 1/2/3/5/10(非硬编码)
- MAX_REVISION_ROUNDS=1;上传 ≤ 10 MB,TXT / DOCX 仅
- API p95 < 300ms(不含 LLM 调用);首个 SSE 事件在 Run 创建后 1s 内
- 覆盖率:核心 domain / workflow / artifact ≥ 85%,后端整体 ≥ 75%
- `.env` 永不提交;`.env.example` 无真实密钥
- 函数 / 类注释用中文,解释意图而非复述代码
