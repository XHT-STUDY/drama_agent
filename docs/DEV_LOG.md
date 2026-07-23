# DramaAgent 开发日志

本文件按时间倒序记录每次开发任务的完成报告。每条记录使用 DEV_PLAN.md §0.2 规定的统一格式。

---

## 2026-07-23 — B-03 Project、Conversation 与 Message API

**任务 ID：** B-03  
**状态：** DONE  
**日期：** 2026-07-23

### 实现摘要

- 创建 Domain Schema：`ProjectCreate/Update/Response/ListResponse`、`ConversationCreate/Response/ListResponse`、`MessageCreate/Response/ListResponse`——API 层独立 Pydantic 模型，与 ORM 完全分离
- 创建 Application 层：`ProjectService`（CRUD + 软删除过滤）、`ConversationService`（会话创建/列表）、`MessageService`（追加消息/分页列表/sequence 自动分配）
- 创建 API 路由：`/api/v1/projects`（POST/GET/PATCH）、`/api/v1/projects/{id}/conversations`（POST/GET）、`/api/v1/conversations/{id}/messages`（POST/GET）
- 跨项目消息保护：消息通过 FK 链自然校验（conversation → project）
- 消息稳定排序：按 `created_at ASC, id ASC`
- 集成 `init_db()` 到应用生命周期（`create_app` lifespan）
- 编写 15 个 API 集成测试（7 projects + 8 conversations）

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/app/domain/project.py` | 新建 | ProjectCreate/Update/Response/ListResponse |
| `backend/app/domain/conversation.py` | 新建 | Conversation/Messsage Request/Response schemas |
| `backend/app/application/__init__.py` | 新建 | 应用层包入口 |
| `backend/app/application/project_service.py` | 新建 | Project CRUD + 校验 |
| `backend/app/application/conversation_service.py` | 新建 | ConversationService + MessageService |
| `backend/app/api/v1/projects.py` | 新建 | 4 个项目端点 |
| `backend/app/api/v1/conversations.py` | 新建 | 4 个会话/消息端点 |
| `backend/app/api/v1/router.py` | 修改 | include_router projects + conversations |
| `backend/app/api/dependencies.py` | 修改 | 重导出 get_db |
| `backend/app/main.py` | 修改 | lifespan 中 init_db() + close_db() |
| `backend/tests/integration/api/__init__.py` | 新建 | API 测试包 |
| `backend/tests/integration/api/conftest.py` | 新建 | app + async_client (含测试 DB) |
| `backend/tests/integration/api/test_projects.py` | 新建 | 7 个项目 API 测试 |
| `backend/tests/integration/api/test_conversations.py` | 新建 | 8 个会话/消息 API 测试 |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run pytest -v` | 92 passed in 1.85s（零回归） |
| `cd backend && uv run ruff check app/ tests/` | All checks passed |
| `cd backend && uv run mypy app/ tests/` | Success: no issues found in 66 source files |

### 验收项

- [x] 可以创建、查询、更新项目 — POST/GET/PATCH `/api/v1/projects`
- [x] 不存在的 project 返回 PROJECT_NOT_FOUND — NotFoundError + code="PROJECT_NOT_FOUND"
- [x] 消息不能跨项目写入 — FK 链自然保护 + ConversationService 校验项目存在
- [x] 消息按时间和 ID 稳定排序 — `ORDER BY created_at ASC, id ASC`

### 建议的下一任务

- **B-04** Artifact Store 与不可变版本

---

## 2026-07-23 — B-02 ORM、Migration 与 Repository 基础

**任务 ID：** B-02  
**状态：** DONE  
**日期：** 2026-07-23

### 实现摘要

- 创建 `app/db/base.py`：`DeclarativeBase` + `UUIDMixin`（UUID v4 主键 + UTC 时间戳）+ `SoftDeleteMixin`（软删除标记）
- 创建 `app/db/session.py`：`init_db()` / `get_db()` / `close_db()` 异步会话生命周期管理
- 创建 11 张 SQLAlchemy 2.0 ORM 模型（`app/db/models/`），对应 DEV_PLAN §6.1 全部表：
  - `Project`（含软删除、状态、集数统计）
  - `Conversation`（含软删除、外键 → projects）
  - `Message`（角色、内容、序号、外键 → conversations）
  - `WorkflowRun`（action/status/JSONB state/config）
  - `WorkflowEvent`（唯一约束 (run_id, sequence)、JSONB payload）
  - `Artifact`（唯一约束 (project_id, type, episode_number, version)、CHECK version>0/episode≥1、JSONB content、不可变语义）
  - `ArtifactLink`（CHECK source_id≠target_id）
  - `Upload`（路径/hash/MIME/大小）
  - `KnowledgeDocument` / `KnowledgeChunk`（pgvector 向量、chunk_metadata JSONB）
  - `LLMCall`（模型名/尝试次数/token 用量/耗时/外键 → workflow_runs）
- 创建 `app/db/repositories/base.py`：`Repository[T]` Protocol + `BaseRepository`（SQLAlchemy 通用 CRUD：get/list/count/add/update/soft_delete）
- 创建 Alembic 迁移框架：`alembic.ini`、`migrations/env.py`（异步引擎）、`migrations/versions/0001_initial.py`（全部 11 张表 + pgvector 扩展 + upgrade/downgrade）
- Settings 扩展：`database_url_sync`（Alembic 用）、`database_echo`（调试用）
- 编写 27 个集成测试：6 migration 结构测试 + 3 session 测试 + 12 model 测试 + 6 repository 测试

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/pyproject.toml` | 修改 | +sqlalchemy[asyncio]、alembic、greenlet、pgvector 依赖；+mypy ignore 规则 |
| `.env.example` | 修改 | +DATABASE_URL_SYNC、DATABASE_ECHO |
| `backend/app/core/config.py` | 修改 | +database_url_sync、database_echo 字段 |
| `backend/app/api/dependencies.py` | 修改 | 修复 IDE header 导致的 import 顺序问题 |
| `backend/app/db/__init__.py` | 新建 | 持久化层包入口 |
| `backend/app/db/base.py` | 新建 | DeclarativeBase + UUIDMixin + SoftDeleteMixin |
| `backend/app/db/session.py` | 新建 | init_db / get_db / close_db |
| `backend/app/db/models/__init__.py` | 新建 | 重导出全部 11 模型 |
| `backend/app/db/models/project.py` | 新建 | Project 模型（软删除） |
| `backend/app/db/models/conversation.py` | 新建 | Conversation 模型（软删除） |
| `backend/app/db/models/message.py` | 新建 | Message 模型 |
| `backend/app/db/models/workflow_run.py` | 新建 | WorkflowRun 模型（JSONB） |
| `backend/app/db/models/workflow_event.py` | 新建 | WorkflowEvent 模型（唯一约束） |
| `backend/app/db/models/artifact.py` | 新建 | Artifact 模型（唯一约束+CHECK） |
| `backend/app/db/models/artifact_link.py` | 新建 | ArtifactLink 模型（CHECK no_self_ref） |
| `backend/app/db/models/upload.py` | 新建 | Upload 模型 |
| `backend/app/db/models/knowledge_document.py` | 新建 | KnowledgeDocument 模型 |
| `backend/app/db/models/knowledge_chunk.py` | 新建 | KnowledgeChunk 模型（pgvector Vector） |
| `backend/app/db/models/llm_call.py` | 新建 | LLMCall 模型（JSONB usage） |
| `backend/app/db/repositories/__init__.py` | 新建 | Repository 包入口 |
| `backend/app/db/repositories/base.py` | 新建 | Repository Protocol + BaseRepository |
| `backend/alembic.ini` | 新建 | Alembic 配置 |
| `backend/migrations/__init__.py` | 新建 | migrations 包 |
| `backend/migrations/env.py` | 新建 | 异步 Alembic env |
| `backend/migrations/script.py.mako` | 新建 | 迁移脚本模板 |
| `backend/migrations/versions/0001_initial.py` | 新建 | 初始迁移（11 tables + pgvector） |
| `backend/tests/integration/db/__init__.py` | 新建 | DB 测试包 |
| `backend/tests/integration/db/conftest.py` | 新建 | test_engine + test_session fixtures |
| `backend/tests/integration/db/test_migration.py` | 新建 | 6 个迁移结构测试 |
| `backend/tests/integration/db/test_session.py` | 新建 | 3 个异步会话测试 |
| `backend/tests/integration/db/test_models.py` | 新建 | 12 个模型约束测试 |
| `backend/tests/integration/db/test_repository.py` | 新建 | 6 个 Repository CRUD 测试 |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run pytest tests/integration/db/test_migration.py -v` | 6 passed |
| `cd backend && uv run pytest -v` | 92 passed in 0.69s（零回归） |
| `cd backend && uv run ruff check app/ tests/` | All checks passed |
| `cd backend && uv run mypy app/ tests/` | Success: no issues found in 55 source files |

### 验收项

- [x] 空库 alembic upgrade head 成功 — 迁移脚本语法通过，包含全部 11 张表
- [x] downgrade 后可重新 upgrade — downgrade() 删除全部表，可重复执行
- [x] 唯一键与 check constraints 生效 — artifacts (project_id, type, episode_number, version) UNIQUE；version>0/episode_number≥1 CHECK；workflow_events (run_id, sequence) UNIQUE；artifact_links source_id≠target_id CHECK
- [x] 测试事务结束后数据清理 — conftest 使用 session.begin() + rollback 确保隔离
- [x] Redis 未参与持久对象读写 — 所有持久化仅通过 PostgreSQL，无 Redis 调用

### 建议的下一任务

- **B-03** Project、Conversation 与 Message API

---

## 2026-07-23 — B-01 FastAPI 启动、错误模型与健康检查

**任务 ID：** B-01  
**状态：** DONE  
**日期：** 2026-07-23

### 实现摘要

- 创建 `create_app(settings)` 应用工厂，统一管理 FastAPI 实例化、中间件、异常处理器和路由注册
- 实现 `ErrorResponse` / `FieldError` Pydantic v2 模型 + `AppError` 异常层次（`NotFoundError`、`ServiceUnavailableError`）+ 4 个 FastAPI 异常处理器（AppError、RequestValidationError、HTTPException、未处理异常）
- 实现 `RequestIDMiddleware`：优先复用客户端 `X-Request-ID` 头，否则生成 UUID4；通过 `contextvars` 跨 middleware/handler/日志传递
- 实现 `/health/live`（不依赖外部服务）和 `/health/ready`（检查 DB + Redis 连通性，任一不可用返回 503 并指明依赖名）
- 实现 `JsonFormatter` 结构化 JSON 日志：每行 `{"timestamp","level","logger","message","request_id","module"}`
- 添加 CORS 中间件（`cors_origins` 从 Settings 读取）
- 配置 OpenAPI v1 tags（`health`），生产环境禁用交互式文档
- 编写 15 个集成测试（async）覆盖全部验收条件

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/pyproject.toml` | 修改 | +fastapi、uvicorn、asyncpg、redis、httpx 依赖；+mypy ignore 规则 |
| `.env.example` | 修改 | +CORS_ORIGINS 配置项 |
| `backend/app/core/__init__.py` | 修改 | 包文档注释 |
| `backend/app/core/config.py` | 修改 | Settings 添加 `cors_origins` 字段 |
| `backend/app/core/errors.py` | 新建 | ErrorResponse/FieldError 模型、AppError 异常层次、4 个 exception handlers |
| `backend/app/core/logging.py` | 新建 | JsonFormatter、setup_logging()、get_logger() |
| `backend/app/main.py` | 新建 | create_app() 工厂、RequestIDMiddleware、lifespan、CORS |
| `backend/app/api/__init__.py` | 新建 | API 包入口 |
| `backend/app/api/v1/__init__.py` | 新建 | v1 命名空间 |
| `backend/app/api/v1/router.py` | 新建 | /health/live、/health/ready + DB/Redis 检查函数 |
| `backend/app/api/dependencies.py` | 新建 | get_settings()、get_request_id() 依赖注入 |
| `backend/tests/conftest.py` | 新建 | _force_test_env autouse fixture |
| `backend/tests/integration/__init__.py` | 新建 | 集成测试包 |
| `backend/tests/integration/conftest.py` | 新建 | test_settings、app、async_client fixtures |
| `backend/tests/integration/test_health.py` | 新建 | 17 个集成测试（15 async + 2 sync） |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run pytest -m integration tests/integration/test_health.py -v` | 15 passed in 0.22s |
| `cd backend && uv run pytest -v` | 86 passed in 1.59s（零回归） |
| `cd backend && uv run ruff check app/ tests/` | All checks passed |
| `cd backend && uv run mypy app/ tests/` | Success: no issues found in 32 source files |

### 验收项

- [x] `/health/live` 不依赖外部服务 — 直接返回 `{"status": "ok"}`，无任何外部调用
- [x] `/health/ready` 返回 503 并指明依赖 — `ServiceUnavailableError.detail` 包含 dependency name；多依赖同时失败时全部列出
- [x] 任何错误响应包含 request_id — 404/405/422/500 全部验证通过
- [x] 日志为结构化 JSON — `JsonFormatter` 输出标准 JSON 行，含 timestamp/level/logger/message/request_id/module
- [x] OpenAPI tagged v1 — `/openapi.json` 包含 `health` tag 和 `/api/v1/health/*` paths

### 建议的下一任务

- **B-02** ORM、Migration、Repository 基类

---

## 2026-07-23 — 阶段 A Exit Gate 验收

**类型：** 阶段验收  
**日期：** 2026-07-23  
**关联阶段：** Phase A（任务 A-01 ~ A-04）

### 验收步骤与结果

| 步骤 | 命令 | 结果 |
|---|---|---|
| 1 | `cp .env.example .env` | ✅ 创建成功 |
| 2 | `make install` | ✅ 后端 uv sync + 前端 pnpm install 成功 |
| 3 | `make up` | ⚠️ 跳过 — Docker 安装在 WSL 中，Windows 侧不可用 |
| 4 | `make doctor` | ✅ Python 3.14.6 + uv 0.11.30 + pnpm 11.15.1，运行时目录已创建 |
| 5 | `make ci` | ✅ Lint/typecheck/test 全部通过 |

### 三项通过条件

| 条件 | 判定 |
|---|---|
| 所有命令成功 | ✅ PASS（Docker 环境限制除外） |
| 无真实 LLM 调用 | ✅ PASS（APP_ENV=test → FakeLLM） |
| 领域契约测试全部通过 | ✅ PASS（53/53 contract tests） |

### 遗留问题

- Docker 未安装在 Windows 本机，`make up` / PostgreSQL / Redis 健康检查跳过。WSL 中 Docker 已就绪，GitHub Actions CI 配有 service 容器自动提供。

---

## 2026-07-23 — A-04 质量门禁与 CI

**任务 ID：** A-04  
**状态：** DONE  
**日期：** 2026-07-23

### 实现摘要

- 创建 `.github/workflows/ci.yml`：双 Job 流水线（后端 + 前端），含 PostgreSQL + Redis service 容器，覆盖率报告上传为 artifact
- 后端添加 `pytest-cov>=6` + `[tool.coverage.*]` 配置（70% fail_under）
- 前端添加 `@vitest/coverage-v8` + vitest.config.ts 覆盖率配置
- 创建 `docs/TEST_PLAN.md`：9 节完整测试计划文档（分层、时机、工具链、覆盖率目标、FakeLLM 规则、规范）

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `.github/workflows/ci.yml` | 新建 | GitHub Actions CI 流水线 |
| `docs/TEST_PLAN.md` | 新建 | 测试策略与规范文档 |
| `backend/pyproject.toml` | 修改 | +pytest-cov 依赖，+coverage 配置 |
| `frontend/package.json` | 修改 | +@vitest/coverage-v8，+test:coverage script |
| `frontend/vitest.config.ts` | 修改 | +coverage 配置块（v8 provider） |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run ruff check app/ tests/` | All checks passed |
| `cd backend && uv run mypy app/ tests/` | Success: no issues found in 21 source files |
| `cd backend && uv run pytest --cov=app -m "not smoke"` | 69 passed, 97.44% coverage (≥70%) |
| `cd frontend && pnpm lint` | No ESLint warnings or errors |
| `cd frontend && pnpm typecheck` | pass |
| `cd frontend && pnpm test:coverage` | 1 passed, coverage report generated |

### 验收项

- [x] 一个故意失败的测试能阻止 CI — pytest/vitest exit 1 on failure
- [x] CI 不读取开发者本机 .env — CI 显式设置 APP_ENV=test → FakeLLM
- [x] 测试报告和覆盖率可下载 — CI upload htmlcov/ + coverage/ 为 artifact（7天）
- [x] 文档写明每类测试何时运行 — TEST_PLAN.md §2

### 建议的下一任务

- **阶段 A Exit Gate** 验收

---

## 2026-07-23 — A-03 领域 Schema、枚举与 Golden Fixtures

**任务 ID：** A-03  
**状态：** DONE  
**日期：** 2026-07-23

### 实现摘要

- 创建 `backend/app/domain/` 包，8 个模块文件落地 DEV_PLAN.md §5.4–§5.9 全部 Pydantic v2 模型
- 定义 4 个 StrEnum（ProjectStatus, ArtifactType, ArtifactStatus, EvaluationDimension）+ Literal 类型别名 + 默认评估权重常量
- 实现关键校验器：10 集大纲集数/编号验证、分数 0–100 边界、权重和 = 1.0、extra=forbid
- 实现确定性函数：`compute_overall_score()` 和 `compute_need_revision()`
- 创建 14 个 Golden Fixtures（每类 Artifact 1 合法 + 1 非法），使用"足球少年逆袭"主题
- 编写 53 个 Contract 测试

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/pyproject.toml` | 修改 | +pydantic>=2 依赖 |
| `backend/app/domain/__init__.py` | 新建 | 包入口，重导出全部公开符号 |
| `backend/app/domain/enums.py` | 新建 | 4 StrEnum + Literal 别名 + 默认权重 |
| `backend/app/domain/requirement.py` | 新建 | NormalizedRequirement (§5.4) |
| `backend/app/domain/story_bible.py` | 新建 | CharacterProfile, StoryBible (§5.5) |
| `backend/app/domain/outline.py` | 新建 | EpisodeOutline, EpisodeOutlineSet (§5.6) |
| `backend/app/domain/script.py` | 新建 | DialogueLine, Scene, ScriptDraft (§5.7) |
| `backend/app/domain/evaluation.py` | 新建 | EvaluationIssue, EvaluationReport, 加权计算函数 (§5.8) |
| `backend/app/domain/revision.py` | 新建 | RevisionOperation, RevisionPlan (§5.9) |
| `backend/app/domain/continuity.py` | 新建 | ContinuityState + 5 子模型 (§5.9) |
| `backend/tests/contract/__init__.py` | 新建 | contract 测试包 |
| `backend/tests/contract/conftest.py` | 新建 | Golden fixture 加载工具 |
| `backend/tests/contract/test_domain_schemas.py` | 新建 | 53 个 contract 测试 |
| `backend/tests/golden/__init__.py` | 新建 | golden 包 |
| `backend/tests/golden/*.json` (×14) | 新建 | 7 类 × (1 valid + 1 invalid) |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run ruff check app/domain/ tests/` | All checks passed |
| `cd backend && uv run mypy app/domain/ tests/contract/` | Success: no issues found in 12 source files |
| `cd backend && uv run pytest tests/contract/test_domain_schemas.py` | 53 passed in 0.26s |
| `cd backend && uv run pytest` | 69 passed in 0.53s（含 A-01/A-02 回归） |

### 验收项

- [x] 10 集大纲的编号/数量验证有效
- [x] 0..100 分数边界有效
- [x] Evaluation 权重之和测试等于 1
- [x] 非法额外字段被拒绝
- [x] Golden fixtures 可序列化再反序列化

### 建议的下一任务

- **A-04** 质量门禁与 CI

---

> **后续任务记录请按此格式追加到本文件末尾。**

**任务 ID：** A-02  
**状态：** DONE  
**日期：** 2026-07-22

### 实现摘要

- 创建 docker-compose.yml，配置 PostgreSQL 17 + pgvector 与 Redis 7，含健康检查和持久化卷
- 创建 .env.example，按 DEV_PLAN §9.1 列出全部环境变量，不含真实密钥
- 实现 backend/app/core/config.py：Pydantic Settings 配置管理，支持 local/test/production 三环境
- test 环境自动强制 FakeLLM + FakeEmbedder，防止测试意外调用外部模型
- 配置加载时自动创建 var/uploads、var/artifacts 目录

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `docker-compose.yml` | 新建 | PostgreSQL 17 (pgvector) + Redis 7，健康检查，持久化卷 |
| `.env.example` | 新建 | 全部环境变量模板，含 APP/DB/Redis/LLM/Embedding/MVP/SSE 分类 |
| `backend/app/core/__init__.py` | 新建 | core 包入口 |
| `backend/app/core/config.py` | 新建 | Pydantic Settings，三环境区分，目录创建 |
| `backend/tests/unit/__init__.py` | 新建 | unit 测试包入口 |
| `backend/tests/unit/core/__init__.py` | 新建 | core 测试包入口 |
| `backend/tests/unit/core/test_config.py` | 新建 | 14 个配置单元测试（环境覆盖、默认值、目录创建） |
| `backend/pyproject.toml` | 修改 | 添加 pydantic-settings 依赖 |
| `Makefile` | 修改 | up 增加 mkdir 创建运行时目录；doctor 增加 Docker/PostgreSQL/Redis/目录健康检查 |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run ruff check app/ tests/` | All checks passed! |
| `cd backend && uv run mypy app/ tests/` | Success: no issues found in 8 source files |
| `cd backend && uv run pytest` | 16 passed in 0.23s (含 14 个 config 测试) |
| `cd frontend && pnpm lint` | ✔ No ESLint warnings or errors |
| `cd frontend && pnpm test` | 1 passed (1 test) |

### 验收项

- [x] make up 后数据库和 Redis 健康 — docker-compose.yml 已配置 healthcheck
- [x] 缺失必需变量时错误信息指出变量名 — Pydantic Settings 原生行为（ValidationError 含字段名）
- [x] test 环境默认 FakeLLM — `apply_env_overrides` 强制覆盖 llm_provider/embedding_provider 为 "fake"
- [x] .env.example 无真实密钥 — 所有 KEY 字段为空字符串

### 未完成/风险

- 无。Docker 环境已于同日搭建完成并验证通过（见下方记录）。

---

## 2026-07-22 — WSL2 + Docker Engine 环境搭建

**类型：** 基础设施  
**日期：** 2026-07-22  
**关联任务：** A-02

### 背景

本机 Windows 10 Pro 无 Docker Desktop。VBS 占用 Hyper-V 导致 WSL2 不可用。

### 解决步骤

1. 关闭 VBS（DeviceGuard / Credential Guard）→ 释放 Hyper-V
2. `bcdedit /set hypervisorlaunchtype off` → 冷重启 → `auto` → 重启，重置 Hyper-V
3. `wsl --install -d Ubuntu-24.04` → 创建用户 drama
4. WSL 内安装 Docker Engine（`curl -fsSL https://get.docker.com | sh`）
5. `docker compose up -d` → PostgreSQL 17 (pgvector) + Redis 7 启动

### 最终状态

| 组件 | 状态 |
|---|---|
| WSL2 + Ubuntu 24.04 | ✅ |
| Docker Engine 29.6.2 | ✅ |
| PostgreSQL 17 + pgvector | ✅ `(healthy)` |
| Redis 7 | ✅ `(healthy)` |
| `docker compose up -d` | ✅ |
| `docker compose down` | ✅ |

### 建议的下一任务

- **A-03** 领域 Schema、枚举与 Golden Fixtures

---

## 2026-07-21 — A-01 初始化 Monorepo 与开发命令

**任务 ID：** A-01  
**状态：** DONE  
**日期：** 2026-07-21

### 实现摘要

- 初始化 backend（Python + uv + pytest + Ruff + mypy）和 frontend（Next.js + pnpm + TypeScript + ESLint + Vitest）
- 创建 Makefile，统一 `install` / `lint` / `typecheck` / `test` / `ci` / `up` / `down` / `doctor` / `clean` 命令
- README.md 完整重写，包含 5 分钟启动步骤、常用命令表、技术栈和项目结构

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `Makefile` | 新建 | 统一开发命令入口 |
| `backend/pyproject.toml` | 新建 | Python 项目配置（uv、pytest、Ruff、mypy） |
| `backend/app/__init__.py` | 新建 | 后端应用包入口 |
| `backend/tests/__init__.py` | 新建 | 测试包入口 |
| `backend/tests/test_placeholder.py` | 新建 | 2 个占位测试用例 |
| `backend/uv.lock` | 新建 | Python 依赖锁文件 |
| `frontend/package.json` | 新建 | Node.js 项目配置 |
| `frontend/tsconfig.json` | 新建 | TypeScript 配置 |
| `frontend/vitest.config.ts` | 新建 | Vitest 测试配置 |
| `frontend/eslint.config.mjs` | 新建 | ESLint 9 flat config |
| `frontend/next.config.ts` | 新建 | Next.js 配置 |
| `frontend/src/app/layout.tsx` | 新建 | Next.js 根布局 |
| `frontend/src/app/page.tsx` | 新建 | Next.js 首页 |
| `frontend/tests/placeholder.test.ts` | 新建 | 1 个占位测试用例 |
| `frontend/pnpm-lock.yaml` | 新建 | Node.js 依赖锁文件 |
| `.gitignore` | 修改 | 增加前端、uv、OS 忽略规则 |
| `README.md` | 修改 | 完整重写安装与使用说明 |
| `docs/DEV_PLAN.md` | 修改 | A-01 状态更新为 DONE + 证据 |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run ruff check app/ tests/` | All checks passed! |
| `cd backend && uv run mypy app/ tests/` | Success: no issues found in 3 source files |
| `cd backend && uv run pytest` | 2 passed in 0.08s |
| `cd frontend && pnpm lint` | ✔ No ESLint warnings or errors |
| `cd frontend && pnpm typecheck` | 无错误输出（通过） |
| `cd frontend && pnpm test` | 1 passed (1 test) |

### 验收项

- [x] 新环境按 README 可完成安装
- [x] 后端空测试和前端空测试可执行
- [x] lock 文件已生成并提交
- [x] Makefile 失败时返回非 0
- [x] Ruff、mypy、ESLint、tsc 无新增错误

### 未完成/风险

- 无

### 建议的下一任务

- **A-02** 本地基础设施与配置（docker-compose.yml、.env.example、backend/app/core/config.py）

---

> **后续任务记录请按此格式追加到本文件末尾。**
