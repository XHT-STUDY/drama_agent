# DramaAgent AI Coding 项目开发执行文档

> 文档版本：v1.5  
> 编制日期：2026-08-16  
> 项目阶段：MVP — Phase A~I 全部完成（I-01~06：韧性/可观测/安全/MCP 扩展/性能与回归/交付文档与 RC 发布），发布候选 **v0.1.0-rc1**；Phase D（RAG）为 MVP 之外 backlog  
> 依据文档：《DramaAgent 项目开发计划》  
> 适用对象：产品负责人、后端/前端开发者、测试人员、AI Coding Agent

---

## 0. 文档目的与使用方法

本文件不是产品介绍，而是 DramaAgent MVP 的“实施合同”。它把原设计中的模块和 8 周计划，进一步拆成可独立编码、测试、验收和追踪的任务。

仓库初始化后，应将本文件放到 docs/DEV_PLAN.md；后续 AI Coding 提示中的 DEV_PLAN.md 均指本文件。

### 0.1 AI Coding 的执行原则

1. 每次只向 AI Coding Agent 下发一个任务 ID，例如 A-01 或 C-04。
2. 开始任务前，先让 Agent 阅读：
   - 本文档的“全局技术约束”；
   - 当前阶段说明；
   - 当前任务卡；
   - 任务依赖项的实现与测试。
3. 未经任务卡授权，不进行跨模块重构，不顺手实现后续阶段。
4. 每个任务必须同时提交：
   - 实现代码；
   - 单元测试或集成测试；
   - 必要的配置/迁移；
   - 文档或接口契约更新；
   - 可复现的验证命令及结果。
5. 任务状态只允许使用：TODO、DOING、BLOCKED、REVIEW、DONE。
6. 只有任务卡中的验收条件全部满足，任务才可标记为 DONE。
7. 真实 LLM 调用不能作为自动化测试的唯一依据；CI 默认使用确定性的 FakeLLM。
8. 所有 LLM 输出必须先通过结构化 Schema 校验，校验失败不得写入正式 Artifact。
9. 所有正式 Artifact 不可原地覆盖；修订必须产生新版本。
10. 每完成一个任务（或一次修复、一次非计划性开发），都必须做三件事：
    1. 更新第 13 节的进度总表（状态 + 验收证据）；
    2. 在 docs/DEV_LOG.md 末尾追加开发日志，至少覆盖：做了什么、**为什么这么做**、**学到了什么**、验证命令与结果；
    3. 涉及 bug fix / 疑难问题解决时，在 docs/TROUBLESHOOTING.md 追加：症状、产生原因、解决方案、**应该学习到什么**。

### 0.2 单任务交付格式

AI Coding Agent 完成任务后，统一返回：

~~~text
任务 ID：
状态：DONE / BLOCKED

实现摘要：
- ...

为什么这么做：
- ...（决策动机、备选方案取舍）

修改文件：
- ...

验证结果：
- 命令：
- 结果：

学习收获：
- ...（可复用的经验 / 教训）

验收项：
- [x] ...

未完成/风险：
- 无 / ...

建议的下一任务：
- ...
~~~

### 0.3 完成定义（Definition of Done）

一个任务同时满足以下条件才算完成：

- 代码实现与任务边界一致；
- 新增或修改的测试全部通过；
- Ruff、类型检查、前端 lint 无新增错误；
- 数据库变更包含 Alembic migration；
- API 变更同步 OpenAPI 与前端类型；
- LLM Schema/Prompt 变更包含版本号与固定样例；
- 日志中不出现 API Key、上传全文或完整 Prompt；
- 没有覆盖既有 Artifact；
- 验收证据已写入进度表；
- 没有遗留未说明的 TODO。

---

## 1. 项目目标与范围

### 1.1 项目定位

DramaAgent 是一个面向中文短剧创作的对话型 Agent 系统。它不是单次 Prompt 生成器，而是一个具备状态、记忆、检索、评估、修订、版本与导出能力的多阶段工作流。

### 1.2 MVP 唯一主路径

~~~text
Idea / Outline / TXT / DOCX
  -> 需求归一化
  -> StoryBible
  -> 10 集分集大纲
  -> 前 3 集完整剧本
  -> 逐集评估
  -> 选择最低分的 1 集自动修订
  -> 连续性检查与重新评估
  -> 新旧版本 Diff
  -> Markdown / DOCX 导出
~~~

### 1.3 MVP 范围

| 能力 | MVP 目标 |
| --- | --- |
| 输入 | Idea、Outline、TXT、DOCX |
| StoryBible | 1 份结构化设定 |
| 分集规划 | 10 集结构化大纲 |
| 正文生成 | 前 3 集完整剧本 |
| 自动评估 | 3 集，9 个维度 |
| 自动修订 | 最低分的 1 集，最多 1 轮 |
| 版本 | 原稿和修订稿均保留，可查看 Diff |
| 记忆 | 最近对话、项目资产、连续性状态 |
| 检索 | 内部短剧知识库 top-k 检索 |
| 导出 | Markdown、DOCX |
| 进度 | SSE 流式展示节点进度 |
| 恢复 | 工作流状态持久化，可从失败节点重试 |

### 1.4 MVP 不做

- 50 集完整正文生成；
- 多人实时协同编辑；
- 自动生成视频；
- 外部视频平台对接；
- 自由自治的多 Agent 群聊；
- 复杂权限/RBAC；
- 在线计费；
- 自动抓取并存储受版权保护的完整剧本；
- 依赖 MCP 才能运行的核心能力；
- 自动发布或对外分享剧本。

### 1.5 MVP 成功标准

以“足球少年逆袭”固定 Demo 为基准：

1. 从创建项目到导出，全流程无需手工修改数据库；
2. 成功生成 1 份 StoryBible、10 集大纲和 3 集正文；
3. 3 集均生成合法 EvaluationReport；
4. 系统按确定规则选择最低分集，且只修订该集一次；
5. 修订不覆盖原稿，能够展示文本 Diff；
6. 修订后通过 locked facts 与连续性检查；
7. 断开 SSE 后可重新连接并补收事件；
8. 任一节点失败后，Run 可定位失败原因并安全重试；
9. 可导出 Markdown 和 DOCX；
10. 使用 FakeLLM 的端到端测试稳定通过。

### 1.6 非功能指标

| 类别 | MVP 指标 |
| --- | --- |
| API 响应 | 不含 LLM 的普通 API，测试环境 p95 小于 300 ms |
| 首事件延迟 | 创建 Run 后 1 秒内产生 run.started 事件 |
| 事件顺序 | 同一 Run 的 sequence 严格单调递增 |
| 幂等性 | 相同 Idempotency-Key 不创建重复 Run |
| 可恢复性 | 节点失败后可从最近 checkpoint 继续 |
| 数据安全 | API Key 不落库、不入日志、不返回前端 |
| 文件限制 | TXT/DOCX 单文件默认不超过 10 MB |
| 自动修订 | MVP 最多 1 轮，避免无限循环 |
| 测试覆盖 | 核心领域、Artifact、Workflow 行覆盖率不低于 85%；后端总体不低于 75% |
| 可观测性 | 每个请求和 Run 都有 request_id / run_id |

---

## 2. 已锁定的技术决策

### 2.1 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 后端 | Python、FastAPI、Pydantic v2 | API、Schema、依赖注入 |
| 工作流 | LangGraph | 有状态节点、条件分支、checkpoint |
| ORM/迁移 | SQLAlchemy 2、Alembic | 数据模型和版本迁移 |
| 主数据库 | PostgreSQL | 项目、消息、Run、Artifact 的唯一事实源 |
| 向量检索 | pgvector | MVP 不再额外维护独立 Vector DB |
| 临时状态 | Redis | SSE 发布、短期记忆、限流；不可作为唯一事实源 |
| 对象文件 | 本地文件目录 | MVP 存上传文件与导出文件，接口预留对象存储 |
| 后台执行 | 进程内 Worker 抽象 | MVP 单机可运行；接口允许后续替换为任务队列 |
| 前端 | Next.js、React、TypeScript | 项目工作台与版本对比 |
| 前端状态 | TanStack Query + 局部状态 | 服务端数据缓存与刷新 |
| 样式 | Tailwind CSS | 快速构建统一 UI |
| 测试 | pytest、Vitest、Playwright | 单元、集成、E2E |
| 工程工具 | uv、Ruff、mypy、pnpm | 锁定依赖与质量门禁 |
| 本地环境 | Docker Compose | PostgreSQL、Redis 一键启动 |

依赖的具体小版本由 lock 文件固定，不在业务代码中依赖未锁定的“最新版本”行为。

### 2.2 架构决策

1. PostgreSQL 是持久状态的唯一事实源。Redis 丢失不能导致项目资产丢失。
2. pgvector 与业务库共用 PostgreSQL，降低 MVP 运维复杂度。
3. Artifact 采用不可变版本模型。更新 StoryBible、剧本或评估报告都创建新记录。
4. LangGraph State 只存 ID、轻量结构和当前执行状态；大文本存 Artifact，避免 checkpoint 膨胀。
5. BaseAgent 只负责通用调用、校验、重试和追踪；具体业务写在 Skill 中。
6. Orchestrator 负责选择确定的工作流，不让多个 Agent 自由对话决定控制流。
7. 真实 LLM 与 FakeLLM 实现同一协议；所有自动测试默认 FakeLLM。
8. Prompt 是版本化代码资产。每次生成记录 prompt_version、model、参数和输入 Artifact ID。
9. API 创建长任务后立即返回 run_id；生成过程通过 SSE 观察。
10. 自动修订由确定性代码选集：overall_score 最低；并列时 episode_number 最小。
11. 评估分数只是一项信号。修订验收还必须检查结构、连续性、locked facts 和合规风险。
12. MCP 在 MVP 只实现 Adapter/Registry 契约，内部工具不依赖 MCP 运行。

### 2.3 模型调用策略

| 角色 | 默认用途 | 必须输出 |
| --- | --- | --- |
| normalizer | 需求归一化、文件分类 | NormalizedRequirement / ImportClassification |
| planner | StoryBible、分集大纲 | StoryBible / EpisodeOutlineSet |
| writer | 单集正文、修订 | ScriptDraft / RevisedScript |
| evaluator | 评分、问题定位、修订计划 | EvaluationReport / RevisionPlan |
| summarizer | 会话与单集摘要 | ConversationSummary / EpisodeSummary |
| embedding | 知识库向量 | float vector |

通用约束：

- temperature、max_tokens、timeout 从配置读取；
- 每次调用带 trace_id、project_id、run_id、node_name；
- 结构化输出最多重试 2 次；
- 第一次失败使用原 Prompt 重试，第二次失败附加精简的 Schema 错误；
- 仍失败则节点失败，不写入正式 Artifact；
- 单节点默认超时 180 秒；
- MVP 单次完整 Demo 的模型调用软上限为 18 次；
- 任何重试都写入 llm_call_attempt 追踪信息。

---

## 3. 系统架构与数据流

### 3.1 逻辑架构

| 顺序 | 层 | 下游 | 主要职责 |
| ---: | --- | --- | --- |
| 1 | Next.js 工作台 | FastAPI API | 用户交互、进度与资产展示 |
| 2 | FastAPI API | Application Services | 参数、鉴权边界、错误契约 |
| 3 | Application Services | Run Service / Repositories | 用例编排与事务边界 |
| 4 | Run / Event Service | LangGraph Workflows | 异步执行、事件、恢复 |
| 5 | LangGraph Workflows | Agents + Skills | 状态节点与条件分支 |
| 6 | Agents + Skills | LLM / RAG / Tools / Memory | 单一业务能力 |
| 7 | Repositories | PostgreSQL + pgvector | 持久状态与向量检索 |

旁路依赖：Run Service 使用 Redis 做实时通知；Application Services 使用 Local File Store 保存上传和导出文件。两者都不替代 PostgreSQL 中的事实记录。

### 3.2 主创建数据流

1. 前端调用 POST /api/v1/projects 创建项目。
2. 前端调用 POST /api/v1/projects/{project_id}/runs，action=create_script。
3. API 校验请求与 Idempotency-Key，创建 WorkflowRun，返回 run_id。
4. Worker 启动 creation_workflow，并发出 run.started。
5. normalize 节点生成 NormalizedRequirement Artifact。
6. retrieve 节点检索知识片段，只将 chunk ID 和摘要放入 State。
7. story_bible 节点生成 StoryBible Artifact。
8. outline 节点一次生成并校验 10 集 EpisodeOutline Artifact。
9. writer 节点按第 1、2、3 集顺序生成 ScriptDraft；每集完成后更新 ContinuityState。
10. evaluator 节点评估 3 集，生成 3 份 EvaluationReport。
11. selector 以确定规则选最低分剧本。
12. reviser 创建 RevisionPlan 与新版本 ScriptDraft。
13. continuity_check 验证 locked facts、人物状态与必要事件。
14. re_evaluator 重新评分，生成与修订版本绑定的新 EvaluationReport。
15. workflow 保存最终 checkpoint，发出 run.completed。
16. 用户在版本对比页查看 Diff，并按需调用导出接口。

### 3.3 失败与恢复

- 每个节点开始前写 node.started，结束后写 node.completed；
- 节点抛出可重试错误时，由统一重试策略处理；
- 重试耗尽后写 node.failed 和 run.failed；
- 已创建的 Artifact 不回滚、不删除；
- 恢复时从最近 checkpoint 继续，节点必须通过输入哈希保证幂等；
- 如果同一输入 Artifact、prompt_version、model_config 已产生成功输出，可直接复用；
- 用户取消 Run 时只停止后续节点，不删除已生成资产；
- 前端断线后使用 Last-Event-ID 重连 SSE，服务端从 PostgreSQL 补发缺失事件。

---

## 4. 仓库目录与模块边界

~~~text
drama-agent/
├── README.md
├── CHANGELOG.md
├── Makefile
├── .env.example
├── .gitignore
├── .github/
│   └── workflows/
│       └── ci.yml
├── docker-compose.yml
├── docker-compose.e2e.yml
├── docs/
│   ├── DEV_PLAN.md
│   ├── API_CONTRACT.md
│   ├── PROMPT_GUIDE.md
│   ├── TEST_PLAN.md
│   ├── OPERATIONS.md
│   ├── SECURITY.md
│   ├── EXTENSIONS.md
│   ├── DEMO.md
│   ├── TEST_REPORT.md
│   ├── KNOWN_LIMITATIONS.md
│   └── adr/
│       ├── 0001-artifact-immutability.md
│       ├── 0002-pgvector.md
│       └── 0003-run-event-model.md
├── backend/
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── alembic.ini
│   ├── migrations/
│   ├── scripts/
│   │   └── evaluate_rubric_smoke.py
│   ├── app/
│   │   ├── main.py
│   │   ├── cli/
│   │   │   └── knowledge.py
│   │   ├── api/
│   │   │   ├── dependencies.py
│   │   │   └── v1/
│   │   │       ├── router.py
│   │   │       ├── projects.py
│   │   │       ├── conversations.py
│   │   │       ├── runs.py
│   │   │       ├── artifacts.py
│   │   │       ├── evaluations.py
│   │   │       ├── revisions.py
│   │   │       ├── uploads.py
│   │   │       └── exports.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── errors.py
│   │   │   ├── logging.py
│   │   │   ├── ids.py
│   │   │   └── security.py
│   │   ├── domain/
│   │   │   ├── enums.py
│   │   │   ├── project.py
│   │   │   ├── conversation.py
│   │   │   ├── artifact.py
│   │   │   ├── requirement.py
│   │   │   ├── story_bible.py
│   │   │   ├── outline.py
│   │   │   ├── script.py
│   │   │   ├── evaluation.py
│   │   │   ├── revision.py
│   │   │   ├── continuity.py
│   │   │   ├── context.py
│   │   │   └── retrieval.py
│   │   ├── db/
│   │   │   ├── session.py
│   │   │   ├── models/
│   │   │   └── repositories/
│   │   ├── application/
│   │   │   ├── project_service.py
│   │   │   ├── conversation_service.py
│   │   │   ├── run_service.py
│   │   │   ├── artifact_service.py
│   │   │   ├── evaluation_service.py
│   │   │   ├── revision_service.py
│   │   │   └── export_service.py
│   │   ├── llm/
│   │   │   ├── protocol.py
│   │   │   ├── client.py
│   │   │   ├── fake.py
│   │   │   ├── structured_output.py
│   │   │   └── models.py
│   │   ├── agents/
│   │   │   ├── base.py
│   │   │   ├── creation.py
│   │   │   ├── evaluation.py
│   │   │   └── revision.py
│   │   ├── skills/
│   │   │   ├── protocol.py
│   │   │   ├── registry.py
│   │   │   ├── requirement.py
│   │   │   ├── story_bible.py
│   │   │   ├── outline.py
│   │   │   ├── episode_writer.py
│   │   │   ├── evaluator.py
│   │   │   ├── revision_plan.py
│   │   │   ├── reviser.py
│   │   │   ├── summarizer.py
│   │   │   └── import_classifier.py
│   │   ├── workflows/
│   │   │   ├── state.py
│   │   │   ├── router.py
│   │   │   ├── creation.py
│   │   │   ├── evaluation.py
│   │   │   ├── revision.py
│   │   │   ├── import_file.py
│   │   │   ├── nodes/
│   │   │   └── checkpoint.py
│   │   ├── tools/
│   │   │   ├── protocol.py
│   │   │   ├── registry.py
│   │   │   ├── word_count.py
│   │   │   ├── dialogue_ratio.py
│   │   │   ├── script_structure.py
│   │   │   ├── continuity_check.py
│   │   │   ├── diff.py
│   │   │   ├── file_parser.py
│   │   │   └── exporters/
│   │   ├── rag/
│   │   │   ├── loader.py
│   │   │   ├── chunker.py
│   │   │   ├── embedder.py
│   │   │   ├── retriever.py
│   │   │   └── models.py
│   │   ├── memory/
│   │   │   ├── short_term.py
│   │   │   ├── summary.py
│   │   │   ├── continuity.py
│   │   │   └── context_builder.py
│   │   ├── artifacts/
│   │   │   ├── store.py
│   │   │   ├── versions.py
│   │   │   └── diff_service.py
│   │   ├── prompts/
│   │   │   ├── loader.py
│   │   │   ├── manifest.yaml
│   │   │   └── templates/
│   │   ├── events/
│   │   │   ├── schemas.py
│   │   │   ├── publisher.py
│   │   │   └── stream.py
│   │   ├── observability/
│   │   │   ├── metrics.py
│   │   │   └── tracing.py
│   │   ├── storage/
│   │   │   ├── protocol.py
│   │   │   └── local.py
│   │   └── integrations/
│   │       └── mcp/
│   │           ├── protocol.py
│   │           └── adapter.py
│   └── tests/
│       ├── unit/
│       ├── integration/
│       ├── contract/
│       ├── workflow/
│       ├── security/
│       ├── performance/
│       ├── fixtures/
│       └── golden/
├── frontend/
│   ├── package.json
│   ├── pnpm-lock.yaml
│   ├── src/
│   │   ├── app/
│   │   ├── components/
│   │   ├── features/
│   │   ├── lib/
│   │   ├── hooks/
│   │   └── types/
│   └── tests/
├── knowledge/
│   ├── README.md
│   ├── rubric/
│   ├── templates/
│   ├── hooks/
│   └── examples/
└── e2e/
    ├── fixtures/
    └── dramaagent.spec.ts
~~~

### 4.1 模块职责约束

| 模块 | 可以做 | 不可以做 |
| --- | --- | --- |
| api | 参数解析、鉴权、调用 application service | 直接调用 LLM、直接写 ORM |
| application | 用例编排、事务边界 | 保存 Prompt 模板、实现数据库细节 |
| domain | Schema、枚举、纯规则 | 网络、数据库、LLM 调用 |
| workflows | 节点连接、状态跳转、恢复 | 承载长篇 Prompt 和 SQL |
| agents | 组合通用 Skill、提供业务角色入口 | 自由决定主工作流 |
| skills | 单一可复用任务、组装上下文与输出 Schema | 直接操作前端或 HTTP |
| tools | 确定性能力，如统计、解析、Diff | 隐式调用 LLM |
| repositories | 数据持久化 | 业务评分和控制流 |
| artifacts | 不可变版本、依赖和 Diff | 修改历史版本 |
| memory | 记忆读取、压缩、上下文预算 | 成为项目资产唯一存储 |
| rag | 文档摄取、向量化、检索 | 未授权抓取外部内容 |

---

## 5. 核心领域模型与数据契约

### 5.1 通用规则

- ID 使用 UUID；
- 时间使用带时区 UTC，API 输出 ISO 8601；
- 所有 Schema 设置 extra=forbid；
- 所有写接口使用明确的 Request/Response Schema；
- JSONB 业务内容必须先通过对应 Pydantic Schema；
- episode_number 从 1 开始；
- 分数范围统一为 0 到 100；
- Artifact 的 content_schema_version 与 prompt_version 分开管理；
- 所有列表字段显式给出空数组，不返回 null；
- 正文允许 Markdown 文本，但不得在服务端执行其中的 HTML/脚本。

### 5.2 Project

~~~python
class Project:
    id: UUID
    title: str
    genre: str | None
    status: ProjectStatus
    target_episode_count: int = 50
    mvp_outline_count: int = 10
    mvp_script_count: int = 3
    current_story_bible_artifact_id: UUID | None
    created_at: datetime
    updated_at: datetime
~~~

ProjectStatus：draft、planning、writing、evaluating、revising、completed、archived。

### 5.3 Artifact

~~~python
class Artifact:
    id: UUID
    project_id: UUID
    type: ArtifactType
    version: int
    episode_number: int | None
    status: ArtifactStatus
    content: dict
    content_schema_version: str
    created_by: str
    source_artifact_ids: list[UUID]
    workflow_run_id: UUID | None
    prompt_name: str | None
    prompt_version: str | None
    model_name: str | None
    model_parameters: dict
    input_hash: str
    checksum: str
    created_at: datetime
~~~

ArtifactType 至少包含：

- normalized_requirement
- story_bible
- episode_outline_set
- script_draft
- evaluation_report
- revision_plan
- continuity_state
- conversation_summary
- import_classification
- export_file

约束：

- 唯一键建议为 project_id + type + episode_number + version；
- version 由数据库事务内计算；
- Artifact 内容不可 UPDATE；状态只允许 draft -> valid 或 draft -> invalid；
- 新版本通过 source_artifact_ids 指向旧版本和其他依据；
- 正式读取默认返回最新 valid 版本，可用 version 显式查询历史。

### 5.4 NormalizedRequirement

~~~python
class NormalizedRequirement:
    title: str
    logline: str
    genre: str
    tone: list[str]
    audience: str | None
    target_episode_count: int
    episode_duration_seconds: int
    protagonist_seed: str
    conflict_seed: str
    must_have: list[str]
    must_avoid: list[str]
    source_type: Literal["idea", "outline", "txt", "docx"]
    assumptions: list[str]
    open_questions: list[str]
~~~

MVP 可在有非关键 open_questions 时继续，但必须把 assumptions 返回给用户；缺少题材、主角或核心冲突时阻止自动生成。

### 5.5 StoryBible

~~~python
class CharacterProfile:
    character_id: str
    name: str
    role: str
    age_range: str | None
    visible_goal: str
    hidden_need: str | None
    traits: list[str]
    strengths: list[str]
    flaws: list[str]
    relationship_notes: list[str]
    forbidden_changes: list[str]

class StoryBible:
    title: str
    logline: str
    genre: str
    tone: list[str]
    world_setting: str
    protagonist: CharacterProfile
    antagonist: CharacterProfile
    supporting_characters: list[CharacterProfile]
    main_conflict: str
    stakes: str
    story_rules: list[str]
    long_term_payoffs: list[str]
    open_loops: list[str]
    locked_facts: list[str]
    compliance_notes: list[str]
~~~

### 5.6 EpisodeOutlineSet

~~~python
class EpisodeOutline:
    episode_number: int
    title: str
    opening_hook: str
    objective: str
    core_conflict: str
    key_events: list[str]
    payoff: str
    ending_hook: str
    next_bridge: str
    introduced_loops: list[str]
    resolved_loops: list[str]
    required_characters: list[str]

class EpisodeOutlineSet:
    episodes: list[EpisodeOutline]
    arc_summary: str
    validation_notes: list[str]
~~~

校验器必须保证：正好 10 集、编号为 1..10、编号不重复、opening_hook / core_conflict / ending_hook 非空、每集至少 2 个 key_events。

### 5.7 ScriptDraft

~~~python
class DialogueLine:
    speaker: str
    text: str
    parenthetical: str | None

class Scene:
    scene_number: int
    location: str
    time_of_day: str
    characters: list[str]
    action: str
    dialogue: list[DialogueLine]

class ScriptDraft:
    episode_number: int
    title: str
    opening_hook: str
    scenes: list[Scene]
    ending_hook: str
    plain_text: str
    word_count: int
    dialogue_ratio: float
    referenced_outline_artifact_id: UUID
~~~

word_count 与 dialogue_ratio 由确定性 Tool 计算，不能信任 LLM 自报数值。

### 5.8 EvaluationReport

九个维度及默认权重：

| 维度 | key | 权重 |
| --- | --- | ---: |
| 开头钩子 | opening_hook | 0.15 |
| 主线清晰度 | main_clarity | 0.10 |
| 人设吸引力 | character_appeal | 0.10 |
| 冲突强度 | conflict_intensity | 0.15 |
| 爽点密度 | payoff_density | 0.15 |
| 集尾钩子 | ending_hook | 0.15 |
| 节奏控制 | pacing | 0.10 |
| 视频化程度 | visualizability | 0.05 |
| 合规安全 | compliance_safety | 0.05 |
| 合计 |  | 1.00 |

~~~python
class EvaluationIssue:
    issue_id: str
    dimension: EvaluationDimension
    severity: Literal["low", "medium", "high"]
    scene_number: int | None
    evidence: str
    diagnosis: str
    suggestion: str

class EvaluationReport:
    episode_number: int
    script_artifact_id: UUID
    rubric_version: str
    dimension_scores: dict[EvaluationDimension, int]
    overall_score: float
    strengths: list[str]
    issues: list[EvaluationIssue]
    revision_suggestions: list[str]
    need_revision: bool
    risk_flags: list[str]
~~~

overall_score 由服务端按权重计算，不采用 LLM 自报总分。need_revision 默认规则：

- overall_score 小于 75；或
- 任一 high 问题；或
- compliance_safety 小于 60。

### 5.9 RevisionPlan 与连续性

~~~python
class RevisionOperation:
    operation_id: str
    target_scene_number: int | None
    issue_ids: list[str]
    instruction: str
    preserve: list[str]
    expected_effect: str

class RevisionPlan:
    episode_number: int
    source_script_artifact_id: UUID
    source_evaluation_artifact_id: UUID
    operations: list[RevisionOperation]
    locked_facts: list[str]
    max_change_ratio: float

class EpisodeSummary:
    episode_number: int
    summary: str
    key_events: list[str]
    ending_state: str

class StoryLoop:
    loop_id: str
    description: str
    introduced_episode: int
    resolved_episode: int | None
    status: Literal["open", "resolved"]

class CharacterState:
    character_id: str
    physical_state: str | None
    emotional_state: str | None
    current_goal: str
    known_information: list[str]
    last_updated_episode: int

class RelationshipChange:
    from_character_id: str
    to_character_id: str
    episode_number: int
    before: str
    after: str

class TimelineEvent:
    event_id: str
    episode_number: int
    order_in_episode: int
    description: str

class ContinuityState:
    through_episode: int
    episode_summaries: list[EpisodeSummary]
    open_loops: list[StoryLoop]
    resolved_loops: list[StoryLoop]
    locked_facts: list[str]
    character_states: dict[str, CharacterState]
    relationship_changes: list[RelationshipChange]
    timeline_events: list[TimelineEvent]
~~~

修订质量门禁：

- 原 ScriptDraft 保留；
- 新稿 episode_number 不变，version 增加；
- locked_facts 不丢失、不反转；
- 大纲中的 required events 不被删除；
- 新稿解析成功；
- 高风险合规项不能增加；
- 文本变化比例不超过 RevisionPlan.max_change_ratio，除非用户明确要求重写；
- 重新评分无硬性“必须涨分”要求，但若下降 5 分以上，应标记 needs_manual_review。

### 5.10 文件导入与导出

~~~python
class ImportClassification:
    upload_id: UUID
    content_type: Literal[
        "idea_or_notes", "outline", "full_script", "reference", "unknown"
    ]
    confidence: float
    summary: str
    detected_episode_numbers: list[int]
    suggested_action: str
    warnings: list[str]

class ExportSelection:
    include_story_bible: bool = True
    include_outlines: bool = True
    script_versions: dict[int, int | Literal["latest"]] = Field(default_factory=dict)
    include_evaluations: bool = True
    include_revision_notes: bool = True
    format: Literal["markdown", "docx"]

class ExportFileContent:
    storage_key: str
    file_name: str
    mime_type: str
    size_bytes: int
    sha256: str
    included_artifact_ids: list[UUID]
~~~

ImportClassification 低置信度或 unknown 时必须请求用户选择，不能自动把参考材料当作创作要求，也不能自动写入知识库。ExportFileContent 只保存文件元数据，文件 bytes 存在 File Store。

---

## 6. 数据库设计

### 6.1 表清单

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| projects | id, title, status, counts | 项目 |
| conversations | id, project_id | 会话 |
| messages | id, conversation_id, role, content | 对话消息 |
| workflow_runs | id, project_id, action, status, state_summary | 长任务 |
| workflow_events | run_id, sequence, type, payload | SSE 事件事实记录 |
| artifacts | type, version, episode_number, content | 不可变资产 |
| artifact_links | source_id, target_id, relation | 资产依赖图 |
| uploads | id, project_id, path, hash, mime_type | 上传文件元数据 |
| knowledge_documents | id, category, title, license | 知识文档 |
| knowledge_chunks | document_id, content, embedding, metadata | 检索块 |
| llm_calls | run_id, node, model, attempt, usage, status | 模型调用追踪 |

### 6.2 关键索引与约束

- artifacts(project_id, type, episode_number, version) 唯一；
- workflow_events(run_id, sequence) 唯一；
- workflow_runs(project_id, created_at desc)；
- messages(conversation_id, created_at)；
- knowledge_chunks 使用 pgvector 对应距离索引；
- uploads.sha256 用于项目内去重；
- artifact_links 禁止 source_id 等于 target_id；
- 数据库层检查 episode_number 大于 0、version 大于 0；
- 删除 Project 默认软删除；MVP 不做级联物理删除。

### 6.3 事务边界

- 创建 Artifact、ArtifactLink 和 artifact.created 事件在同一数据库事务内；
- 创建 Run 与 run.started 可分两步，但 Worker 必须能扫描 queued Run；
- 版本号分配与插入在同一事务内，并处理并发冲突；
- Redis 发布失败不回滚 PostgreSQL 事件；SSE 可从数据库补发；
- 文件落盘使用临时文件 + 原子 rename，数据库只记录最终成功路径。

---

## 7. Workflow 与状态机契约

### 7.1 DramaAgentState

~~~python
class DramaAgentState(TypedDict):
    project_id: str
    conversation_id: str | None
    run_id: str
    action: str
    user_input: str | None
    upload_id: str | None

    normalized_requirement_id: str | None
    story_bible_id: str | None
    outline_set_id: str | None
    script_draft_ids: list[str]
    evaluation_report_ids: list[str]
    revision_plan_id: str | None
    revised_script_id: str | None
    continuity_state_id: str | None
    retrieved_chunk_ids: list[str]

    selected_episode_number: int | None
    revision_round: int
    current_node: str
    completed_nodes: list[str]
    errors: list[WorkflowError]
~~~

State 不存剧本全文、完整 Prompt、Embedding 或上传文件 bytes。

### 7.2 Creation Workflow

| 顺序 | 节点 | 成功后的下一步 | 条件分支 |
| ---: | --- | --- | --- |
| 1 | normalize | retrieve | 关键输入缺失 -> needs_user_input |
| 2 | retrieve | story_bible | 无检索结果仍继续 |
| 3 | story_bible | outline | Schema 失败按策略重试 |
| 4 | outline | write episodes 1..3 | 必须通过 10 集校验 |
| 5 | write episodes 1..3 | evaluate episodes 1..3 | 按集顺序生成 |
| 6 | evaluate episodes 1..3 | revision decision | 无需修订 -> done |
| 7 | select + plan | revise | 无候选 -> done |
| 8 | revise | continuity check | 轮数达到上限 -> needs_review |
| 9 | continuity check | re-evaluate | 失败 -> needs_review |
| 10 | re-evaluate | done | 分数显著下降 -> needs_review |

节点约束：

- normalize：支持 Idea、Outline 或已解析文件文本；
- retrieve：无知识库数据时返回空列表，不阻断创建；
- story_bible：需要 requirement；
- outline：需要 StoryBible，输出正好 10 集；
- write：按 1 到 3 顺序写，失败停在当前集；
- evaluate：可以并行，但保存顺序按 episode_number；
- select：仅当至少一集 need_revision 为 true；
- revise：revision_round 必须小于 1；
- continuity_check：失败时不自动二次修订，转 needs_manual_review；
- re-evaluate：只评估 revised_script_id；
- done：更新 Project 状态并发 run.completed。

### 7.3 独立 Workflow

| Workflow | 输入 | 输出 |
| --- | --- | --- |
| evaluation_workflow | 一个或多个 Script Artifact ID | EvaluationReport 列表 |
| revision_workflow | Script ID、Evaluation ID、用户要求可选 | RevisionPlan、新 Script、Evaluation |
| import_workflow | Upload ID | 解析文本、分类 Artifact |
| export_workflow | Project ID、Artifact 选择 | ExportFile Artifact |

### 7.4 节点幂等键

建议计算：

~~~text
sha256(
  node_name
  + sorted(input_artifact_ids)
  + prompt_version
  + model_name
  + canonical_model_parameters
  + relevant_config_version
)
~~~

命中成功结果时可复用；用户显式 force=true 时创建新 Run，但仍产生新版本并记录复用/重算原因。

---

## 8. API 与事件契约

### 8.1 核心 API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | /api/v1/projects | 创建项目 |
| GET | /api/v1/projects | 项目列表 |
| GET | /api/v1/projects/{project_id} | 项目详情 |
| PATCH | /api/v1/projects/{project_id} | 更新元数据 |
| POST | /api/v1/projects/{project_id}/conversations | 创建会话 |
| POST | /api/v1/conversations/{conversation_id}/messages | 保存用户消息并解析意图 |
| POST | /api/v1/projects/{project_id}/runs | 创建长任务 |
| GET | /api/v1/runs/{run_id} | 查询状态 |
| POST | /api/v1/runs/{run_id}/retry | 从失败 checkpoint 重试 |
| POST | /api/v1/runs/{run_id}/cancel | 取消后续执行 |
| GET | /api/v1/runs/{run_id}/events | SSE |
| GET | /api/v1/projects/{project_id}/artifacts | 查询资产 |
| GET | /api/v1/artifacts/{artifact_id} | 资产详情 |
| GET | /api/v1/artifacts/{artifact_id}/versions | 版本历史 |
| GET | /api/v1/artifacts/diff | 两版本 Diff |
| POST | /api/v1/projects/{project_id}/evaluations | 发起独立评估 |
| POST | /api/v1/projects/{project_id}/revisions | 发起修订 |
| POST | /api/v1/projects/{project_id}/uploads | 上传 TXT/DOCX |
| POST | /api/v1/projects/{project_id}/exports | 发起导出 |
| GET | /api/v1/exports/{artifact_id}/download | 下载导出文件 |
| GET | /health/live | 存活检查 |
| GET | /health/ready | DB/Redis 就绪检查 |

### 8.2 创建 Run

~~~json
{
  "action": "create_script",
  "conversation_id": "uuid-or-null",
  "user_input": "一个被青训队抛弃的足球少年……",
  "upload_id": null,
  "options": {
    "outline_count": 10,
    "script_count": 3,
    "auto_evaluate": true,
    "auto_revise": true
  }
}
~~~

响应使用 HTTP 202：

~~~json
{
  "run_id": "uuid",
  "status": "queued",
  "events_url": "/api/v1/runs/uuid/events"
}
~~~

### 8.3 SSE Event

~~~json
{
  "event_id": "uuid",
  "run_id": "uuid",
  "sequence": 17,
  "event_type": "artifact.created",
  "stage": "episode_writer",
  "progress": 0.56,
  "message": "第 2 集剧本已生成",
  "artifact_id": "uuid",
  "payload": {
    "episode_number": 2,
    "artifact_type": "script_draft"
  },
  "created_at": "2026-07-20T18:00:00Z"
}
~~~

EventType：

- run.queued
- run.started
- node.started
- node.progress
- llm.retrying
- artifact.created
- node.completed
- node.failed
- run.needs_review
- run.failed
- run.cancelled
- run.completed

禁止在 payload 中发送完整剧本、完整 Prompt、密钥和堆栈。前端收到 artifact_id 后另行查询内容。

### 8.4 错误响应

~~~json
{
  "error": {
    "code": "ARTIFACT_SCHEMA_INVALID",
    "message": "模型输出未通过 StoryBible 校验",
    "request_id": "uuid",
    "details": {
      "retryable": true,
      "field_paths": ["protagonist.visible_goal"]
    }
  }
}
~~~

错误码至少包括：

- VALIDATION_ERROR
- PROJECT_NOT_FOUND
- ARTIFACT_NOT_FOUND
- ARTIFACT_SCHEMA_INVALID
- INVALID_WORKFLOW_TRANSITION
- RUN_ALREADY_ACTIVE
- RUN_NOT_RETRYABLE
- LLM_TIMEOUT
- LLM_RATE_LIMITED
- LLM_OUTPUT_INVALID
- FILE_TOO_LARGE
- FILE_TYPE_UNSUPPORTED
- FILE_PARSE_FAILED
- EXPORT_FAILED
- INTERNAL_ERROR

---

## 9. 配置、Prompt 与上下文规范

### 9.1 环境变量

~~~dotenv
APP_ENV=local
APP_HOST=0.0.0.0
APP_PORT=8000
LOG_LEVEL=INFO

DATABASE_URL=postgresql+asyncpg://drama:drama@localhost:5432/drama
REDIS_URL=redis://localhost:6379/0
ARTIFACT_FILE_ROOT=./var/artifacts
UPLOAD_FILE_ROOT=./var/uploads

LLM_PROVIDER=openai_compatible
LLM_API_BASE=
LLM_API_KEY=
LLM_NORMALIZER_MODEL=
LLM_PLANNER_MODEL=
LLM_WRITER_MODEL=
LLM_EVALUATOR_MODEL=
LLM_SUMMARIZER_MODEL=
LLM_TIMEOUT_SECONDS=180
LLM_MAX_RETRIES=2

EMBEDDING_PROVIDER=
EMBEDDING_MODEL=
EMBEDDING_DIMENSION=

MVP_OUTLINE_COUNT=10
MVP_SCRIPT_COUNT=3
AUTO_REVISION_THRESHOLD=75
MAX_REVISION_ROUNDS=1
SHORT_TERM_MESSAGE_COUNT=12
CONTEXT_MAX_TOKENS=24000
RAG_TOP_K=5
UPLOAD_MAX_BYTES=10485760
SSE_HEARTBEAT_SECONDS=15
~~~

要求：

- .env 不提交 Git；
- .env.example 不含真实密钥；
- 启动时验证必需配置；
- test 环境强制 FakeLLM；
- 所有可影响结果的配置写入 WorkflowRun.config_snapshot。

### 9.2 Prompt 目录规范

每个 Prompt 文件头包含：

~~~yaml
name: story_bible
version: 1.0.0
input_schema: StoryBiblePromptInput
output_schema: StoryBible
owner: creation
changelog: initial MVP prompt
~~~

Prompt 必须区分：

1. 固定系统规则；
2. 当前任务说明；
3. Artifact 上下文；
4. RAG 片段；
5. 输出 Schema；
6. 自检清单。

禁止：

- 把数据库对象直接 repr 后拼入 Prompt；
- 把全部历史对话或全部 10 集正文放入每次调用；
- 在 Prompt 内偷偷改变评分权重；
- 依赖模型自行计算 word_count、dialogue_ratio、overall_score；
- 没有版本号就修改生产 Prompt。

### 9.3 Context Builder 预算

默认按可用 token 预算分配：

| 内容 | 上限占比 |
| --- | ---: |
| 系统规则与输出 Schema | 15% |
| 当前用户请求 | 10% |
| StoryBible / 当前大纲 | 25% |
| 前集摘要与 ContinuityState | 20% |
| RAG 片段 | 15% |
| 当前稿件或目标场景 | 按任务使用剩余预算 |
| 输出与安全缓冲 | 至少 10% |

裁剪顺序：

1. 删除低分 RAG 片段；
2. 只保留与当前集角色/伏笔有关的连续性项；
3. 将较早会话换成摘要；
4. 缩短非目标集大纲；
5. 如果当前稿件仍超限，按场景分段处理并显式记录，不能静默截断。

---

## 10. 测试、可观测性与安全基线

### 10.1 测试分层

| 层 | 内容 | 是否调用真实 LLM |
| --- | --- | --- |
| unit | 纯领域规则、Schema、工具、选择器 | 否 |
| contract | API、事件、Prompt 输出 Schema 快照 | 否 |
| repository | PostgreSQL/Redis 读写与并发版本 | 否 |
| workflow | FakeLLM 驱动节点、分支、恢复 | 否 |
| integration | API + DB + Redis + 文件系统 | 否 |
| golden | 固定输入的结构与不变量 | 默认否 |
| e2e | Playwright 完整 Demo | 否 |
| smoke | 手工触发真实模型最小链路 | 是，不进入普通 CI |

### 10.2 FakeLLM 规则

- 按 prompt_name 返回 fixtures/golden 中的合法对象；
- 可以配置第 N 次调用超时、限流或输出非法 JSON；
- 记录调用顺序与输入 Artifact ID；
- 不通过字符串模糊判断来生成业务数据；
- 支持 deterministic seed；
- E2E 的固定数据必须包含 1 个低分集，确保进入修订分支。

### 10.3 关键测试场景

1. 10 集大纲缺第 7 集时校验失败；
2. ScriptDraft 自报 word_count 错误时以 Tool 结果覆盖；
3. 三集同分时选择 episode_number 最小者；
4. revision_round=1 时不再进入修订；
5. 修订稿改变 locked fact 时进入 needs_manual_review；
6. LLM 两次输出非法结构时 Run 失败且无 valid Artifact；
7. Redis 清空后仍能从 workflow_events 补发 SSE；
8. 相同 Idempotency-Key 不重复创建 Run；
9. 并发创建同类 Artifact 时版本不重复；
10. 上传伪装成 DOCX 的文件被拒绝；
11. 导出失败不影响原有剧本资产；
12. checkpoint 后重试不重复生成已完成的前两集。

### 10.4 日志与指标

结构化日志字段：

- timestamp
- level
- service
- request_id
- project_id
- run_id
- node_name
- artifact_id
- event_type
- duration_ms
- error_code

核心指标：

- workflow_runs_total{action,status}
- workflow_node_duration_seconds{node}
- llm_calls_total{role,model,status}
- llm_retry_total{reason}
- llm_token_usage_total{role}
- artifact_created_total{type}
- schema_validation_failure_total{schema}
- sse_connections_active
- rag_retrieval_duration_seconds
- export_total{format,status}

### 10.5 安全与内容边界

- 上传文件校验扩展名、MIME、文件签名与大小；
- DOCX 只读取文档文本，不执行宏、不解析外部链接；
- 文件名由服务端重命名，阻止路径穿越；
- 下载接口验证 Artifact 所属项目；
- 日志对消息和 Prompt 只保留长度、哈希或受控摘要；
- API Key 仅从服务端环境变量读取；
- RAG 文档记录来源、授权/许可证和导入者；
- 对模型输出做 HTML 转义；
- 合规评分不替代正式法律审查；
- MVP 即使无登录，也保留 owner_id/tenant_id 接口位置，避免后续数据模型重构。

---

## 11. 阶段总览与依赖关系

### 11.1 阶段路线

| 阶段 | 目标 | 预计工作量 | 阶段性可演示结果 |
| --- | --- | ---: | --- |
| A | 工程基线与契约 | 3 人日 | 仓库可启动，Schema/CI 可运行 |
| B | 平台底座 | 6 人日 | 项目、Run、SSE、Artifact、FakeLLM 纵切可用 |
| C | 创作链路 | 7.25 人日 | Idea -> StoryBible -> 10 集大纲 -> 3 集正文 |
| D | RAG 知识库 | 4 人日 | 创作各节点能检索并记录知识依据 |
| E | 评估链路 | 4 人日 | 3 集结构化评分、诊断与报告 |
| F | 修订与版本 | 5 人日 | 最低分集修订、连续性门禁、Diff、重新评分 |
| G | 记忆、导入与导出 | 5 人日 | 多轮继续创作、TXT/DOCX 输入、MD/DOCX 输出 |
| H | 前端工作台与全链路 | 5 人日 | 可操作的完整 Web Demo |
| I | 稳定性、扩展接口与发布 | 3 人日 | 可恢复、可观测、可交付的 MVP |
| 合计 | 单人 + AI Coding 基准 | 约 42.25 人日 | 约 8 到 9 周 |

若要严格压缩到 8 周，可在阶段 C 结束后并行开发 D 与 H 的静态页面，但 F 必须依赖 E，完整 E2E 必须在全部阶段完成后执行。

### 11.2 阶段门禁

| 阶段 | 必须完成的前置 Gate | 可并行说明 |
| --- | --- | --- |
| A | 无 | 不并行，先锁定工程契约 |
| B | A | B-06 可与 B-02/B-03 并行 |
| C | B | Skill 可先分文件开发，C-07 最后集成 |
| D | C | 可与 H 的静态页面开发并行 |
| E | C、D 的 Rubric/Retriever 能力 | 与 D 后半段有限并行 |
| F | E | 不与 E 的契约变更并行 |
| G | F，且 D 已稳定 | G-03 文件解析可提前 |
| H | C、D、E、F、G 的目标接口 | H-01/H-02 可提前 |
| I | H | 只做加固，不增加产品范围 |

规则：阶段 Exit Gate 未通过，不进入依赖它的阶段。允许先编写后续模块的接口或静态 UI，但不能将其标记为完成。

---

## 12. 分阶段开发任务

## 阶段 A：工程基线与契约

### 阶段目标

建立可重复的本地环境、稳定目录、领域 Schema、测试框架和 CI 门禁。此阶段不实现业务工作流。

### A-01 初始化 Monorepo 与开发命令

- 预计：0.5 人日
- 依赖：无
- 修改文件：
  - README.md
  - Makefile
  - .gitignore
  - backend/pyproject.toml
  - backend/app/__init__.py
  - backend/tests/__init__.py
  - frontend/package.json
  - frontend/tsconfig.json
- 实现：
  - 初始化 backend 和 frontend；
  - 配置 uv、pytest、Ruff、mypy；
  - 配置 pnpm、TypeScript、ESLint、Vitest；
  - 统一命令：make install、make lint、make typecheck、make test；
  - README 写出 5 分钟启动步骤。
- 验收：
  - [ ] 新环境按 README 可完成安装；
  - [ ] 后端空测试和前端空测试可执行；
  - [ ] lock 文件已生成并提交；
  - [ ] Makefile 失败时返回非 0。
- 测试命令：
  - make lint
  - make typecheck
  - make test

### A-02 本地基础设施与配置

- 预计：0.75 人日
- 依赖：A-01
- 修改文件：
  - docker-compose.yml
  - .env.example
  - backend/app/core/config.py
  - backend/tests/unit/core/test_config.py
  - Makefile
- 实现：
  - Compose 启动 PostgreSQL + pgvector、Redis；
  - Settings 使用 Pydantic Settings；
  - local、test、production 配置边界；
  - make up、make down、make doctor；
  - 启动时创建本地 var/uploads、var/artifacts，但不提交内容。
- 验收：
  - [ ] make up 后数据库和 Redis 健康；
  - [ ] 缺失必需变量时错误信息指出变量名；
  - [ ] test 环境默认 FakeLLM；
  - [ ] .env.example 无真实密钥。
- 测试：
  - pytest backend/tests/unit/core/test_config.py
  - make doctor

### A-03 领域 Schema、枚举与 Golden Fixtures

- 预计：1 人日
- 依赖：A-01
- 修改文件：
  - backend/app/domain/enums.py
  - backend/app/domain/requirement.py
  - backend/app/domain/story_bible.py
  - backend/app/domain/outline.py
  - backend/app/domain/script.py
  - backend/app/domain/evaluation.py
  - backend/app/domain/revision.py
  - backend/app/domain/continuity.py
  - backend/tests/golden/*.json
  - backend/tests/contract/test_domain_schemas.py
- 实现：
  - 落地第 5 节的 Pydantic 模型；
  - 实现分数、集数、权重和必填字段验证器；
  - 为每种 Artifact 内容创建一份合法和一份非法 fixture；
  - 输出 JSON Schema 快照；
  - Schema 设置 extra=forbid。
- 验收：
  - [ ] 10 集大纲的编号/数量验证有效；
  - [ ] 0..100 分数边界有效；
  - [ ] Evaluation 权重之和测试等于 1；
  - [ ] 非法额外字段会被拒绝；
  - [ ] Golden fixtures 可序列化再反序列化。
- 测试：
  - pytest backend/tests/contract/test_domain_schemas.py

### A-04 质量门禁与 CI

- 预计：0.75 人日
- 依赖：A-01、A-03
- 修改文件：
  - .github/workflows/ci.yml
  - backend/pyproject.toml
  - frontend/package.json
  - docs/TEST_PLAN.md
- 实现：
  - 后端 lint、typecheck、unit/contract test；
  - 前端 lint、typecheck、unit test；
  - CI 使用 PostgreSQL/Redis service；
  - pytest marker：unit、integration、contract、workflow、smoke；
  - coverage 失败阈值先设 70%，阶段 I 提升到最终目标；
  - 禁止 smoke 测试在普通 CI 调用真实模型。
- 验收：
  - [ ] 一个故意失败的测试能阻止 CI；
  - [ ] CI 不读取开发者本机 .env；
  - [ ] 测试报告和覆盖率可下载；
  - [ ] 文档写明每类测试何时运行。
- 测试：本地执行与 CI 相同的 make ci。

### 阶段 A Exit Gate

~~~text
前置：全新 clone
执行：
1. cp .env.example .env
2. make install
3. make up
4. make doctor
5. make ci

通过条件：所有命令成功；无真实 LLM 调用；领域契约测试全部通过。
~~~

阶段 A 交付：可开发仓库、锁定依赖、领域 JSON Schema、测试与 CI 基线。

---

## 阶段 B：平台底座

### 阶段目标

实现 FastAPI、持久化、项目/会话、Run/Event/SSE、Artifact、LLM 抽象和注册表。阶段末跑通一个“Fake 节点创建 Artifact 并实时通知”的最小纵切。

### B-01 FastAPI 启动、错误模型与健康检查

- 预计：0.75 人日
- 依赖：A-02
- 修改文件：
  - backend/app/main.py
  - backend/app/api/v1/router.py
  - backend/app/core/errors.py
  - backend/app/core/logging.py
  - backend/app/api/dependencies.py
  - backend/tests/integration/test_health.py
- 实现：
  - app factory：create_app(settings)；
  - request_id middleware；
  - 统一 ErrorResponse 与异常处理器；
  - /health/live 与 /health/ready；
  - CORS 从配置读取；
  - OpenAPI 标记 v1。
- 验收：
  - [ ] live 不依赖外部服务；
  - [ ] ready 在 DB/Redis 不可用时返回 503 和具体依赖名；
  - [ ] 任意错误响应包含 request_id；
  - [ ] 日志为结构化 JSON。
- 测试：
  - pytest -m integration backend/tests/integration/test_health.py

### B-02 ORM、Migration 与 Repository 基础

- 预计：1.25 人日
- 依赖：B-01、A-03
- 修改文件：
  - backend/app/db/session.py
  - backend/app/db/models/*.py
  - backend/app/db/repositories/base.py
  - backend/migrations/versions/0001_initial.py
  - backend/tests/integration/db/*
- 实现：
  - 第 6 节全部基础表；
  - AsyncSession 生命周期；
  - Repository Protocol 与 SQLAlchemy 实现；
  - created_at/updated_at 统一 UTC；
  - 软删除字段；
  - migration upgrade/downgrade；
  - PostgreSQL pgvector 扩展。
- 验收：
  - [ ] 空库 alembic upgrade head 成功；
  - [ ] downgrade 后可重新 upgrade；
  - [ ] 唯一键与 check constraints 生效；
  - [ ] 测试事务结束后数据清理；
  - [ ] Redis 未参与持久对象读写。
- 测试：
  - alembic upgrade head
  - pytest -m integration backend/tests/integration/db

### B-03 Project、Conversation 与 Message API

- 预计：0.75 人日
- 依赖：B-02
- 修改文件：
  - backend/app/domain/project.py
  - backend/app/domain/conversation.py
  - backend/app/application/project_service.py
  - backend/app/application/conversation_service.py
  - backend/app/api/v1/projects.py
  - backend/app/api/v1/conversations.py
  - backend/tests/integration/api/test_projects.py
  - backend/tests/integration/api/test_conversations.py
- 实现：
  - Project CRUD（不含物理删除）；
  - 创建会话、追加消息、分页读取消息；
  - title、episode counts 范围校验；
  - 服务层事务；
  - API 分页统一 cursor 或 offset 契约。
- 验收：
  - [ ] 可以创建、查询、更新项目；
  - [ ] 不存在的 project 返回 PROJECT_NOT_FOUND；
  - [ ] 消息不能跨项目写入；
  - [ ] 消息按时间和 ID 稳定排序。
- 测试：
  - pytest -m integration backend/tests/integration/api/test_projects.py backend/tests/integration/api/test_conversations.py

### B-04 Artifact Store 与不可变版本

- 预计：1 人日
- 依赖：B-02、A-03
- 修改文件：
  - backend/app/artifacts/store.py
  - backend/app/artifacts/versions.py
  - backend/app/application/artifact_service.py
  - backend/app/db/repositories/artifacts.py
  - backend/app/api/v1/artifacts.py
  - backend/tests/unit/artifacts/*
  - backend/tests/integration/artifacts/*
- 实现：
  - create_validated_artifact；
  - get_latest、get_version、list_by_project；
  - source links；
  - canonical JSON checksum 与 input_hash；
  - 事务内分配 version，处理并发冲突；
  - 禁止更新 content；
  - Artifact API 与分页。
- 主要接口：
  - ArtifactStore.create(...)
  - ArtifactStore.get_latest(...)
  - ArtifactStore.list_versions(...)
  - ArtifactStore.find_by_input_hash(...)
- 验收：
  - [ ] 首版本为 1；
  - [ ] 新版本不覆盖旧 content；
  - [ ] 并发写入不产生重复 version；
  - [ ] 非法 Schema 只可保存为 invalid 诊断记录，不能成为 latest valid；
  - [ ] source_artifact_ids 可查询。
- 测试：
  - pytest backend/tests/unit/artifacts backend/tests/integration/artifacts

### B-05 WorkflowRun、Event、SSE 与 Worker

- 预计：1 人日
- 依赖：B-02、B-03
- 修改文件：
  - backend/app/application/run_service.py
  - backend/app/events/schemas.py
  - backend/app/events/publisher.py
  - backend/app/events/stream.py
  - backend/app/api/v1/runs.py
  - backend/app/workflows/checkpoint.py
  - backend/tests/integration/events/*
- 实现：
  - queued/running/completed/failed/cancelled/needs_review 状态机；
  - PostgreSQL 事件落库；
  - Redis pub/sub 仅做实时通知；
  - SSE heartbeat、Last-Event-ID 补发；
  - 进程内 Worker 扫描 queued Run；
  - Idempotency-Key；
  - retry/cancel 基础接口。
- 验收：
  - [ ] sequence 严格递增且唯一；
  - [ ] SSE 断线重连不丢事件；
  - [ ] Redis 清空后历史事件仍存在；
  - [ ] cancelled Run 不再启动新节点；
  - [ ] 相同幂等键返回原 run_id。
- 测试：
  - pytest -m integration backend/tests/integration/events

### B-06 LLM Protocol、结构化输出与 FakeLLM

- 预计：0.75 人日
- 依赖：A-03、A-02
- 修改文件：
  - backend/app/llm/protocol.py
  - backend/app/llm/client.py
  - backend/app/llm/structured_output.py
  - backend/app/llm/fake.py
  - backend/app/llm/models.py
  - backend/tests/unit/llm/*
- 实现：
  - LLMClient.generate_structured；
  - provider adapter；
  - Pydantic 校验、超时、限流和重试分类；
  - usage/latency 记录；
  - FakeLLM fixture 路由和故障注入；
  - 敏感字段日志脱敏。
- 验收：
  - [ ] FakeLLM 返回指定 Schema；
  - [ ] 非法输出触发最多 2 次重试；
  - [ ] 超时映射为 LLM_TIMEOUT；
  - [ ] 单元测试不访问网络；
  - [ ] 日志没有 API Key 和完整 Prompt。
- 测试：
  - pytest backend/tests/unit/llm

### B-07 BaseAgent、Tool Registry 与 Skill Registry

- 预计：0.5 人日
- 依赖：B-06
- 修改文件：
  - backend/app/agents/base.py
  - backend/app/tools/protocol.py
  - backend/app/tools/registry.py
  - backend/app/skills/protocol.py
  - backend/app/skills/registry.py
  - backend/tests/unit/registries/*
- 实现：
  - Tool metadata：name、version、input_schema、output_schema；
  - Skill metadata 与 execute(context)；
  - 重名注册失败；
  - BaseAgent 统一追踪、模型调用、Schema 校验；
  - 创建测试用 EchoTool、EchoSkill、EchoAgent。
- 验收：
  - [ ] 注册、查询、执行、未找到错误均可测试；
  - [ ] Agent 不直接依赖具体 provider；
  - [ ] Tool 不可隐式调用 LLM；
  - [ ] 元数据可序列化供未来 MCP Adapter 使用。
- 测试：
  - pytest backend/tests/unit/registries

### 阶段 B Exit Gate

最小纵切场景：

1. 创建项目和会话；
2. 创建 action=platform_smoke 的 Run；
3. Worker 通过 EchoAgent/FakeLLM 生成一个测试 Artifact；
4. 浏览器或测试客户端从 SSE 收到 started、artifact.created、completed；
5. 查询 Artifact 内容与版本；
6. 清空 Redis，仍能从 Last-Event-ID 补发数据库事件。

全部完成后才能进入创作链路。

---

## 阶段 C：创作链路

### 阶段目标

实现从 Idea/Outline 到 StoryBible、10 集大纲、前 3 集正文的完整后端工作流。RAG 在本阶段使用 NullRetriever 或少量静态 fixture，阶段 D 再接入正式向量检索。

### C-01 Prompt Loader、Manifest 与版本追踪

- 预计：0.5 人日
- 依赖：B-06
- 修改文件：
  - backend/app/prompts/manifest.yaml
  - backend/app/prompts/templates/*.md
  - backend/app/prompts/loader.py
  - docs/PROMPT_GUIDE.md
  - backend/tests/contract/test_prompts.py
- 实现：
  - 按 name/version 加载 Prompt；
  - 启动时校验 input_schema/output_schema 存在；
  - 模板变量缺失立即失败；
  - manifest 记录 changelog；
  - Prompt hash 写入 LLM call 与 Artifact。
- 验收：
  - [x] 同 name 不允许重复 version；
  - [x] Prompt 修改但 version 未变时快照测试失败；
  - [x] 模板不存在返回可定位错误；
  - [x] 不在模板中硬编码模型名和密钥。
- 测试：
  - pytest backend/tests/contract/test_prompts.py

### C-02 Requirement Skill

- 预计：0.75 人日
- 依赖：C-01、B-07
- 修改文件：
  - backend/app/skills/requirement.py
  - backend/app/prompts/templates/requirement.md
  - backend/tests/unit/skills/test_requirement.py
  - backend/tests/golden/requirement_football.json
- 实现：
  - RequirementInput 与 NormalizedRequirement；
  - Idea/Outline source_type；
  - 非关键缺省生成 assumptions；
  - 关键输入缺失返回 NeedsUserInput，而非猜测；
  - 保存 Artifact 的 service 由节点调用，Skill 本身不写库。
- 验收：
  - [x] 足球 Idea 生成合法结构；
  - [x] 缺主角和核心冲突时阻断；
  - [x] target_episode_count 范围合法；
  - [x] 原始用户要求中的 must_have 不丢失。
- 测试：
  - pytest backend/tests/unit/skills/test_requirement.py

### C-03 StoryBible Skill

- 预计：1 人日
- 依赖：C-02
- 修改文件：
  - backend/app/skills/story_bible.py
  - backend/app/agents/creation.py
  - backend/app/prompts/templates/story_bible.md
  - backend/tests/unit/skills/test_story_bible.py
  - backend/tests/golden/story_bible_football.json
- 实现：
  - StoryBiblePromptInput；
  - 角色 ID 稳定化；
  - locked_facts、story_rules、open_loops 最小数量校验；
  - 同名角色和空目标校验；
  - CreationAgent.generate_story_bible。
- 验收：
  - [x] 主角、反派、至少一个配角字段完整；
  - [x] locked_facts 至少 3 条；
  - [x] 角色 ID 在后续 fixture 中可引用；
  - [x] Artifact 记录 requirement source ID 与 Prompt 版本。
- 测试：
  - pytest backend/tests/unit/skills/test_story_bible.py

### C-04 Outline Skill

- 预计：1 人日
- 依赖：C-03
- 修改文件：
  - backend/app/skills/outline.py
  - backend/app/prompts/templates/outline.md
  - backend/app/domain/outline.py
  - backend/tests/unit/skills/test_outline.py
  - backend/tests/golden/outline_football_10.json
- 实现：
  - 一次生成 10 集；
  - EpisodeOutlineSet.validate_sequence；
  - 检查第 N 集 next_bridge 与第 N+1 集目标至少存在语义承接说明；
  - 检查 required_characters 均在 StoryBible；
  - 结构错误可重试，业务弱项只写 validation_notes。
- 验收：
  - [x] 正好 10 集且连续编号；
  - [x] 每集有开头、冲突、爽点和结尾钩子；
  - [x] 不引用不存在角色；
  - [x] 第 10 集形成小阶段高潮而不是强制大结局；
  - [x] 保存为单个 episode_outline_set Artifact。
- 测试：
  - pytest backend/tests/unit/skills/test_outline.py

### C-05 Episode Writer 与确定性文本工具

- 预计：1.25 人日
- 依赖：C-04、B-07
- 修改文件：
  - backend/app/skills/episode_writer.py
  - backend/app/prompts/templates/episode_writer.md
  - backend/app/tools/word_count.py
  - backend/app/tools/dialogue_ratio.py
  - backend/tests/unit/tools/*
  - backend/tests/unit/skills/test_episode_writer.py
- 实现：
  - 以当前集 Outline、StoryBible、前集摘要为输入；
  - 输出 Scene/DialogueLine 结构与 plain_text；
  - WordCountTool 与 DialogueRatioTool；
  - 按 1、2、3 顺序生成；
  - 每集独立 ScriptDraft Artifact；
  - 设置合理的中文字符/台词比例告警，但不因轻微越界直接失败。
- 验收：
  - [x] LLM 自报指标被服务端计算值覆盖；
  - [x] Scene 编号连续且至少 2 场；
  - [x] 角色名均可追溯到 StoryBible，临时群众角色有明确规则；
  - [x] ending_hook 与 Outline 对应；
  - [x] 第 2 集调用上下文包含第 1 集摘要而非全文。
- 测试：
  - pytest backend/tests/unit/tools backend/tests/unit/skills/test_episode_writer.py

### C-06 Continuity Manager 与 Context Builder 基础

- 预计：1 人日
- 依赖：C-03、C-04、C-05
- 修改文件：
  - backend/app/memory/continuity.py
  - backend/app/memory/context_builder.py
  - backend/app/skills/summarizer.py
  - backend/app/prompts/templates/episode_summary.md
  - backend/tests/unit/memory/*
- 实现：
  - 初始 ContinuityState 从 StoryBible 创建；
  - 每集后生成 EpisodeSummary 并更新人物状态、伏笔、时间线；
  - locked facts 只增不减，除非新 StoryBible 版本显式修改；
  - ContextBuilder 按第 9.3 节预算组装；
  - 输出 context_manifest，记录使用/裁剪的资产与 chunk。
- 验收：
  - [ ] 生成第 3 集时读取前两集摘要；
  - [ ] 开放和回收伏笔状态可追踪；
  - [ ] 超预算时按约定顺序裁剪；
  - [ ] 不能静默截断当前目标场景；
  - [ ] context_manifest 可用于调试。
- 测试：
  - pytest backend/tests/unit/memory

### C-07 LangGraph Creation Workflow

- 预计：1.25 人日
- 依赖：C-02 至 C-06、B-05
- 修改文件：
  - backend/app/workflows/state.py
  - backend/app/workflows/creation.py
  - backend/app/workflows/nodes/normalize.py
  - backend/app/workflows/nodes/story_bible.py
  - backend/app/workflows/nodes/outline.py
  - backend/app/workflows/nodes/write_episode.py
  - backend/app/workflows/nodes/finalize.py
  - backend/tests/workflow/test_creation_workflow.py
- 实现：
  - State 使用 Artifact ID；
  - normalize -> null_retrieve -> story_bible -> outline -> write 1..3 -> finalize；
  - 每节点事件、checkpoint、input hash；
  - 已完成节点重试时复用；
  - Project 状态随节点更新；
  - 预留 evaluate_after_creation 分支，但暂不实现评估逻辑。
- 验收：
  - [ ] FakeLLM 完整生成 5 类核心资产；
  - [ ] 第 2 集失败后重试不重复第 1 集；
  - [ ] State 不含 Script 全文；
  - [ ] 每个 Artifact 依赖链正确；
  - [ ] run.completed 前所有 Artifact 已提交。
- 测试：
  - pytest -m workflow backend/tests/workflow/test_creation_workflow.py

### C-08 Creation API 纵切与契约测试

- 预计：0.5 人日
- 依赖：C-07、B-03
- 修改文件：
  - backend/app/api/v1/runs.py
  - backend/app/workflows/router.py
  - backend/tests/integration/api/test_creation_run.py
  - docs/API_CONTRACT.md
- 实现：
  - action=create_script；
  - options 的 MVP 边界验证；
  - 202 + run_id；
  - 从 SSE 到 Artifact 查询的契约测试；
  - 重复活跃创建 Run 的冲突策略。
- 验收：
  - [ ] outline_count 不是 10 或 script_count 超过 3 时按 MVP 配置处理；
  - [ ] 完整 API 纵切无需直接调用内部 service；
  - [ ] OpenAPI 中响应和错误码完整；
  - [ ] SSE progress 单调不倒退。
- 测试：
  - pytest -m integration backend/tests/integration/api/test_creation_run.py

### 阶段 C Exit Gate

固定输入：

~~~text
一个被青训队抛弃的足球少年，靠隐藏天赋逆袭进入职业赛场。
要求强爽点、强反派压迫、每集结尾有追更钩子。
~~~

通过条件：

- 生成 1 份 requirement、1 份 StoryBible、1 份 10 集大纲、3 份 ScriptDraft 和连续性状态；
- 事件顺序完整；
- 资产依赖可追溯；
- 中途故障恢复测试通过；
- 真实模型只需完成一次人工 smoke，不作为阶段自动验收前提。

---

## 阶段 D：RAG 知识库

### 阶段目标

建立可追溯、可替换的内部短剧知识库，使 StoryBible、大纲、正文和后续评估按任务检索相关知识，而不是把全部资料塞入 Prompt。

### D-01 知识分类、元数据与内容治理

- 预计：0.5 人日
- 依赖：阶段 C
- 修改文件：
  - knowledge/README.md
  - knowledge/rubric/*
  - knowledge/templates/*
  - knowledge/hooks/*
  - knowledge/examples/*
  - backend/app/rag/models.py
- 实现：
  - category：genre_template、opening_hook、ending_hook、payoff、character_archetype、rubric、compliance；
  - metadata：title、source、license、language、genre、stage、tags、version；
  - 只纳入有权限使用的内容；
  - 为每类至少准备 3 个可测试短片段；
  - 定义 knowledge corpus 版本。
- 验收：
  - [ ] 每份文档有来源与授权字段；
  - [ ] 测试资料不包含完整商业剧本；
  - [ ] 文件格式和命名统一；
  - [ ] corpus_version 可写入检索追踪。
- 测试：元数据 Schema 扫描测试。

### D-02 Loader、Chunker 与摄取命令

- 预计：0.75 人日
- 依赖：D-01、B-02
- 修改文件：
  - backend/app/rag/loader.py
  - backend/app/rag/chunker.py
  - backend/app/cli/knowledge.py
  - backend/migrations/versions/0002_knowledge.py
  - backend/tests/unit/rag/test_chunker.py
  - backend/tests/integration/rag/test_loader.py
- 实现：
  - Markdown/JSON 知识文档加载；
  - 按标题与语义段落切块，保留父标题；
  - 确定性 document hash 和 chunk hash；
  - 重复摄取幂等；
  - CLI：knowledge ingest、knowledge status。
- 验收：
  - [ ] 重复导入不产生重复 chunk；
  - [ ] 文档更新只重建变化部分；
  - [ ] chunk 保留来源、分类和标题路径；
  - [ ] 删除源文件不会静默物理删除线上记录。
- 测试：
  - pytest backend/tests/unit/rag/test_chunker.py backend/tests/integration/rag/test_loader.py

### D-03 Embedder 与 pgvector 存储

- 预计：0.75 人日
- 依赖：D-02
- 修改文件：
  - backend/app/rag/embedder.py
  - backend/app/db/repositories/knowledge.py
  - backend/tests/unit/rag/test_embedder.py
  - backend/tests/integration/rag/test_pgvector.py
- 实现：
  - Embedder Protocol、真实 Adapter、FakeEmbedder；
  - dimension 启动校验；
  - 批处理、重试与缓存；
  - 文本 hash 相同复用 embedding；
  - pgvector 相似度查询。
- 验收：
  - [ ] 测试环境不访问网络；
  - [ ] 维度不匹配时在写入前失败；
  - [ ] 同一文本不重复计算；
  - [ ] top-k 查询稳定返回相似度与 metadata。
- 测试：
  - pytest backend/tests/unit/rag/test_embedder.py backend/tests/integration/rag/test_pgvector.py

### D-04 Retriever、过滤与 RetrievalTrace

- 预计：1 人日
- 依赖：D-03
- 修改文件：
  - backend/app/rag/retriever.py
  - backend/app/domain/retrieval.py
  - backend/tests/unit/rag/test_retriever.py
  - backend/tests/golden/rag_queries.json
- 实现：
  - query + category/genre/stage 过滤；
  - top_k、最低相似度、每文档最大块数；
  - 去重与稳定排序；
  - RetrievalTrace：query、chunk IDs、scores、filters、corpus_version；
  - NullRetriever 继续保留用于无知识库模式。
- 验收：
  - [ ] StoryBible 查询不会返回纯评分 Rubric；
  - [ ] Evaluator 可限定 rubric 类；
  - [ ] 无结果返回空列表而非异常；
  - [ ] trace 不包含不必要的全文。
- 测试：
  - pytest backend/tests/unit/rag/test_retriever.py

### D-05 创作链路接入与检索质量测试

- 预计：1 人日
- 依赖：D-04、C-07
- 修改文件：
  - backend/app/workflows/nodes/retrieve.py
  - backend/app/skills/story_bible.py
  - backend/app/skills/outline.py
  - backend/app/skills/episode_writer.py
  - backend/tests/workflow/test_creation_with_rag.py
  - backend/tests/golden/rag_expectations.json
- 实现：
  - StoryBible 检索题材模板/人物原型；
  - Outline 检索同题材结构/钩子；
  - Writer 检索冲突、爽点、对白类片段；
  - context_manifest 记录 chunk ID；
  - 简单检索评测：固定 query 的 expected category 命中率。
- 验收：
  - [ ] 三类节点检索过滤不同；
  - [ ] Prompt 中每个片段带短 ID 和来源标题；
  - [ ] 删除 RAG 后主流程仍可运行；
  - [ ] 固定测试集 category hit@5 达到 90%；
  - [ ] 不用生成文本字面匹配判断“质量提升”。
- 测试：
  - pytest -m workflow backend/tests/workflow/test_creation_with_rag.py

### 阶段 D Exit Gate

1. 清空知识库并运行 creation_workflow，流程成功；
2. 摄取 fixture corpus 后再次运行，RetrievalTrace 有记录；
3. StoryBible、Outline、Writer 各自命中对应类别；
4. Artifact 能追溯 corpus_version 和 chunk IDs；
5. 重复摄取幂等。

---

## 阶段 E：评估链路

### 阶段目标

实现结构化、可解释、可复现测试的剧本评估。LLM 负责给出维度判断、证据和诊断；服务端负责分数范围、加权总分、阈值和结构校验。

### E-01 Rubric 配置与确定性指标

- 预计：0.75 人日
- 依赖：D-01、A-03
- 修改文件：
  - knowledge/rubric/mvp_v1.yaml
  - backend/app/domain/evaluation.py
  - backend/app/tools/word_count.py
  - backend/app/tools/dialogue_ratio.py
  - backend/app/tools/script_structure.py
  - backend/tests/unit/evaluation/test_rubric.py
- 实现：
  - 9 维定义、权重、1/3/5 档锚点说明；
  - 0..100 映射；
  - compute_overall_score；
  - compute_need_revision；
  - 客观辅助特征：场景数、对白比例、钩子字段、角色数；
  - rubric_version。
- 验收：
  - [ ] 权重和严格等于 1；
  - [ ] overall_score 只由服务端计算；
  - [ ] 高风险问题可独立触发 need_revision；
  - [ ] Rubric 版本改变会进入 Artifact metadata；
  - [ ] 辅助特征不偷偷替代 LLM 维度分。
- 测试：
  - pytest backend/tests/unit/evaluation/test_rubric.py

### E-02 Evaluation Skill 与 Prompt

- 预计：1 人日
- 依赖：E-01、C-05、D-04
- 修改文件：
  - backend/app/skills/evaluator.py
  - backend/app/agents/evaluation.py
  - backend/app/prompts/templates/evaluator.md
  - backend/tests/unit/skills/test_evaluator.py
  - backend/tests/golden/evaluation_episode_*.json
- 实现：
  - EvaluationPromptInput 包含当前 Script、Outline、必要 StoryBible、Rubric 和辅助特征；
  - 每个问题必须提供 evidence、diagnosis、suggestion；
  - evidence 限制长度并要求定位到场景；
  - 维度分由模型给出，overall/need_revision 服务端回填；
  - 合规风险输出 risk_flags；
  - 不把其他集的评估结论注入当前集。
- 验收：
  - [ ] 每个低于 70 的维度至少有一条对应 issue；
  - [ ] issue 的 scene_number 必须存在或为 null；
  - [ ] evidence 来自目标稿件，长度受限；
  - [ ] 总分计算与固定 fixture 一致；
  - [ ] Artifact source 包含 Script、Outline、Rubric 版本。
- 测试：
  - pytest backend/tests/unit/skills/test_evaluator.py

### E-03 Evaluation Service 与报告查询

- 预计：0.75 人日
- 依赖：E-02、B-04
- 修改文件：
  - backend/app/application/evaluation_service.py
  - backend/app/db/repositories/artifacts.py
  - backend/app/api/v1/evaluations.py
  - backend/tests/integration/api/test_evaluations.py
- 实现：
  - evaluate_script、evaluate_many；
  - 已有相同 input_hash 结果复用；
  - 按 episode_number 排序；
  - 查询项目最新评估、按版本查询；
  - 独立评估返回 Run，不同步阻塞请求。
- 验收：
  - [ ] 可单集或多集发起评估；
  - [ ] Evaluation 与具体 Script 版本绑定；
  - [ ] 修订后不会覆盖原稿评估；
  - [ ] 不允许评估其他项目的 Artifact；
  - [ ] 相同输入复用行为可追踪。
- 测试：
  - pytest -m integration backend/tests/integration/api/test_evaluations.py

### E-04 Evaluation Workflow 与创建链路分支

- 预计：0.75 人日
- 依赖：E-03、C-07
- 修改文件：
  - backend/app/workflows/evaluation.py
  - backend/app/workflows/nodes/evaluate_episode.py
  - backend/app/workflows/creation.py
  - backend/tests/workflow/test_evaluation_workflow.py
  - backend/tests/workflow/test_creation_evaluation_branch.py
- 实现：
  - 独立 evaluation_workflow；
  - creation_workflow 在 3 集完成后进入评估；
  - 可并行执行模型调用，但落库与 State 顺序稳定；
  - 任一集失败时明确 partial failure，不伪装为完整评估；
  - 阶段末暂时到 needs_revision_decision，不进入实际修订。
- 验收：
  - [ ] 3 份报告均绑定正确集和版本；
  - [ ] SSE 展示正在评估第 N 集；
  - [ ] 并发完成顺序不同不影响返回顺序；
  - [ ] 单集失败可单独重试；
  - [ ] 有低分集时 Run 进入 needs_revision_decision。
- 测试：
  - pytest -m workflow backend/tests/workflow/test_evaluation_workflow.py backend/tests/workflow/test_creation_evaluation_branch.py

### E-05 评估一致性与 Golden 回归

- 预计：0.75 人日
- 依赖：E-04
- 修改文件：
  - backend/tests/golden/evaluation_cases/*
  - backend/tests/contract/test_evaluation_invariants.py
  - backend/scripts/evaluate_rubric_smoke.py
  - docs/TEST_PLAN.md
- 实现：
  - 高、中、低三个固定剧本 fixture；
  - 自动测试结构与不变量，不断言真实 LLM 的精确分值；
  - 手工 smoke 可重复调用真实 evaluator，输出均值、标准差和问题交集；
  - 记录 model、Prompt、Rubric 版本；
  - 真实测试结果只作诊断，不阻塞普通 CI。
- 验收：
  - [ ] FakeLLM 回归完全确定；
  - [ ] 每个 Golden case 的预期分支明确；
  - [ ] 真实 smoke 脚本不包含密钥；
  - [ ] 报告区分“模型判断”与“确定性指标”；
  - [ ] 不用缓存同一结果冒充评估稳定性。
- 测试：
  - pytest backend/tests/contract/test_evaluation_invariants.py

### 阶段 E Exit Gate

- 对前 3 集生成 3 份合法报告；
- overall_score 与 need_revision 均由服务端规则得出；
- 每个问题有定位、原因和可执行建议；
- 低分集被正确标记；
- Evaluation Artifact 与剧本具体版本绑定；
- FakeLLM 全链路与真实模型手工 smoke 均可运行。

---

## 阶段 F：修订闭环、连续性门禁与版本管理

### 阶段目标

实现“选择最低分集 -> 修订计划 -> 新版本 -> 连续性检查 -> 重新评分 -> Diff”的闭环，同时保证旧版本和已有设定不被破坏。

### F-01 确定性选集与 RevisionPlan

- 预计：0.75 人日
- 依赖：阶段 E
- 修改文件：
  - backend/app/application/revision_service.py
  - backend/app/domain/revision.py
  - backend/app/skills/revision_plan.py
  - backend/app/prompts/templates/revision_plan.md
  - backend/tests/unit/revision/test_selector.py
  - backend/tests/unit/revision/test_plan.py
- 实现：
  - select_revision_candidate(reports)；
  - 只从 need_revision=true 中选 overall 最低者；
  - 同分按 episode_number；
  - 从 issue 生成 RevisionOperation；
  - 每个 operation 绑定 issue_ids、目标场景和 preserve；
  - max_change_ratio 默认 0.35。
- 验收：
  - [ ] 选择逻辑不调用 LLM；
  - [ ] 三集同分选最小集号；
  - [ ] 无 need_revision 时返回 None；
  - [ ] plan 不允许无来源 issue 的空泛任务；
  - [ ] locked_facts 写入计划。
- 测试：
  - pytest backend/tests/unit/revision/test_selector.py backend/tests/unit/revision/test_plan.py

### F-02 Revision Skill 与局部改写

- 预计：1 人日
- 依赖：F-01
- 修改文件：
  - backend/app/skills/reviser.py
  - backend/app/agents/revision.py
  - backend/app/prompts/templates/reviser.md
  - backend/tests/unit/skills/test_reviser.py
  - backend/tests/golden/revised_episode_football.json
- 实现：
  - 输入原稿、计划、StoryBible、当前 ContinuityState、当前集 Outline；
  - 输出完整的新 ScriptDraft，不输出原地 patch；
  - 在模型输入中显式列出 preserve 和禁止修改项；
  - 服务端重新计算文本指标；
  - 记录 operation 覆盖情况。
- 验收：
  - [ ] 新稿可被 ScriptDraft Schema 解析；
  - [ ] episode_number 与 title 规则不被误改；
  - [ ] 原稿 Artifact content 完全不变；
  - [ ] 每个 operation 有执行结果或未执行说明；
  - [ ] 新稿 source 包含原稿、评估、计划。
- 测试：
  - pytest backend/tests/unit/skills/test_reviser.py

### F-03 Continuity Validator

- 预计：1 人日
- 依赖：F-02、C-06
- 修改文件：
  - backend/app/memory/continuity.py
  - backend/app/tools/continuity_check.py
  - backend/app/domain/revision.py
  - backend/tests/unit/revision/test_continuity_check.py
- 实现：
  - 检查 locked facts 的保留/矛盾；
  - required events、角色、伏笔状态；
  - 关键人物状态变化；
  - 输出 ContinuityCheckResult：pass、violations、warnings；
  - 规则检查优先，必要语义检查通过独立 Skill 且结构化输出；
  - 失败转 needs_manual_review，不自动无限改写。
- 验收：
  - [ ] 固定事实被反转时失败；
  - [ ] 轻微措辞改变不误判为事实丢失；
  - [ ] required event 被删除时失败；
  - [ ] warnings 与 violations 分开；
  - [ ] 失败稿仍保存为 invalid/candidate 版本用于诊断。
- 测试：
  - pytest backend/tests/unit/revision/test_continuity_check.py

### F-04 Diff Service 与版本查询

- 预计：0.75 人日
- 依赖：F-02、B-04
- 修改文件：
  - backend/app/domain/diff.py（新增，模型层）
  - backend/app/tools/diff.py
  - backend/app/artifacts/diff_service.py
  - backend/app/api/v1/artifacts.py
  - backend/tests/unit/artifacts/test_diff.py
  - backend/tests/integration/api/test_artifact_versions.py
- 实现：
  - scene-aware diff，无法解析时回退 line diff；
  - added、removed、modified 统计；
  - change_ratio；
  - 版本列表和 GET /artifacts/diff；
  - 限制超大 diff 的响应体。
- 验收：
  - [x] 中文文本 Diff 不乱码；
  - [x] 可识别新增/删除/修改场景；
  - [x] A/B 颠倒时方向正确；
  - [x] 跨项目查询拒绝；
  - [x] change_ratio 被 Revision Gate 使用。
- 测试：
  - pytest backend/tests/unit/artifacts/test_diff.py backend/tests/integration/api/test_artifact_versions.py

### F-05 Revision Workflow 与重新评估

- 预计：1 人日
- 依赖：F-01 至 F-04、E-04
- 修改文件：
  - backend/app/workflows/revision.py
  - backend/app/workflows/nodes/select_revision.py
  - backend/app/workflows/nodes/revise.py
  - backend/app/workflows/nodes/continuity_check.py
  - backend/app/workflows/nodes/re_evaluate.py
  - backend/app/workflows/creation.py
  - backend/tests/workflow/test_revision_workflow.py
- 实现：
  - 接通主工作流 need_revision 分支；
  - revision_round 原子增加；
  - 只修订被选中的一个 Script；
  - pass 后保存 valid 新稿并重新评估；
  - fail 后保存候选稿与诊断，Run=needs_review；
  - 新评估只绑定新稿；
  - 重新评分下降超过 5 分标记 needs_manual_review。
- 验收：
  - [x] MAX_REVISION_ROUNDS=1 有效（test_round_budget_max_1_effective：重评仍低分 → round=1 非 2，停 needs_review）；
  - [x] 只有一个 episode 版本增加（test_happy_path：ep1 恰 2 版本、ep2/3 各 1）；
  - [x] 重试不会重复增加 revision_round（test_retry_does_not_double_increment）；
  - [x] 原稿与原评估可查询（test_happy_path 末段断言 orig 仍 valid、orig_eval overall=73.2）；
  - [x] 连续性失败不会进入 completed（test_continuity_failure_never_completes）。
- 测试：
  - pytest -m workflow backend/tests/workflow/test_revision_workflow.py

### F-06 Revision API 与闭环契约

- 预计：0.5 人日
- 依赖：F-05
- 修改文件：
  - backend/app/api/v1/revisions.py
  - backend/tests/integration/api/test_revisions.py
  - docs/API_CONTRACT.md
- 实现：
  - 自动修订和用户指定单集修订；
  - 可选 user_instruction，但不能绕过 locked facts；
  - 返回 Run；
  - 查询 RevisionPlan、ContinuityCheckResult、Diff 与新评估；
  - 权限与版本校验。
- 验收：
  - [x] 用户可指定一个合法 Script 版本（POST body `script_artifact_id` 任意版本，worker 覆盖"最新 valid"解析）；
  - [x] 已过期评估与新稿不匹配时拒绝（指定剧本无绑定 valid 评估 → 404 `EVALUATION_NOT_FOUND`）；
  - [x] 接口异步返回 202（POST → `RunResponse`，`schedule_worker` 后台执行独立修订图）；
  - [x] 完整结果可从 Artifact 链查询（`result_chain`：source_script/source_evaluation/candidate_script/continuity_check/new_evaluation/diff_ids）；
  - [x] OpenAPI 示例可直接运行（OpenAPI paths 含 `/revisions`；文档提供可运行 JSON 示例）。
- 测试：
  - pytest -m integration backend/tests/integration/api/test_revisions.py

### 阶段 F Exit Gate

固定 Demo 中：

1. 3 份评估报告中选择最低分集；
2. 只为该集生成 RevisionPlan；
3. 修订稿版本加 1，原稿 checksum 不变；
4. locked facts 与 required events 检查通过；
5. 新稿重新评分；
6. Diff 正确展示；
7. 将 fixture 改为破坏 locked fact 时，Run 进入 needs_review 而非 completed。

---

## 阶段 G：记忆、文件导入与导出

### 阶段目标

支持多轮对话继续创作、可靠解析 TXT/DOCX、识别输入类型，并把项目资产导出为 Markdown/DOCX。

### G-01 短期、中期与项目记忆

- 预计：0.75 人日
- 依赖：B-03、C-06
- 修改文件：
  - backend/app/memory/short_term.py
  - backend/app/memory/summary.py
  - backend/app/application/conversation_service.py
  - backend/app/prompts/templates/conversation_summary.md
  - backend/tests/unit/memory/test_short_term.py
  - backend/tests/integration/memory/test_summary.py
- 实现：
  - Redis 最近 N=12 条消息，设置 TTL；
  - PostgreSQL 消息为事实源；
  - 超过阈值生成 ConversationSummary Artifact；
  - Redis miss 时从 DB 恢复；
  - 项目记忆通过最新 Artifact 指针读取，不复制全文。
- 验收：
  - [x] Redis 丢失不丢消息（TestRedisShortTermRecovery：清空 short_term key 后 recent 从 Message 表恢复）；
  - [x] 摘要记录覆盖的消息范围（covered_from/to/message_count 落库）；
  - [x] 新消息不会被旧摘要覆盖（覆盖区间 1..2 → 3..5 连续不重叠）；
  - [x] 项目切换不串记忆（不同项目会话摘要互不可见，artifact 归属各自项目）；
  - [x] 摘要失败不阻断消息保存（FakeLLM 未注册 → RuntimeError → 消息仍落库、无摘要 artifact）。
- 测试：
  - pytest backend/tests/unit/memory/test_short_term.py backend/tests/integration/memory/test_summary.py

### G-02 Context Builder 完整化

- 预计：0.75 人日
- 依赖：G-01、D-05
- 修改文件：
  - backend/app/domain/context.py（新增）
  - backend/app/memory/context_builder.py（重写 build_for）
  - backend/app/memory/summary.py（latest_project_summary_text）
  - backend/app/workflows/nodes/write_episode.py（接入 build_for）
  - backend/app/workflows/nodes/retrieve.py（回填 stage_chunk_ids）
  - backend/app/domain/script.py（EpisodeWriterInput.assembled_context）
  - backend/app/skills/episode_writer.py（渲染 assembled_context）
  - backend/app/prompts/templates/episode_writer_v2.md（v1.1.0）+ manifest.yaml
  - backend/tests/unit/memory/test_context_budget.py（新增）
  - backend/tests/integration/memory/test_summary_reaches_writer.py（新增，Exit Gate）
- 实现：
  - 针对 requirement/story_bible/outline/writer/evaluator/reviser 的不同策略；
  - token estimator adapter；
  - 相关角色、伏笔和相邻集过滤；
  - context_manifest 保存 token 估算和裁剪原因；
  - 明确超限异常 ContextTooLarge。
- 验收：
  - [x] 不同任务上下文组成不同（6 任务策略各自独立，test_context_budget::TestTaskPolicies）；
  - [x] 任何构建结果都保留输出缓冲（current_target 完整保留，TestOutputBuffer）；
  - [x] 当前稿件不能无提示截断（超预算抛 ContextTooLargeError，TestContextTooLarge）；
  - [x] 旧会话优先摘要（write_episode 节点 previous_summary_continuity = 会话摘要 + 连续性，集成测试 test_summary_reaches_writer 断言摘要进入组装上下文）；
  - [x] 测试覆盖边界 token 预算（TestBoundaryBudget）。
- 测试：
  - pytest backend/tests/unit/memory/test_context_budget.py
  - pytest backend/tests/integration/memory/test_summary_reaches_writer.py

### G-03 安全上传与 TXT/DOCX Parser

- 预计：1 人日
- 依赖：B-03
- 修改文件：
  - backend/app/storage/protocol.py（FileStore 协议：save/open/exists/delete）
  - backend/app/storage/local.py（LocalFileStore：UUID 键、原子落盘、防路径穿越）
  - backend/app/tools/file_parser.py（FileParserTool + ParsedFile）
  - backend/app/core/errors.py（+InvalidFileTypeError 415/FileTooLargeError 413/FileParseFailedError 422）
  - backend/app/db/models/upload.py（+original_name/parse_status/char_count/warnings）
  - backend/migrations/versions/0003_upload_metadata.py
  - backend/app/db/repositories/uploads.py（UploadRepository）
  - backend/app/api/v1/uploads.py（POST/GET /projects/{id}/uploads）
  - backend/app/api/v1/router.py（include uploads_router）
  - backend/tests/unit/tools/test_file_parser.py
  - backend/tests/integration/api/test_uploads.py
- 实现：
  - TXT 编码探测，优先 UTF-8，GBK 回退（回退记 warning）；
  - DOCX 读取段落和表格文本（python-docx）；
  - 文件大小（upload_max_bytes）、MIME、签名（zip 魔数）和后缀联合校验；
  - SHA-256、服务端 UUID 文件名、原子落盘（tmp + os.replace）；
  - 文本长度和解析警告；
  - 拒绝宏（docm/vbaProject）、损坏压缩包（BadZipFile 映射 422）和路径穿越。
- 验收：
  - [x] 中文 TXT/DOCX 不乱码（单元 test_chinese_docx_encoding + 集成 test_upload_chinese_txt 落盘回读一致）；
  - [x] 空文件和损坏文件返回明确错误（空 TXT→201 char_count=0；.docx 非 zip→422 FILE_PARSE_FAILED）；
  - [x] 伪装扩展名被拒绝（.txt 内容为 zip → 422；集成 test_upload_disguised_extension）；
  - [x] 原始文件名不用于磁盘路径（集成断言 path 为 UUID 键不含客户端名；跨项目隔离测试）；
  - [x] 文件内容不写日志（file_parser 仅 log warning 异常消息不含内容；上传 API 不写正文）。
- 测试：
  - pytest backend/tests/unit/tools/test_file_parser.py backend/tests/integration/api/test_uploads.py

### G-04 Import Classification 与工作流路由

- 预计：0.75 人日
- 依赖：G-03、C-02
- 修改文件：
  - backend/app/domain/import_file.py（ImportClassificationInput / ImportClassification）
  - backend/app/skills/import_classifier.py（ImportClassifierSkill：规则先行 + LLM 兜底）
  - backend/app/workflows/router.py（route_import 纯函数路由表）
  - backend/app/workflows/import_file.py（ImportFileWorkflow 单节点状态图）
  - backend/app/prompts/templates/import_classifier.md + manifest.yaml 条目（owner classifier）
  - backend/app/prompts/loader.py（_auto_register_domain_schemas 注册 ImportClassification(+Input)）
  - backend/app/application/artifact_service.py（_SCHEMA_MAP += import_classification）
  - backend/app/artifacts/versions.py（compute_input_hash：dedup_extra 无源时也参与哈希）
  - backend/app/api/v1/runs.py（action=import 分支 + upload_id config + fake fixture 注册）
  - backend/tests/golden/import_classification_{outline,full_script,unknown}.json
  - backend/tests/integration/workflow/test_import_workflow.py
  - backend/tests/unit/workflow/test_import_router.py
  - backend/tests/unit/artifacts/test_versions.py（+dedup_extra 无源哈希测试）
- 实现：
  - 分类：idea_or_notes、outline、full_script、reference、unknown；
  - 规则特征先行（字符数/行数/场景标记/分集标记/对白行数/参考关键词），命中即返回不调 LLM；
  - LLM 兜底（prompt "import_classifier"）只处理规则未命中的模糊文本；
  - route_import：outline/idea_or_notes -> create，full_script -> evaluate，reference -> hold，unknown -> needs_user_input；
  - import_file 节点：读 FileStore + G-03 Parser 解析 → 分类 → 持久化 import_classification Artifact（dedup_extra=f"upload:{upload_id}" 幂等）→ 确定性路由；
  - 修复 compute_input_hash：dedup_extra 单独出现（无源）时返回哈希而非 None，使无源独立产物（会话摘要/导入分类）可幂等去重。
- 验收：
  - [x] 固定 Outline 和剧本 fixture 分类正确（test_llm_fallback_outline → outline；test_rules_hit_full_script → full_script）；
  - [x] reference 不会自动污染知识库（test_rules_hit_reference_no_llm → route=hold，不进入 create/evaluate）；
  - [x] 分类 Artifact 可查询（各用例经 _get_classification_artifact 断言 content/content_type/detected_features）；
  - [x] unknown 不误启动昂贵生成（unknown → needs_user_input=True，test_rules_hit_short_unknown / test_llm_fallback_unknown_needs_user_input）；
  - [x] 路由行为有 contract test（tests/unit/workflow/test_import_router.py：全覆盖 + ImportRoute 字面量 + reference 不自动入库）。
- 测试：
  - pytest tests/unit/workflow/test_import_router.py tests/integration/workflow/test_import_workflow.py（9 用例，含归属校验/幂等/失败路径）

### G-05 Markdown 与 DOCX Exporter

- 预计：1 人日
- 依赖：B-04、F-04
- 修改文件：
  - backend/app/tools/exporters/markdown.py
  - backend/app/tools/exporters/docx.py
  - backend/app/application/export_service.py
  - backend/app/storage/local.py
  - backend/tests/unit/export/test_markdown.py
  - backend/tests/integration/export/test_docx.py
- 实现：
  - 可选导出 StoryBible、大纲、最新剧本、评估、修订说明；
  - 默认只导出 latest valid；
  - Markdown 标题层级稳定；
  - DOCX 设置标题、页眉、页码、分页与中文字体 fallback；
  - 先写临时文件再原子发布；
  - 导出文件保存为 ExportFile Artifact。
- 验收：
  - [x] Markdown 不包含内部 ID/Prompt/Token；
  - [x] DOCX 可打开，中文、表格和分页正常；
  - [x] 3 集按集号排序；
  - [x] 用户可显式选择版本；
  - [x] 导出失败不生成 valid ExportFile。
- 测试：
  - pytest backend/tests/unit/export/test_markdown.py backend/tests/integration/export/test_docx.py

### G-06 Export API 与导入导出集成

- 预计：0.75 人日
- 依赖：G-04、G-05
- 修改文件：
  - backend/app/api/v1/exports.py
  - backend/app/api/v1/runs.py（action=export 分支 + _resolve_upload_text + evaluate 入 Worker 名单）
  - backend/app/workflows/import_file.py（full_script → 确定性 script_draft 入库）
  - backend/app/tools/script_text.py（full_script_to_script_draft）
  - backend/tests/integration/api/test_exports.py
  - backend/tests/integration/api/test_upload_to_export.py（两条导入路径端到端；测试经 API + Worker 装配，故落在 integration/api）
  - backend/tests/unit/tools/test_script_text.py
  - docs/API_CONTRACT.md
- 实现：
  - POST export 返回 Run；
  - 下载使用安全 Content-Disposition；
  - 文件存在性和所属项目验证；
  - 上传 Outline -> 创作 -> 导出测试；
  - 上传完整剧本 -> 评估 -> 导出测试。
- 验收：
  - [x] 下载文件名安全且可读；
  - [x] 不能下载其他项目文件；
  - [x] 文件丢失返回 EXPORT_FILE_MISSING；
  - [x] 两条导入路径均端到端可运行；
  - [x] Export Artifact source links 完整。
- 测试：
  - pytest backend/tests/integration/api/test_exports.py backend/tests/integration/api/test_upload_to_export.py

### 阶段 G Exit Gate

- 多轮会话后继续生成能读取摘要和项目资产；
- 清空 Redis 后仍可恢复上下文；
- TXT/DOCX 均可上传、解析和分类；
- Outline 文件能进入创作流程；
- 完整剧本文件能进入评估流程；
- 项目可以导出 Markdown 与可正常打开的 DOCX。

---

## 阶段 H：前端工作台与完整 Demo

### 阶段目标

将后端能力组织成可操作、可观察、可恢复的前端工作台。MVP 重视流程完整和状态清楚，不追求复杂视觉编辑器。

### H-01 前端基座、API Client 与类型生成

- 预计：0.5 人日
- 依赖：B-01、API 契约稳定
- 修改文件：
  - frontend/src/app/layout.tsx
  - frontend/src/lib/api-client.ts
  - frontend/src/lib/query-client.ts
  - frontend/src/types/api.generated.ts
  - frontend/src/components/*
  - frontend/tests/*
- 实现：
  - Next.js App Router；
  - OpenAPI 生成 TypeScript 类型；
  - API error 统一展示；
  - TanStack Query；
  - 基础布局、loading、empty、error state；
  - 测试 mock server。
- 验收：
  - [ ] 前端不手写重复 API 类型；
  - [ ] request_id 在错误详情可见；
  - [ ] loading/error/empty 均有组件；
  - [ ] API base URL 从环境变量读取；
  - [ ] 单元测试不依赖后端在线。
- 测试：
  - pnpm lint
  - pnpm typecheck
  - pnpm test

### H-02 项目列表与创建项目

- 预计：0.5 人日
- 依赖：H-01、B-03
- 修改文件：
  - frontend/src/app/projects/page.tsx
  - frontend/src/app/projects/new/page.tsx
  - frontend/src/features/projects/*
  - frontend/tests/projects.test.tsx
- 实现：
  - 项目列表、创建表单、状态标签；
  - 输入标题、题材、目标集数；
  - 创建成功跳转工作台；
  - 表单校验与 API 错误。
- 验收：
  - [ ] 创建与刷新后项目仍存在；
  - [ ] 非法集数不能提交；
  - [ ] 空列表有引导；
  - [ ] API 错误不丢用户输入。
- 测试：
  - pnpm test -- projects

### H-03 对话输入、上传与 SSE 进度

- 预计：1 人日
- 依赖：H-02、B-05、G-03
- 修改文件：
  - frontend/src/app/projects/[id]/page.tsx
  - frontend/src/features/conversation/*
  - frontend/src/features/runs/*
  - frontend/src/hooks/use-run-events.ts
  - frontend/tests/run-events.test.tsx
- 实现：
  - 对话输入、文件上传；
  - 创建 Run；
  - SSE 自动重连、Last-Event-ID；
  - 节点进度、当前阶段、失败、重试、取消；
  - 事件只存概要，Artifact 单独拉取；
  - 页面刷新后恢复活跃 Run。
- 验收：
  - [ ] 能从 Idea 启动创建；
  - [ ] 上传进度和解析错误可见；
  - [ ] SSE 断开后自动恢复；
  - [ ] 重复点击不创建重复 Run；
  - [ ] 失败节点和错误码清晰展示。
- 测试：
  - pnpm test -- run-events

### H-04 StoryBible 与分集大纲视图

- 预计：0.5 人日
- 依赖：H-03、C-08
- 修改文件：
  - frontend/src/features/story-bible/*
  - frontend/src/features/outlines/*
  - frontend/src/app/projects/[id]/story-bible/page.tsx
  - frontend/src/app/projects/[id]/outlines/page.tsx
- 实现：
  - 角色卡、locked facts、伏笔；
  - 10 集大纲列表和单集展开；
  - Artifact 版本选择；
  - 来源与生成状态；
  - 暂不实现富文本直接编辑。
- 验收：
  - [ ] 10 集排序稳定；
  - [ ] 空字段有明确提示而非页面崩溃；
  - [ ] 可以切换历史版本；
  - [ ] locked facts 视觉上可识别。
- 测试：组件测试 + API mock。

### H-05 剧本编辑视图与评估报告

- 预计：0.75 人日
- 依赖：H-04、阶段 E
- 修改文件：
  - frontend/src/features/scripts/*
  - frontend/src/features/evaluations/*
  - frontend/src/app/projects/[id]/scripts/[episode]/page.tsx
  - frontend/tests/evaluation-report.test.tsx
- 实现：
  - 左侧集数、中央剧本、右侧评分；
  - 9 维分数、总分、strengths、issues；
  - 点击 issue 定位 scene；
  - 版本与评估绑定显示；
  - 手动发起重新评估。
- 验收：
  - [ ] 不把旧评估显示在新稿上；
  - [ ] issue 能定位或明确“全局问题”；
  - [ ] risk flags 明显展示；
  - [ ] 评估中、失败和无报告状态完整。
- 测试：
  - pnpm test -- evaluation-report

### H-06 修订、版本与 Diff 页面

- 预计：0.75 人日
- 依赖：H-05、阶段 F
- 修改文件：
  - frontend/src/features/revisions/*
  - frontend/src/features/diff/*
  - frontend/src/app/projects/[id]/versions/page.tsx
  - frontend/tests/diff-view.test.tsx
- 实现：
  - RevisionPlan、执行结果和连续性检查；
  - 原稿/修订稿版本选择；
  - scene/line diff；
  - 重新评分对比；
  - needs_manual_review 提示；
  - 不提供覆盖旧版本按钮。
- 验收：
  - [x] 新增、删除、修改显示清楚；（DiffView 徽章 + 行级红/绿 + scene_summary 计数）
  - [x] 分数下降不会被包装成成功提升；（ScoreComparison `scoreDelta` 负 delta 红「↓ 下降」，测试断言不含「提升」）
  - [x] 连续性失败有具体 violation；（ContinuityCheckView kind→中文标签 + source 徽章 + 目标/期望/实际/证据）
  - [x] 原稿可随时查看；（RevisionDetail「查看原稿」按钮 + ScriptView 全文）
  - [x] 大 diff 页面不会卡死；（后端 >2000 行截断 + SceneCard 惰性渲染 body + >20 场景默认折叠，21 场景测试锁定）
- 测试：
  - pnpm test -- diff-view
  - pnpm test -- revisions-view

### H-07 导出中心与 Playwright E2E

- 预计：1 人日
- 依赖：H-06、G-06
- 修改文件：
  - frontend/src/features/exports/*
  - frontend/src/app/projects/[id]/exports/page.tsx
  - e2e/dramaagent.spec.ts
  - e2e/fixtures/*
  - docker-compose.e2e.yml
- 实现：
  - 选择导出内容和格式；
  - 显示导出历史与下载；
  - Playwright 固定 Demo；
  - E2E 使用 FakeLLM/FakeEmbedder；
  - 截图/trace 只在失败时保留；
  - 覆盖刷新、SSE 重连、版本 Diff。
- 验收：
  - [x] 用户从空项目完成整个 Demo；（e2e/dramaagent.spec.ts 单用例串行完成，`make e2e REPEAT=5` → 5 passed）
  - [x] 自动生成 10+3、评估、修订、Diff、导出；（spec 逐段断言 StoryBible / 10 集大纲 / 3 集剧本 / 低分评估 / 1 条修订 / v1→v2 Diff / MD+DOCX 下载）
  - [x] E2E 可重复运行至少 5 次；（`make e2e REPEAT=5` → `5 passed (15.0s)`）
  - [x] 每次只修订一个低分集；（expectExactlyOneRevision 断言修订列表恰好 1 条且为第 1 集）
  - [x] 下载文件存在且非空；（expectDownloadNotEmpty 断言 statSync size > 0）
- 测试：
  - pnpm playwright test

### 阶段 H Exit Gate

非开发者可按以下路径完成：

创建项目 -> 输入 Idea -> 查看实时进度 -> 查看 StoryBible -> 查看 10 集大纲 -> 查看 3 集剧本 -> 查看评分 -> 查看最低分集修订 -> 对比版本 -> 下载 DOCX。

全过程不使用 Swagger、不进入数据库、不需要手工刷新状态。

---

## 阶段 I：稳定性、扩展接口与发布

### 阶段目标

完成错误恢复、成本保护、可观测性、安全检查、MCP/Skill 扩展契约和交付文档，使 MVP 可稳定演示并可继续迭代。

### I-01 幂等、重试、取消与成本保护

- 预计：0.75 人日
- 依赖：阶段 H
- 修改文件：
  - backend/app/llm/{retry,budget}.py（新增）
  - backend/app/llm/{openai_compatible,fake}.py（重试/预算接入）
  - backend/app/workflows/checkpoint.py（取消注册表+失败分类+状态检查点）
  - backend/app/application/run_service.py（状态机 running→cancelled、cancel 协作式）
  - backend/app/api/v1/runs.py（POST /runs/{id}/retry、worker 预算/checkpoint 恢复）
  - backend/app/db/models/workflow_run.py + migrations/versions/0004（error 列）
  - backend/app/workflows/nodes/* + import_file.py（入口取消/失败短路守卫）
  - backend/tests/{unit/llm/test_retry.py, integration/workflow/test_recovery_matrix.py, integration/api/test_run_recovery.py}
- 实现：
  - 节点错误分类与 retryable 标记；
  - 指数退避与 provider Retry-After；
  - per-run 调用次数/token 软硬上限；
  - cancel cooperative check；
  - checkpoint 恢复矩阵；
  - 避免重复计费节点；
  - 静态边节点失败级联修复（12 节点 status==failed 短路守卫）。
- 验收：
  - [x] 429、timeout、invalid schema 走不同策略；
  - [x] 达硬上限后 Run 明确失败；
  - [x] cancel 后不创建新 Artifact；
  - [x] 恢复不会重复成功节点；
  - [x] 所有失败有 error_code。
- 测试：
  - pytest backend/tests/unit/llm/test_retry.py backend/tests/integration/workflow/test_recovery_matrix.py backend/tests/integration/api/test_run_recovery.py

### I-02 可观测性与运行诊断

- 预计：0.5 人日
- 依赖：I-01
- 修改文件：
  - backend/app/observability/metrics.py
  - backend/app/observability/tracing.py
  - backend/app/core/logging.py
  - docs/OPERATIONS.md
  - backend/tests/unit/observability/*
- 实现：
  - 第 10.4 节指标；
  - request -> run -> node -> llm_call 关联；
  - 日志脱敏测试；
  - /metrics 可由配置开关；
  - Run 诊断接口返回阶段耗时、调用数和错误概要。
- 验收：
  - [x] 可根据 run_id 找到完整节点时间线；
  - [x] 可统计一次 Demo 的调用次数与 token；
  - [x] 日志脱敏自动测试通过；
  - [x] 指标标签无 project_id 等高基数值。
- 测试：
  - pytest backend/tests/unit/observability
  - pytest backend/tests/integration/api/test_metrics_endpoint.py test_diagnostics_endpoint.py

### I-03 安全、文件与内容回归

- 预计：0.5 人日
- 依赖：G-03、H-07
- 修改文件：
  - backend/app/core/security.py
  - backend/tests/security/*
  - frontend/tests/security/*
  - docs/SECURITY.md
- 实现：
  - 路径穿越、恶意文件名、超大上传；
  - HTML/script 输出转义；
  - CORS、下载权限；
  - Prompt injection 内容与系统指令隔离；
  - 密钥/Prompt/全文日志扫描；
  - 数据删除策略说明。
- 验收：
  - [x] 常见路径穿越 fixture 全被拒绝；
  - [x] 剧本文本不能执行脚本；
  - [x] 上传和下载均验证项目归属；
  - [x] Prompt injection 文本被当作内容而非系统指令；
  - [x] 安全文档注明 MVP 局限。
- 测试：
  - pytest backend/tests/security
  - pnpm test -- security

### I-04 MCP Adapter 与 Skill 插件契约

- 预计：0.5 人日
- 依赖：B-07
- 修改文件：
  - backend/app/integrations/mcp/protocol.py
  - backend/app/integrations/mcp/adapter.py
  - backend/app/skills/registry.py
  - backend/tests/contract/test_mcp_adapter.py
  - docs/EXTENSIONS.md
- 实现：
  - MCPToolAdapter 把外部 tool metadata 映射到 Tool Protocol；
  - timeout、错误和 Schema 边界；
  - Skill 元数据查询/执行；
  - 使用本地 FakeMCP Server 做 contract test；
  - 核心 File/RAG/Export 仍使用内部实现。
- 验收：
  - [x] 无 MCP 配置时主流程完全可用；
  - [x] Fake MCP Tool 可注册、调用、超时；
  - [x] 外部错误不会泄露内部连接信息；
  - [x] 重名策略明确；
  - [x] 文档提供新增 Skill 的最小示例。
- 测试：
  - pytest backend/tests/contract/test_mcp_adapter.py

### I-05 性能、覆盖率与全量回归

- 预计：0.5 人日
- 依赖：I-01 至 I-04
- 修改文件：
  - backend/tests/performance/*
  - e2e/dramaagent.spec.ts
  - docs/TEST_REPORT.md
  - CI 配置
- 实现：
  - 普通 API p95；
  - 100 个并发 SSE 测试的基础版本；
  - 1,000 Artifact 查询；
  - 5 次 E2E 重复；
  - 核心覆盖率 85%、总体 75% 门禁；
  - 生成测试报告。
- 验收：
  - [x] 达到第 1.6 节指标（普通 API p95 < 300ms、100 并发 SSE < 1s、1000 Artifact 分页 < 300ms，实测见 docs/TEST_REPORT.md §4）；
  - [x] 无未解释 flaky test（E2E「创建项目」strict-mode 选择器竞态已修复；SSE 断开日志噪声为预期并写入 TEST_REPORT §7）；
  - [x] E2E 5/5 通过（make e2e REPEAT=5，隔离 postgres/redis）；
  - [x] 内存/连接在测试后释放（SSE gauge 回落基线断言 + 性能测试引擎显式 NullPool）；
  - [x] 报告区分含/不含 LLM 的耗时（docs/TEST_REPORT.md §4.1）。
- 测试：
  - make ci
  - make e2e
  - make perf

### I-06 交付文档、Demo 数据与发布候选

- 预计：0.25 人日
- 依赖：I-05
- 修改文件：
  - README.md
  - docs/OPERATIONS.md
  - docs/API_CONTRACT.md
  - docs/DEMO.md
  - docs/KNOWN_LIMITATIONS.md
  - CHANGELOG.md
- 实现：
  - 安装、配置、启动、迁移、备份与恢复；
  - 固定 Demo 步骤；
  - 常见错误排查；
  - 数据库/文件备份说明；
  - 已知限制和 V1 backlog；
  - 标记 v0.1.0-rc1。
- 验收：
  - [x] 新开发者只读 README 可运行 Demo（README 快速启动 + docs/DEMO.md 固定步骤）；
  - [x] 所有命令与实际一致（Makefile/命令表与 scripts/ 对齐核对）；
  - [x] 迁移和回滚步骤经过验证（alembic upgrade head 在 E2E/性能测试反复执行；回滚步骤见 OPERATIONS.md）；
  - [x] 没有占位链接和未说明 TODO（KNOWN_LIMITATIONS 显式列出 MVP 接受项与 backlog，无"未说明"挂账）；
  - [x] 发布清单由另一人或独立 AI Review 检查（Phase I Exit Gate 独立审查，见 §16.4）。

### 阶段 I Exit Gate / MVP Release Gate

- make ci 全绿；
- make e2e 连续 5 次通过；
- 安全回归全绿；
- 核心覆盖率与性能指标达标；
- 使用 FakeLLM 可离线完成 Demo；
- 使用真实 LLM 至少成功完成一次人工 smoke；
- Run 失败、恢复、取消均有演示记录；
- Markdown/DOCX 导出均人工打开检查；
- v0.1.0-rc1 发布说明和已知限制完整。

---

## 13. 开发进度总表

### 13.1 使用规则

- Status 初始均为 TODO；
- 开始时改为 DOING，并填写负责人、开始日期；
- 等待外部输入时改为 BLOCKED，必须写 blocker；
- 完成开发但未验收时改为 REVIEW；
- 验收通过后改为 DONE，并填写证据链接、测试命令或提交号；
- 每个阶段 DONE 后，把对应 Exit Gate 作为一条单独验收记录；
- 不允许只凭“代码已生成”标记完成。

### 13.2 Master Tracker

| ID | 交付项 | 预计 | 依赖 | Status | Owner | Evidence / Blocker |
| --- | --- | ---: | --- | --- | --- | --- |
| A-01 | Monorepo 与开发命令 | 0.5d | - | DONE | AI Agent | 后端: pytest 2/2 passed, Ruff/mypy clean; 前端: Vitest 1/1 passed, ESLint/tsc clean; lock 文件已提交 |
| A-02 | Compose 与配置 | 0.75d | A-01 | DONE | AI Agent | pytest 14/14 passed (config), Ruff/mypy clean; docker-compose.yml 可用 |
| A-03 | 领域 Schema 与 fixtures | 1d | A-01 | DONE | AI Agent | pytest 53/53 contract tests passed, Ruff/mypy clean; 14 golden fixtures; 97.44% coverage |
| A-04 | CI 与质量门禁 | 0.75d | A-01,A-03 | DONE | AI Agent | CI workflow 创建; 后端 97.44% coverage (≥70%); Ruff/mypy/ESLint/tsc 全绿; TEST_PLAN.md 完成 |
| B-01 | FastAPI、错误与健康检查 | 0.75d | A-02 | DONE | AI Agent | pytest 86/86 passed (含 15 个集成测试), Ruff/mypy clean; create_app+request_id+health+ErrorResponse+JSON logging |
| B-02 | ORM、Migration、Repository | 1.25d | B-01,A-03 | DONE | AI Agent | pytest 92/92 passed (含 6 migration tests), Ruff/mypy clean; 11 tables + alembic + repository pattern |
| B-03 | Project/Conversation API | 0.75d | B-02 | DONE | AI Agent | pytest 92/92 passed, Ruff/mypy clean; 8 API endpoints + domain schemas + application services |
| B-04 | Artifact Store 与版本 | 1d | B-02,A-03 | DONE | AI Agent | pytest 104/104 passed, Ruff/mypy clean; ArtifactStore + versions + schema validation + 5 API endpoints |
| B-05 | Run/Event/SSE/Worker | 1d | B-02,B-03 | DONE | AI Agent | pytest 104/104 passed, Ruff/mypy clean; state machine + SSE + idempotency + EventPublisher |
| B-06 | LLM Protocol 与 FakeLLM | 0.75d | A-02,A-03 | DONE | AI Agent | pytest 116/116 passed, Ruff/mypy clean; LLMClient+StructuredOutputParser+FakeLLM(fixture+故障注入) |
| B-07 | Agent/Tool/Skill Registry | 0.5d | B-06 | DONE | AI Agent | pytest 129/129 passed, Ruff/mypy clean; BaseAgent+ToolRegistry+SkillRegistry+EchoTool/Skill |
| C-01 | Prompt Loader 与版本 | 0.5d | B-06 | DONE | AI Agent | pytest 219/219 passed (含 38 contract tests); 6 模板 + manifest + PromptLoader + SchemaRegistry; Ruff/mypy clean; PROMPT_GUIDE.md 完成 |
| C-02 | Requirement Skill | 0.75d | C-01,B-07 | DONE | AI Agent | pytest 219/219 passed (含 14 skill tests); RequirementSkill + RequirementInput + NeedsUserInput; 关键词检测阻断; golden fixture |
| C-03 | StoryBible Skill | 1d | C-02 | DONE | AI Agent | pytest 219/219 passed (含 14 skill tests); StoryBibleSkill + StoryBibleInput + CreationAgent; 角色校验质量门禁; golden fixture |
| C-04 | Outline Skill | 1d | C-03 | DONE | AI Agent | pytest 219/219 passed (含 13 skill tests); OutlineSkill + OutlineInput; validate_characters + validate_sequence; golden fixture |
| C-05 | Episode Writer 与文本工具 | 1.25d | C-04,B-07 | DONE | AI Agent | pytest 219/219 passed (含 25 C-05 tests); EpisodeWriterSkill + WordCountTool + DialogueRatioTool; 指标覆盖 + 角色追溯; Ruff/mypy clean |
| C-06 | Continuity 与 Context 基础 | 1d | C-03..C-05 | DONE | AI Agent | pytest 383/383 passed (48 个新测试), Ruff/mypy clean; ContinuityManager + ContextBuilder + SummarizerSkill |
| — | OpenAICompatibleLLM + 测试脚本 | 0.5d | B-06,C-06 | DONE | AI Agent | pytest 362/362 passed, Ruff clean; 26 LLM 单元测试; 5 个 Skill 全部通过真实验证（qwen3.7-plus） |
| C-07 | Creation Workflow | 1.25d | C-02..C-06,B-05 | DONE | AI Agent | pytest 383/383 passed (8 个 workflow tests), Ruff/mypy clean; LangGraph 6 节点串联 + 条件路由 + 重试复用 |
| C-08 | Creation API 纵切 | 0.5d | C-07,B-03 | DONE | AI Agent | pytest 383/383 passed (13 个契约测试), Ruff/mypy clean; POST create_script → Worker → Artifact 查询全链路, API_CONTRACT.md 完成 |
| D-01 | 知识分类与治理 | 0.5d | C Gate | DONE | AI Agent | 11 metadata tests passed, Ruff/mypy clean; knowledge/ 语料 18 篇原创(mvp_v1)+ README/VERSION; rag/models.py(KnowledgeCategory 7 类 + KnowledgeDocMetadata extra=forbid + parse_frontmatter + corpus version) + KnowledgeDocument 元数据列扩展; 全量单元回归 |
| D-02 | Loader/Chunker/摄取 | 0.75d | D-01,B-02 | DONE | AI Agent | 398 tests passed (12 migration + 16 RAG 集成 + 370 单元), Ruff/mypy clean; 迁移 0002(元数据列 + HNSW cosine 索引, 真实 downgrade/upgrade 往返验证); rag/loader.py + chunker.py(标题层级/父路径/chunk_hash) + db/repositories/knowledge.py(幂等摄取: hash 跳过/只重建变化块/删源文件不物理删除) + app/cli/knowledge.py(ingest/status, argparse); CLI 冒烟: 首次 18 新增/二次 18 跳过/status 18 文档 18 块; 修复 ORM embedding 可空漂移(与 0001 迁移对齐) |
| D-03 | Embedder 与 pgvector | 0.75d | D-02 | DONE | AI Agent | 427 tests passed (21 embedder 单元 + 8 pgvector 集成), Ruff/mypy clean; rag/embedder.py(Embedder ABC + OpenAICompatibleEmbedder HTTP 批处理/重试/缓存 + FakeEmbedder 确定性归一化伪向量 + load_embedder 工厂 + 维度校验) + EmbeddingResult + KnowledgeRepository(update_chunk_embedding/backfill_document_embeddings/search_similar pgvector <=>) + KnowledgeSearchHit |
| D-04 | Retriever 与 Trace | 1d | D-03 | DONE | AI Agent | 441 tests passed (12 retriever 单元 + 2 新增 pgvector 集成), Ruff/mypy clean; rag/retriever.py(Retriever + RetrieveConfig, 去重/稳定排序/每文档上限/短 ID slug-n) + domain/retrieval.py(RetrievedChunk/RetrievalResult/RetrievalTrace/NullRetriever) + tests/golden/rag_queries.json(7 类每类 2-3 条) + repo search_similar 增 genre/stage 过滤与稳定排序 |
| D-05 | 创作链路接入 RAG | 1d | D-04,C-07 | DONE | AI Agent | 697 tests passed (10 新增: 6 retriever 单元 + 4 workflow 集成), 仅 2 存量日志失败, Ruff/mypy 零新增; rag/retriever.py 增 retrieve_for_stage(阶段→分类映射: story_bible→genre_template+character_archetype; outline→genre_template+opening_hook; writer→payoff+character_archetype; 跨分类合并/去重/截断/重编 slug) + NullRetriever.retrieve_for_stage 同签名; retrieve 节点改直通为真实检索(归一化需求构建 query, 三阶段各检索一次, 写 ctx[stage]_rag 与合并 rag_context 向后兼容, 每阶段持久化 RetrievalTrace Artifact, 检索失败降级空上下文); 三 Skill 节点消费各自阶段 rag_context; ContextManifest 增 rag_chunk_ids; ArtifactType 增 retrieval_trace(contract 同步 12 类); tests/golden/rag_expectations.json + test_creation_with_rag.py(hit@5≥90% 结构性保证、三阶段过滤不同、NullRetriever 降级主流程可运行、trace 含 corpus_version+chunk IDs 不含全文); 修复三阶段 trace 共享 source 致 input_hash 幂等去重成一条(dedup_extra=stage 区分) |
| E-01 | Rubric 与确定性指标 | 0.75d | D-01,A-03 | DONE | AI Agent | 25 tests passed, 新增文件 Ruff/mypy clean; knowledge/rubric/mvp_v1.yaml + Rubric 模型(权重和=1/9维/锚点校验) + load_rubric + ScriptStructureTool; 权重与 enums 一致性测试; 存量失败与 HEAD 基线相同 |
| E-02 | Evaluation Skill | 1d | E-01,C-05,D-04 | DONE | AI Agent | 8 tests passed, Ruff/mypy clean; EvaluationSkill(evaluate_episode)+EvaluationAgent+EvaluationInput; 服务端回填 overall/need_revision, 低分维度自动补 issue, evidence 限长/scene 降级; prompt 升 v1.1.0 + manifest/哈希快照同步; 存量失败与 HEAD 基线一致 |
| E-03 | Evaluation Service/API | 0.75d | E-02,B-04 | DONE | AI Agent | 7 tests passed, Ruff/mypy clean; EvaluationService(evaluate_script/many, 跨项目防护, 幂等复用, 版本绑定)+ GET /evaluations + GET /evaluations/for-script; repository 按 content.script_artifact_id 查询; 存量失败与 HEAD 基线一致 |
| E-04 | Evaluation Workflow | 0.75d | E-03,C-07 | DONE | AI Agent | 5 new tests passed; evaluation_workflow + evaluate_episodes 节点 + CreationState 扩展 + creation 自动评估分支(低分→needs_revision_decision) + runs action=evaluate + FakeLLM fixture; 修复 workflow conftest 事务冲突(存量 6 失败→0); 全量 434 passed/2 存量失败, Ruff clean, mypy app/ 与 HEAD 持平 |
| E-05 | 评估 Golden 回归 | 0.75d | E-04 | DONE | AI Agent | 15 tests passed, Ruff/mypy clean; evaluation_cases/{high,medium,low} + test_evaluation_invariants(结构/服务端回填/低分补issue/FakeLLM确定性) + evaluate_rubric_smoke.py(真实LLM手工smoke,无密钥) + TEST_PLAN.md §10 |
| F-01 | 选集与 RevisionPlan | 0.75d | E Gate | DONE | AI Agent | 28 tests passed, Ruff/mypy clean; domain/revision.py 增 select_revision_candidate(纯函数: 只从 need_revision 选 overall 最低, 同分取最小集号) + operations_from_issues(issue→operation 绑定 issue_ids/场景/preserve) + filter_grounded_operations(剔除无来源空泛任务); RevisionPlanSkill(LLM 生成 + 有据可依校验 + 确定性兜底 + 权威字段覆盖/场景钳制/锁定事实并入 preserve) + revision_plan.md v1.0.0 + manifest/loader/openai 注册; RevisionService(选集→锁事实→计划→持久化, 跨项目防护); 全量 477 passed/2 存量日志失败 |
| F-02 | Revision Skill | 1d | F-01 | DONE | AI Agent | 30 tests passed, Ruff/mypy clean(改动文件); domain/revision.py 增 RevisionTaskInput(原稿/计划/StoryBible/大纲/连续性状态) + OperationExecution(applied/partial/skipped+note) + RevisionResult(完整新稿+执行记录+source_* 原稿/评估/计划) + normalize_executions(剔除臆造/去重/补齐缺失/按计划顺序全覆盖); ReviserSkill(LLM 生成完整新稿非patch + protection_block 显式列出 preserve 与禁止修改项 + 权威覆盖 episode/title/source + 服务端重算 word_count/dialogue_ratio) + reviser.md v1.0.0 + RevisionAgent + golden revised_episode_football.json; 全量 507 passed/2 存量日志失败(477→507 恰为 F-02 新增 30) |
| F-03 | Continuity Validator | 1d | F-02,C-06 | DONE | AI Agent | 39 tests passed, Ruff/mypy clean(改动文件); domain/revision.py 增 ContinuityViolation(kind/source=rule·semantic) + ContinuityWarning + ContinuitySemanticCheck(独立 Skill 结构化输出) + ContinuityCheckInput(新稿/原稿/大纲/StoryBible/连续性状态/锁定事实) + ContinuityCheckResult(pass/fail + violations/warnings 分列 + checks_run, fail ⟺ 有违规); memory/continuity.py 增规则检查(锁定事实回归: 原稿有而新稿无才判缺失, 内容字符覆盖率≥0.5 容忍轻微措辞改变 / 大纲 key_events 必须体现 / required_characters 必须出场, 角色 ID→名称映射) + fact_preserved_in_text(子串命中或覆盖率) ; ContinuityCheckTool(纯规则) + ContinuitySemanticCheckSkill(LLM, source 权威置 semantic) + ContinuityCheckSkill(规则优先: 规则失败跳过 LLM 直接 fail; 规则通过才语义复核反转/状态/伏笔); continuity_semantic_check.md v1.0.0 + manifest/loader/openai 注册(映射 reviser); 全量 546 passed/2 存量日志失败(507→546 恰为 F-03 新增 39) |
| F-04 | Diff 与版本查询 | 0.75d | F-02,B-04 | DONE | AI Agent | 36 tests passed（unit 27 + integration 9）, Ruff/mypy clean(改动文件); domain/diff.py 纯模型(extra=forbid, 字段 ge/le) + tools/diff.py 确定性算法(两阶段场景对齐: 编号锚定 sim≥0.6 + Needleman-Wunsch sim≥0.35; 行级相似度规避 SequenceMatcher autojunk 病态; diff_lines 三计数 replace 块配对; 对称 change_ratio=(removed+added)chars/(from+to)chars; check_change_ratio 供 F-05 gate; 超大 diff truncated 保留统计清行明细) + DiffService(跨项目/类型/集数防护, content 无法解析回退 line diff) + GET /artifacts/diff(注册于 {artifact_id} 之前防路由吞噬); 集成测覆盖版本列表不可变/方向对称/跨项目拒绝/截断; 全量 582 passed/2 存量日志失败(546→582 恰为 F-04 新增 36) |
| F-05 | Revision Workflow/重评 | 1d | F-01..F-04,E-04 | DONE | AI Agent | 修订分支接通主工作流: select_revision(确定性选集+revision_round 原子自增) → revise(候选稿 draft 落库) → continuity_check(规则+语义,pass 提升 valid/fail 保 draft 转人工) → re_evaluate(权威原分取自 plan,下降>5 转人工); creation.py 低分改走修订分支; runs.py 事后处理改 elif 链(manual_review/needs_revision_decision/needs_user_input); revision.py 独立图+路由; ArtifactType 增 continuity_check; 新 7 个 workflow 测试+fixtures; **修复 B 期存量 input_hash 跨集碰撞**(compute_input_hash 仅哈希 source ids,各集剧本共享 outline/sb → ep2+ 幂等复用 ep1,真实管线只产出第 1 集; 现把 episode_number+artifact_type 纳入哈希载荷); 全量 589 passed/2 存量 health 日志失败(582→589), Ruff clean, mypy 回到 14 存量基线(0 新增) |
| F-06 | Revision API | 0.5d | F-05 | DONE | AI Agent | 修订闭环 HTTP 化: 新 app/api/v1/revisions.py(POST /projects/{id}/revisions 202+Run, script_artifact_id 任意合法版本/自动选集+user_instruction; 同步校验 SCRIPT_NOT_FOUND·CROSS_PROJECT_ACCESS·EVALUATION_NOT_FOUND; GET 列表/详情+result_chain 反查) + runs.py 接入 action=revise(schedule_worker 公开, 独立 build_revision_workflow 中途播种: 最新 valid 剧本+绑定评估, 用户指定剧本覆盖保证"任一合法版本"; elif 链加 revise→completed) + select_revision 尊重预置候选 + 独立图条件边(无候选直接 END) + RevisionPlanInput/Plan 增 user_instruction + revision_plan.md v1.1.0(用户补充要求段,不可违反锁定事实) + compute_input_hash 增 dedup_extra(仅非空加载荷,存量哈希逐字节不变) + find_referencing_artifacts 反查; 新 8 集成测试; 全量 597 passed/2 存量 health 日志失败(589→597), Ruff clean, mypy 0 新增 |
| G-01 | 短期/中期/项目记忆 | 0.75d | B-03,C-06 | DONE | AI Agent | 17 tests passed(unit 11 + integration 6), Ruff/mypy clean(改动文件); 新增 app/core/redis_client.py(惰性共享 Redis + TTL 帮助 + RedisUnavailableError 降级) + app/memory/short_term.py(ShortTermStore(ABC)→RedisShortTermStore: list+TTL 滑动窗口+miss 回退 DB / InMemoryShortTermStore: 单测与降级) + app/memory/summary.py(ConversationSummaryManager: 消息数达 threshold 整数倍→滚动摘要"超出短期窗口的旧消息", covered_from=上次 covered_to+1 保证连续不重叠, 服务端回填范围字段, dedup_extra=conv:covered_to 幂等, 失败只 log 不阻断) + domain/summary.py 增 ConversationSummary/Body/Input + conversation_summary.md 模板+manifest 条目(owner summarizer) + _SCHEMA_MAP+=conversation_summary + loader 注册 + MessageService.append DI 挂载(push+maybe_summarize 捕获异常) + conversations.py 惰性接线(测试 FakeLLM/生产 OpenAICompatible, Redis 不可用自动降级); config 增 short_term_ttl_seconds/conversation_summary_threshold; **验收 5 项全满足**: Redis 清空 DB 恢复 / 摘要覆盖范围 / 区间连续不重叠 / 项目不串记忆 / 摘要失败不阻断; 全量 pytest 与基线一致, Ruff clean, mypy 0 新增 |
| G-02 | Context Builder 完整化 | 0.75d | G-01,D-05 | DONE | AI Agent | 19 个 unit tests 通过(test_context_budget.py)+ Exit Gate 集成测试 test_summary_reaches_writer 通过; Ruff clean, mypy 0 新增(改动文件); 新增 app/domain/context.py(TaskKind/ContextSection/TaskContextPolicy 六任务策略表+get_policy 未知回退 writer+ContextTooLargeError(413/CONTEXT_TOO_LARGE)+TokenEstimator(ABC)→CharacterRatioEstimator(1.5)) + context_builder.py 重写 build_for(task,*)分任务策略组装(current_target 输出缓冲永不静默截断, 超限抛 ContextTooLargeError, 非目标段按权重归一化裁剪+记录 truncation_reasons/section_estimates, rag_chunk_ids 从调用方回填 manifest, build() 兼容旧入口) + write_episode 节点最小接入(previous_summary_continuity=latest_project_summary_text 会话摘要+ContinuityManager 连续性, current_target=本集大纲, 注入 assembled_context) + episode_writer_v2.md 模板 v1.1.0(manifest.yaml 追加, v1.0.0 保持不变保住 hash 快照测试) + retrieve.py 回填 stage_chunk_ids + EpisodeWriterInput.assembled_context 字段; **验收 5 项全满足**(分任务组成不同/输出缓冲保留/当前稿件不静默截断/旧会话优先摘要进上下文/边界预算); 全量 pytest 734 passed/2 存量日志失败与基线一致, Ruff 45 存量 migration E501 仅预置, mypy 91 较基线 93 还少 2(移除无用 ignore) |
| G-03 | 上传与 TXT/DOCX Parser | 1d | B-03 | DONE | AI Agent | 20 unit + 11 integration tests 全绿(test_file_parser.py: 编码探测 UTF-8→GBK 回退/表格提取/宏·损坏·伪装·路径穿越拒绝; test_uploads.py: 中文不乱码回读/GBK 告警/DOCX 落盘一致/空文件/损坏 422/伪装 422/超限 413/项目 404/跨项目隔离/列表倒序); Ruff clean(仅 45 存量 migration E501), mypy 0 新增(全 app 12 较基线 14 还少 2); 全量 pytest 765 passed/2 存量日志失败与基线一致; 新增 FileStore 协议+LocalFileStore(UUID 键/原子 os.replace/防穿越), FileParserTool(大小·扩展名·zip 魔数联合校验+拒绝 docm/vbaProject+BadZipFile 映射 422), Upload 模型+0003 migration 加 original_name/parse_status/char_count/warnings, UploadRepository, uploads.py POST+GET(分块读取提前止损, 原始名仅存 original_name 展示, 内容不写日志), router.py 挂载; 验收 5 项全满足 |
| G-04 | Import 分类与路由 | 0.75d | G-03,C-02 | DONE | AI Agent | 20 unit(10 router contract + 9 workflow 集成 + versions 幂等新增)全绿, Ruff clean, mypy 0 新增(全 app 11 较会话基线 12 还少 1); 新增 domain/import_file.py(ImportClassificationInput/ImportClassification) + skills/import_classifier.py(规则先行: 特征提取 字符/行/场景/分集/对白/参考关键词, 命中即返回不调 LLM; 模糊文本回退 LLM prompt import_classifier) + workflows/router.py(route_import 纯函数: outline/idea→create, full_script→evaluate, reference→hold 不自动入库, unknown→needs_user_input) + workflows/import_file.py(单节点状态图: FileStore+Parser 解析→分类→持久化 import_classification Artifact dedup_extra=upload:{id} 幂等→确定性路由) + import_classifier.md 模板+manifest 条目 + loader/artifact_service 注册 + runs.py action=import 分支(upload_id 进 configurable, 事后 elif 链完成/needs_user_input 拦截) + golden fixtures(import_classification_outline/full_script/unknown); **修复 G-01 遗留**: compute_input_hash 对"无源仅 dedup_extra"返回 None 使会话摘要/导入分类的幂等去重从未生效, 现 dedup_extra 单独出现也参与哈希(有源哈希逐字节不变, 无源无 dedup_extra 仍 None), 存量测试全绿; 验收 5 项全满足 |
| G-05 | Markdown/DOCX Exporter | 1d | B-04,F-04 | DONE | AI Agent | 14 unit + 8 docx + 10 service integration tests 全绿(test_markdown.py: 无内部 ID/Prompt/Token、3 集按集号升序、标题层级稳定 H1/H2/H3、缺数据占位、文件名清洗; test_docx.py: python-docx 重开中文/表格/页眉/页码域/分页; test_export_service.py: 组装 latest valid→序列化→FileStore 原子落盘→ExportFile Artifact, 幂等复用/显式版本选择/跨项目拒绝/失败不生成 valid/源链接完整); Ruff clean, mypy 0 新增(全 app 11 与基线一致); 新增 domain/export.py(ExportSelection[kinds/format/artifact_ids 显式版本]+ExportFileContent[storage_key/format/filename/size_bytes/sha256/source_artifact_ids/warnings], extra=forbid) + tools/exporters/markdown.py(移植前端 export.ts 序列化逻辑: story_bible/outline/script/evaluation/revision + build_export_markdown 按集号排序 + 文件名清洗+时间戳) + tools/exporters/docx.py(标题/页眉/PAGE 页码域/一级标题分页/中文字体 w:eastAsia fallback/GFM 管道表格) + application/export_service.py(组装各 kind latest valid→序列化→LocalFileStore.save 原子落盘→create_validated_artifact(EXPORT_FILE, source_artifact_ids, dedup_extra=selection 规范化 JSON); 幂等命中清理孤儿文件; 任一步失败抛错不生成 valid) + _SCHEMA_MAP+=export_file(非法 content→invalid) + config 增 export_file_root; 验收 5 项全满足 |
| G-06 | Export API 与导入导出集成 | 0.75d | G-04,G-05 | DONE | AI Agent | 36 focused tests 全绿(test_exports.py 10 + test_upload_to_export.py 2 条端到端 + test_import_workflow.py + test_script_text.py 15), Ruff clean, mypy 0 新增(全 app 11 与基线一致); 新增 api/v1/exports.py(POST /projects/{id}/exports: ExportSelection 校验 kinds/format/artifact_ids→create_run(action=export)+schedule_worker 异步 202+Run; GET /exports/{artifact_id}/download: 项目归属校验(跨项目 403 CROSS_PROJECT_ACCESS)→FileStore.open→安全 Content-Disposition(中文 filename* RFC 5987+ASCII 兜底+禁路径分隔符/控制字符), Artifact 缺失/非 export_file/存储文件丢失→404 EXPORT_FILE_MISSING) + errors.py 增 ExportFileMissingError + router 挂载; runs.py action=export 确定性分支(无 LLM 无 LangGraph: ExportService.export_project 组装→序列化→落盘→run.completed SSE+Artifact ID) + _resolve_upload_text(upload_id 注入 create_script 创作输入, 支撑"Outline→创作") + **修复存量缺口: standalone action=evaluate 未在 schedule_worker 名单, 直接创建评估 Run 永不执行, 现补 evaluate**; import_file.py full_script 分类时用确定性 full_script_to_script_draft(纯正则+规则无 LLM: 场景/地点时间/对白/动作/首末钩子/plain_text/字数/对白比, <2 场返 None 仅告警) 持久化 script_draft Artifact(dedup_extra=upload:{id}), 支撑"完整剧本→评估"; 验收 5 项全满足(下载文件名安全/跨项目 403/文件丢失 EXPORT_FILE_MISSING/两条导入路径端到端/Export source links 完整 4 条) |
| H-01 | 前端基座与 API 类型 | 0.5d | B-01 | DONE | AI Agent | 12 tests passed, ESLint/tsc clean; Tailwind CSS + TanStack Query + API Client + 布局 + 3 通用组件 |
| H-02 | 项目列表与创建 | 0.5d | H-01,B-03 | DONE | AI Agent | 22 tests passed, ESLint/tsc clean; ProjectCard+StatusBadge+列表+创建表单, target_episode_count 类型对齐后端 |
| H-03 | 对话、上传与 SSE | 1d | H-02,B-05,G-03 | DONE | AI Agent | 30 tests passed, ESLint/tsc clean; useRunEvents SSE hook + ChatInput + RunProgress + 项目工作台 |
| H-04 | StoryBible/大纲视图 | 0.5d | H-03,C-08 | DONE | AI Agent | 71 tests passed, ESLint/tsc clean; CharacterCard+StoryBibleView+EpisodeCard+OutlineListView, locked facts视觉识别, 版本切换, 空字段占位 |
| H-05 | 剧本编辑与评估视图 | 0.75d | H-04,E Gate | DONE | AI Agent | 122 tests passed, ESLint/tsc clean; EpisodeNav+ScriptView+ScoreBar+IssueCard+EvaluationPanel, 三栏布局, issue→scene定位, risk_flags明显展示, 评估四态覆盖, 阶段E Mock |
| — | 🔧 SSE/日志/集数修复 | 0.5d | H-05 | DONE | AI Agent | SSE fetch→EventSource, event_type字段对齐, autocommit事件提交, DB轮询回退, 日志北京时区+彩色console, outline_count/script_count从config读取, 角色校验降级为日志, ChatInput集数选择器 |
| — | 📐 文档工作流与 CLAUDE.md 精简 | 0.25d | - | DONE | AI Agent | CLAUDE.md 精简+状态修正; 「开发收尾清单」固化; DEV_PLAN 模板补"为什么/学到什么"; TROUBLESHOOTING 模板升级并回填 7 条学习经验 |
| H-06 | 修订/版本/Diff | 0.75d | H-05,F Gate | DONE | AI Agent | 145 tests passed(122→145), ESLint/tsc clean, pnpm build 通过; types/api.ts 增 continuity_check + revision/continuity/diff 全套类型与中文标签; api-client 增 revisionsApi(create/list/get)+artifactsApi.diff; features/diff/DiffView.tsx(scene/line 双模式、行级红绿+删除线、截断分级提示、SceneCard 受控折叠+body 惰性渲染+>20 场景默认折叠防卡死、line 回退横幅、空 diff 占位); features/revisions/ 四叶子(RevisionPlanView 计划/锁定事实/用户补充要求、ContinuityCheckView 违规明细+source 徽章+warnings、ScoreComparison scoreDelta 纯函数+下降红绝不包装提升、RevisionPlanList 选中高亮); RevisionDetail.tsx 容器(result_chain 反查→needs_manual_review banner: 连续性失败或评分降>5、评分对比、Diff、原稿/修订稿全文切换); versions/page.tsx 两区(修订记录: 发起修订 POST→轮询 Run→刷新; 版本对比: 集数→原稿/修订稿版本→任意两版本 diff, invalid 标红, 只读不覆盖旧版本); 工作台两处完成态加「🔀 修订与版本」入口; **修复 vitest React 双实例**(@testing-library/react 自根加载 root react-dom→root react, 测试 import 走 frontend react → Invalid hook call; vitest.config resolve.alias 数组把 react 系统一到根副本, 更具体前缀在前) + 修复 jsdom 不触发 <details> toggle→SceneCard 改按钮显式折叠 |
| H-07 | 导出与 E2E | 1d | H-06,G-06 | DONE | AI Agent | 前端 162 tests passed(145→162), ESLint/tsc clean, pnpm build 通过; 客户端本地导出中心 features/exports/(ExportSection 内容多选+格式单选+生成下载、ExportHistory localStorage 历史+重新下载+清空)+lib/export.ts 纯序列化(downloadBlob/buildDocx docx 动态导入, 不进 SSR)+exports 页; 工作台 completed/needs_review 双终态加「📦 导出中心」入口; **低分场景 E2E 支撑**: golden evaluation_report_lowscore.json(overall≈58.7<75→need_revision)+FAKE_LLM_SCENARIO=revision 开关(runs.py 注册低分 fixture)+test_fake_scenario.py(3 tests); **E2E 基建**: docker-compose.e2e.yml(postgres:5433/redis:6380 隔离, healthcheck)+e2e/playwright.config.ts(截图/trace 仅失败保留)+fixtures(data/helpers)+scripts/e2e.sh(建库→迁移→FakeLLM 低分后端:8010→前端:3100→playwright)+Makefile e2e/e2e-setup/e2e-down; dramaagent.spec.ts 全链路 8 段(空项目→Idea→SSE→刷新重连→SB→10 集大纲→3 集剧本→低分评估→恰 1 条修订→连续性/评分对比/Diff→MD+DOCX 下载非空→历史 2 条); needs_review 终态前端支持(useRunEvents/RunProgress 横幅/工作台链接, 低分场景创作 Run 停需复核仍可见入口) | make e2e REPEAT=5 → 5 passed; E2E 验收 5 项全部满足(见任务卡勾选); 后端全量 pytest 仅 2 存量日志失败(与 HEAD 一致) |
| I-01 | 恢复与成本保护 | 0.75d | H Gate | DONE | AI Agent | 恢复矩阵 6 测试类(27)+API 9+retry 单元全绿，全量 881 passed/2 存量日志失败(与 HEAD 一致)，Ruff clean，mypy 全 app 95 较基线 103 还少 8(0 新增)；新增 llm/retry.py(RetryPolicy 指数退避 base*factor^(attempt-1)+max_delay+Retry-After 解析秒/HTTP-date, is_retryable: 429/timeout/provider_error 可重试, execute_with_retry 驱动, LLM_ERROR_RUN_CODES 映射)+llm/budget.py(RunBudgetRegistry+contextvar: 软上限 run.warning 事件/硬上限抛 RUN_BUDGET_EXCEEDED)+checkpoint.py 重写(协作式取消 RunCancelledError 继承 BaseException 不被 except Exception 吞、_cancel_registry 跨 Task 共享、raise_if_cancelled 各节点入口、classify_error_code AppError.code 优先/文本兜底、node_failure、save/load_checkpoint)+FakeLLM retry_policy opt-in+inject_fault 新类型(timeout/rate_limited/invalid_schema/provider_error)+_attempt_count 含重试; run_service 状态机 running→cancelled, cancel_run 协作式(queued 立即 cancelled/running 置标记); runs.py POST /runs/{id}/retry(守卫 409 RUN_NOT_RETRYABLE/RUN_ALREADY_ACTIVE, failed/needs_review→queued 清 error 字段→schedule_worker)+worker enter_run/exit_run 预算登记+retry 以 state_summary 恢复(剥离 status/error 字段, completed_nodes 早退+write_episodes existing_scripts 跳过→不重调 LLM/不重写集/不重推 revision_round)+save_checkpoint+RunCancelledError→cancelled+run.cancelled+_persist_run_error; WorkflowRun error_code/error_detail 列+0004 migration+RunResponse 暴露; **修复真 bug: 节点失败级联**(story_bible 失败后 outline 静态边仍执行 uuid.UUID(None) 崩溃, error_code 被覆盖→12 节点+import_file 加 status==failed 短路守卫, _should_evaluate 干净终止); 验收 5 项全满足(见任务卡勾选) |
| I-02 | 可观测性 | 0.5d | I-01 | DONE | AI Agent | 验收 4 项全勾选：/runs/{id}/diagnostics 聚合事件表给出节点时间线(耗时/终态)、run.llm_stats 给出调用数与 prompt/completion token、日志脱敏 RedactFilter 自动测试通过、metrics 输出无 project_id/run_id 高基数标签(API 级断言)；新增 observability/metrics.py(进程内 Counter/Gauge/Histogram registry+Prometheus 文本渲染, 9 个命名指标按 §10.4, 标签低基数)+tracing.py(contextvar span 链 request→run→node, 跨 Task 隔离)+diagnostics.py(事件表聚合: node.started→completed/failed 耗时, run.llm_stats→llm_calls/llm_tokens, run.failed→errors, error_node 回退最近 node.failed); GET /metrics 端点(metrics_enabled 开关, 关→404, 埋点仍累积); 埋点接入 9 处(run_service/creation 节点 _timed_node/llm client/retry/artifact store/export/SSE stream/rag retriever); worker finally 发 run.llm_stats(在 exit_run 前读 budget registry); core/logging.py 加 RedactFilter+mask_secret(sk-*/api_key/Bearer/access_token 保留前缀掩蔽值+超长截断)+修复 2 个存量 TestStructuredLogging 失败; 测试 unit/observability 26(metrics/tracing/log_redaction)+API 7(metrics on/off+无高基数标签, diagnostics 时间线/llm_stats/errors/404)；全量 916 passed/0 failed, Ruff clean, mypy app/ 11 错误与 HEAD 完全一致(0 新增)；OPERATIONS.md(metrics/diagnostics 使用说明) |
| I-03 | 安全回归 | 0.5d | G-03,H-07 | DONE | AI Agent | 验收 5 项全勾选（路径穿越/剧本不执行脚本/上传下载归属/Prompt 注入隔离/安全文档局限）；新增 core/security.py 集中安全工具(escape_html 五字符&先转/sanitize_filename_part/assert_safe_key/mask_secret/truncate_content)，logging/storage/local/markdown(深转义)/exporters __init__ 复用去重；Prompt 注入隔离 loader 层内容边界(manifest user_content_vars 声明→render 包裹 【用户内容开始/结束】+固定指令句，不改 10 个模板)，全 manifest 契约测试兜底声明变量与模板同步；前端 export.ts 镜像 escapeHtml+escapeDeep(buildExportMarkdown 深转义+项目名转义)；测试 backend/tests/security 40(注入 6+转义 7+日志扫描 2+CORS 6+路径安全 17)+frontend escaping 7；全量 939 passed/0 failed(916→939)，Ruff clean，mypy app/ 11 与 HEAD 完全一致(0 新增)；SECURITY.md(威胁模型/输入卫生/输出转义/访问控制/注入隔离/日志脱敏/数据删除策略/MVP 局限) |
| I-04 | MCP/Skill 扩展契约 | 0.5d | B-07 | DONE | AI Agent | 验收 5 项全勾选（无 MCP 配置主流程可用/Fake MCP Tool 注册·调用·超时/外部错误不泄露内部连接信息/重名策略明确/文档提供新增 Skill 最小示例）；新增 integrations/mcp/{protocol,adapter}.py(MCPToolSpec+MCPAdapterConfig(enabled/base_url/timeout/prefix), MCPToolAdapter(Tool) 把外部 HTTP JSON-RPC 工具映射为内部 Tool, 注册名=prefix+spec.name, 429/5xx 复用 I-01 RetryPolicy+parse_retry_after 退避重试, 超时→EXTERNAL_TOOL_TIMEOUT 504/连接·HTTP≥400·JSON-RPC error·响应不可解析→泛化 EXTERNAL_TOOL_ERROR 502 均 from None 不泄漏, transport 可注入 MockTransport 测试)+register_mcp_tools(enabled=False 返回空列表不触碰注册表)+errors.py 两错误类+config.py mcp_enabled/base_url/timeout; ToolRegistry/SkillRegistry 增 get_metadata/list_metadata 元数据查询入口; 代表性工具 word_count/dialogue_ratio 补 input_schema/output_schema 样例(其余留空容忍); 测试 tests/contract/test_mcp_adapter.py 18(注册名前缀/schema 透传/成功调用 JSON-RPC 载荷/超时 504/5xx 重试耗尽 502/429+Retry-After 重试成功/400 不重试/JSON-RPC error/非 JSON/错误不泄漏 base_url 与连接细节/批量注册/enabled=False/重名 409/list_metadata/get_metadata 404/Skill 元数据查询); 全量 974 passed/0 failed(956→974), Ruff clean, mypy app/ 11 与 HEAD 完全一致(0 新增); EXTENSIONS.md(新增 Skill 最小示例+Tool schema+3.3 MCP 注册与错误表); .env.example 补 MCP_ENABLED/BASE_URL/TIMEOUT |
| I-05 | 性能/覆盖率/回归 | 0.5d | I-01..I-04 | DONE | AI Agent | 验收 5 项全勾选（§1.6 指标实测达标/无未解释 flaky/E2E 5/5/连接释放/报告区分含不含 LLM 耗时）；新增 tests/performance/{test_api_latency,test_concurrent_sse,test_1000_artifacts}.py 6 测试(普通 API p95 实测 28-31ms/100 并发 SSE 首块 701.8ms 且 gauge 回落基线/1000 Artifact 分页 45.3ms, 阈值 300/1000/300ms 全绿; 显式 NullPool 修 SSE 泄漏连接复检 InterfaceError, uvicorn 进程内随机端口, 合成 run_id 隔离 worker 污染); 覆盖率双门禁 pyproject fail_under=75(总体实测 88%)+coverage report --include=domain/workflows/artifacts --fail-under=85(核心实测 92%); pytest addopts -m not performance+marker 注册, CI -m "not smoke and not performance"+核心门禁步骤, Makefile perf/cov/ci; 修复 E2E flaky(startCreation「创建项目」substring 定位器在空态过渡帧 strict-mode 冲突 → 改点头部「+ 创建项目」唯一匹配); 修复 docker-compose.e2e.yml 与主 compose 共享 project name=drama_agent 导致 e2e 清理 down -v 连带移除开发库容器 → 加 name: drama-e2e 隔离; docs/TEST_REPORT.md(计数 974 passed/0 failed/6 deselected/覆盖率 88%+92%/性能实测表/含不含 LLM 耗时区分/E2E 5×); Ruff clean, mypy 与 HEAD 一致(11, 0 新增) |
| I-06 | 文档与 RC 发布 | 0.25d | I-05 | DONE | AI Agent | 验收全满足：新增 docs/DEMO.md（FakeLLM 离线固定步骤 + 真实 LLM smoke 说明）+ docs/KNOWN_LIMITATIONS.md（19 项 MVP 接受/backlog）+ CHANGELOG.md（Keep a Changelog，0.1.0-rc1 条目）；README 状态表修正为 A~I 全 DONE + 命令表补 cov/perf/e2e + 能力总览；API_CONTRACT 补 retry/diagnostics/metrics 端点 + 全局错误码全集（I-01/02/04）；.env.example 补 EXPORT_FILE_ROOT/SHORT_TERM_TTL_SECONDS/CONVERSATION_SUMMARY_THRESHOLD；backend+frontend 版本 0.1.0-rc1；§13.3 H/F/I → PASS；DEV_LOG I-06 条目；git tag v0.1.0-rc1 |

### 13.3 阶段验收记录

| Gate | 计划日期 | 实际日期 | 结果 | 验收人 | 证据 | 遗留问题 |
| --- | --- | --- | --- | --- | --- | --- |
| A | - | 2026-07-23 | PASS | AI Agent | 全部命令成功（Docker 环境限制除外）；无真实 LLM 调用；69 tests passed, 97.44% coverage | Docker 未安装在 Windows，WSL 中已就绪 |
| B | 2026-07-23 | 2026-07-23 | PASS | AI Agent | 129 tests; 6 场景 Exit Gate 测试就绪; PostgreSQL 不可用时代码级验证通过 | Docker PostgreSQL 不可用，Exit Gate 集成测试待 DB 就绪后执行 |
| C | - | 2026-07-25 | PASS | AI Agent | pytest 391/391 passed, Ruff clean; 8 Exit Gate C tests (全链路 FakeLLM 驱动), 5/5 验收条件通过 | 无 |
| D | - | - | NOT RUN | - | - | - |
| E | - | 2026-08-08 | PASS | AI Agent | 前 3 集生成合法报告; overall/need_revision 由服务端规则得出; 低分维度自动补 issue; Evaluation Artifact 绑定剧本版本; FakeLLM 全链路(449 tests)+ 真实模型手工 smoke 脚本就绪; 全量 2 存量失败(日志) | 阶段 H-06 需等 F Gate |
| F | - | 2026-08-16 | PASS | AI Agent | 修订闭环全部 DONE（F-01~F-06：确定性选集 / 版本化修订 / 连续性校验 / Diff / 重评 / Revision API）；F-05 修复存量 input_hash 跨集碰撞 | 无 |
| G | - | 2026-08-16 | PASS | AI Agent | 全量 845 passed/2 存量日志失败(与 HEAD 基线一致), Ruff clean, mypy 0 新增(11 存量); Exit Gate 6 项全满足: 多轮会话摘要进 writer 上下文(test_summary_reaches_writer) / Redis 清空 DB 恢复(test_summary) / TXT·DOCX 上传解析分类(test_uploads + test_import_workflow) / Outline→创作→导出(test_upload_to_export 路径 1) / 完整剧本→评估→导出(路径 2) / MD+DOCX 导出可打开(test_markdown + test_docx + test_exports) | 前端上传/导出 UI 入口为占位(后端为主范围外, 后续阶段实现) |
| H | - | 2026-08-16 | PASS | AI Agent | 工作台全链路 + 导出中心 + Playwright E2E；`make e2e REPEAT=5` 5 passed（FakeLLM + 低分场景，隔离 postgres/redis） | 无 |
| I / Release | - | 2026-08-16 | PASS | AI Agent | Exit Gate 修正后全绿：`make ci` lint+typecheck+cov 全过——typecheck 首跑暴露 mypy 109 错误（此前只验 `mypy app/` 0 新增、未跑 `tests/`，属过报），本轮按基线 4ba03b8 拆分存量 85 + 新增 24 全部清零为 **0 errors / 281 files**（含 `disallow_incomplete_defs` 双开关修复、21 处 `RunnableConfig` 注解、`cast` 收敛等，详见 DEV_LOG 2026-08-16）；覆盖率总体 87.55%≥75%、核心 domain/workflows/artifacts 92%≥85%；`make e2e REPEAT=5` 5 passed、安全回归全绿、`make perf` 6 passed（p95 达标）、全量 974 passed/0 failed 零存量失败、FakeLLM 离线 Demo 可复现（docs/DEMO.md）、v0.1.0-rc1 发布候选（tag）、release 文档完整 | 真实 LLM 一次人工 smoke 尚未执行（需用户批准与 Key）；RAG（Phase D）为 backlog |

### 13.4 开发日志模板

~~~markdown
#### YYYY-MM-DD

- 当前任务：C-04
- 状态：DOING
- 今日完成（做了什么）：
  - ...
- 为什么这么做：
  - ...
- 测试：
  - 命令：
  - 结果：
- 学习收获：
  - ...（可复用的经验 / 教训）
- 决策：
  - ...
- Blocker：
  - 无 / ...
- 下一步：
  - ...
~~~

---

## 14. 8 周建议排期

此排期按“单名开发者 + AI Coding、每天至少一次可验收提交”设计。估时是计划基线，不应为了赶日期跳过 Gate。

| 周 | 任务 | 周末可验收增量 |
| --- | --- | --- |
| 第 1 周 | A-01..A-04，B-01..B-03 | 仓库、基础设施、Schema、项目/会话 API |
| 第 2 周 | B-04..B-07，C-01..C-02 | Run/SSE/Artifact/FakeLLM 最小纵切，需求归一化 |
| 第 3 周 | C-03..C-08 | Idea 到 StoryBible、10 集大纲、3 集正文 |
| 第 4 周 | D-01..D-05，E-01 | RAG 接入创作链路，Rubric 锁定 |
| 第 5 周 | E-02..E-05，F-01..F-02 | 3 集评估，最低分集选出并生成修订稿 |
| 第 6 周 | F-03..F-06，G-01..G-03 | 修订闭环、版本 Diff、记忆、文件解析 |
| 第 7 周 | G-04..G-06，H-01..H-05 | 导入/导出，前端可操作至评估报告 |
| 第 8 周 | H-06..H-07，I-01..I-06 | 完整 Web Demo、E2E、加固、RC 发布 |

### 14.1 每周固定节奏

- 周一：确认本周 Gate、依赖和风险；
- 每日开始：选择一个任务 ID，状态改为 DOING；
- 每日结束：运行该任务测试，更新 Evidence；
- 周四：阶段内集成，停止增加非必要范围；
- 周五：执行 Gate、记录失败项和下周 blocker；
- 每个阶段预留 10% 到 15% 时间处理 LLM Schema 和集成问题。

### 14.2 进度计算

不建议只按任务数量计算。使用加权进度：

~~~text
任务进度 =
  TODO 0%
  DOING 30%
  BLOCKED 30%
  REVIEW 80%
  DONE 100%

阶段进度 = sum(任务预计工时 * 任务进度) / sum(任务预计工时)
项目进度 = sum(阶段预计工时 * 阶段进度) / 42.25
~~~

Gate 未通过时，即使阶段任务都标 DONE，阶段状态仍为 REVIEW，不算正式完成。

---

## 15. 需求到实现的追踪矩阵

| 原始需求 | 主要任务 | 核心测试 | 最终证据 |
| --- | --- | --- | --- |
| Idea/Outline 输入 | C-02、C-08 | creation API/workflow | Requirement Artifact |
| TXT/DOCX 输入 | G-03、G-04 | upload/import workflow | Upload + Classification Artifact |
| StoryBible | C-03 | StoryBible golden/schema | StoryBible Artifact |
| 10 集大纲 | C-04 | sequence validator | EpisodeOutlineSet Artifact |
| 前 3 集正文 | C-05、C-07 | creation workflow | 3 个 ScriptDraft |
| 知识库检索 | D-01..D-05 | hit@5、trace | RetrievalTrace/context manifest |
| 逐集评估 | E-01..E-04 | evaluation workflow | 3 个 EvaluationReport |
| 选择最低分集 | F-01 | selector unit test | selected_episode_number |
| 自动修订 1 集 | F-02、F-05 | revision workflow | 新版本 ScriptDraft |
| 连续性保护 | C-06、F-03 | locked fact fixture | ContinuityCheckResult |
| 重新评分 | F-05 | revision workflow | 新 EvaluationReport |
| 新旧 Diff | F-04、H-06 | diff unit/UI test | Diff Response |
| Artifact 版本 | B-04、F-04 | 并发版本测试 | Version history |
| 对话与项目记忆 | G-01、G-02 | Redis loss recovery | Summary/Context manifest |
| SSE 流式进度 | B-05、H-03 | reconnect test | Event timeline |
| Markdown/DOCX 导出 | G-05、G-06 | export test | ExportFile Artifact |
| 多 Agent/Skill | B-07、C/E/F Skills | registry contract | 注册元数据与调用记录 |
| MCP 预留 | I-04 | FakeMCP contract | Adapter 示例 |
| 失败恢复 | B-05、I-01 | recovery matrix | 重试 Run 记录 |

追踪规则：任何新增产品需求必须先补充本表，再创建任务；没有测试或最终证据的需求不能进入当前 Sprint。

---

## 16. AI Coding 提示模板

### 16.1 实现单个任务

~~~text
你正在开发 DramaAgent。

只实现任务：[TASK_ID] [TASK_NAME]。

执行前：
1. 阅读 DEV_PLAN.md 的第 0、2、4、10 节。
2. 阅读当前阶段目标、当前任务卡和全部直接依赖。
3. 检查仓库现状，不假设尚未实现的模块存在。

实现约束：
- 严格控制在任务卡范围内，不实现后续任务。
- 遵守模块边界；API 不直接访问 ORM 或 LLM。
- 所有 LLM 输出用 Pydantic v2 Schema 校验。
- 测试默认 FakeLLM/FakeEmbedder，不访问外网。
- Artifact 不可变，禁止覆盖旧版本。
- 数据库修改必须增加 Alembic migration。
- 新配置必须进入 .env.example，不能提交密钥。
- 函数和类写清晰中文注释；注释解释意图，不复述代码。

完成后：
1. 运行任务卡要求的测试。
2. 再运行相关 lint/typecheck。
3. 列出修改文件、验收项、验证命令和结果。
4. 若无法完成，停止扩展范围并说明 blocker。
5. 不要仅返回代码片段，要直接修改仓库文件。
6. 完整后更新开发计划和开发日志
~~~

### 16.2 独立代码审查

~~~text
请只审查任务 [TASK_ID] 的实现，不修改代码。

依据：
- 当前任务卡；
- 全局技术约束；
- 领域/API/Event Schema；
- Definition of Done。

重点检查：
1. 是否越过任务边界；
2. Artifact 是否可能被覆盖；
3. LangGraph State 是否存入大文本；
4. API 是否绕过 application/repository；
5. LLM 输出是否未经 Schema 写库；
6. 重试是否可能重复计费或重复创建版本；
7. 日志是否泄露密钥、全文或 Prompt；
8. 测试是否只测 mock 调用次数而没测业务不变量；
9. 失败路径、并发和幂等是否覆盖。

输出按严重级别排列：
- Blocker
- Major
- Minor
- Test gap

每条问题给出文件、代码位置、实际风险和最小修复建议。
若无问题，明确说明仍未覆盖的风险。
~~~

### 16.3 修复任务

~~~text
修复任务 [TASK_ID] 验收中发现的问题：
[ISSUE_LIST]

要求：
- 先复现并新增失败测试；
- 只做最小修复；
- 不改变已经发布的 Schema，除非问题本身来自 Schema；
- 修复后运行原任务测试、回归测试和 lint/typecheck；
- 返回“复现证据 -> 根因 -> 修改 -> 验证”。
~~~

### 16.4 阶段验收

~~~text
对阶段 [PHASE] 执行独立验收。

1. 不新增功能。
2. 按 Exit Gate 从干净环境运行全部命令。
3. 检查该阶段每个任务的 Evidence。
4. 抽查至少一个成功路径、一个校验失败路径、一个外部依赖失败路径。
5. 输出 PASS / FAIL。
6. FAIL 时列出阻止进入下一阶段的最小问题集，不顺手修复。
~~~

---

## 17. 验收场景清单

### 17.1 正常路径 AC-01

前置：空项目、FakeLLM、已摄取测试知识库。

步骤：

1. 创建“足球少年逆袭”项目；
2. 输入固定 Idea；
3. 创建 create_script Run；
4. 订阅 SSE；
5. 等待 Run 结束；
6. 查询全部 Artifact；
7. 查看修订 Diff；
8. 导出 Markdown/DOCX。

预期：

- 1 Requirement、1 StoryBible、1 OutlineSet；
- 3 个 v1 Script；
- 3 个原稿 Evaluation；
- 1 RevisionPlan；
- 最低分集有 v2 Script；
- 1 个 v2 Evaluation；
- 至少 1 个 ContinuityState；
- 2 个 ExportFile；
- Run 状态 completed；
- 事件 sequence 无空洞或重复。

### 17.2 无需修订 AC-02

FakeLLM 令 3 集均不触发 need_revision：

- selector 返回 None；
- 不创建 RevisionPlan；
- revision_round 保持 0；
- Run 正常 completed；
- 导出使用 v1。

### 17.3 连续性失败 AC-03

修订 fixture 故意改变 locked fact：

- ContinuityCheckResult=fail；
- 候选修订稿不成为 latest valid；
- Run=needs_review；
- 原稿仍为 latest valid；
- 不执行 re-evaluate；
- 前端显示 violation。

### 17.4 模型结构失败 AC-04

FakeLLM 连续输出两次非法 StoryBible：

- 发生 2 次重试；
- node.failed、run.failed；
- 不创建 valid StoryBible；
- 错误码为 LLM_OUTPUT_INVALID；
- retry 后前置 Requirement 不重复生成。

### 17.5 SSE 恢复 AC-05

- 接收前 5 个事件后断开；
- 期间 Run 继续；
- 带 Last-Event-ID 重连；
- 补收 sequence 6..N；
- 无重复、无遗漏；
- Redis 清空后仍能补收。

### 17.6 上传安全 AC-06

分别上传：合法 UTF-8 TXT、合法中文 DOCX、超大文件、伪装 DOCX、损坏压缩包、路径穿越文件名。

预期：前两者成功，其余返回明确 4xx；磁盘路径均由服务端生成；日志不包含全文。

### 17.7 并发与幂等 AC-07

- 同一 Idempotency-Key 并发提交两次；
- 只创建一个 Run；
- 并发写同类 Artifact 时版本不重复；
- 重试 completed Run 不产生重复资产，除非 force=true。

---

## 18. 风险登记与触发条件

| 风险 | 触发信号 | 预防 | 触发后的处理 |
| --- | --- | --- | --- |
| LLM 结构不稳定 | Schema 重试率大于 10% | 结构化输出、Prompt 版本、FakeLLM | 收缩 Schema/Prompt；不写非法 Artifact |
| 上下文过长 | ContextTooLarge 或成本持续升高 | 摘要、Artifact ID、预算裁剪 | 分场景处理；记录被裁剪项 |
| Agent 控制流失控 | 同一任务重复调用/循环 | LangGraph 确定分支、round 上限 | 终止 Run，检查 checkpoint 与幂等键 |
| 修订破坏设定 | locked fact violation | RevisionPlan preserve、连续性门禁 | needs_review，保留原稿为 latest valid |
| 评分被“刷高” | 分数涨但关键事件丢失 | 评分 + 结构 + 连续性联合门禁 | 标记人工复核，不只看总分 |
| RAG 污染 | 不相关类别频繁命中 | metadata filter、来源治理 | 停用相关文档/回滚 corpus version |
| 数据重复 | 同一节点产生多个相同版本 | input_hash、幂等与事务版本 | 诊断并发写；不手工删除历史 |
| SSE 丢事件 | sequence 缺口 | DB 事件事实源、Last-Event-ID | 从 DB 补发；Redis 仅通知 |
| 成本超预算 | 单 Run 调用超过 18 | 调用/token 上限、缓存 | 停止 Run，显示 budget exceeded |
| 文件安全 | 解析异常/路径穿越 | 签名、大小、服务端命名 | 隔离文件、返回安全错误 |
| 真实模型评估漂移 | 同样输入分数方差扩大 | 固定 Prompt/Rubric/模型版本 | 运行稳定性诊断，不用结果缓存掩盖 |
| 前后端契约漂移 | 运行时字段缺失 | OpenAPI 生成类型、contract test | 阻断 CI，先修契约 |

---

## 19. 变更控制

以下变更属于“范围变更”，不能由 AI Coding Agent自行决定：

- MVP 大纲从 10 集改为其他数量；
- 正文从 3 集扩展；
- 自动修订超过 1 轮；
- 用 CrewAI/AutoGen 替换 LangGraph；
- 用独立 Vector DB 替换 pgvector；
- 将 Redis 变为事实源；
- Artifact 改为可变记录；
- 新增登录、支付、多人协作；
- 自动抓取互联网内容进入知识库；
- 将同步长请求替代 Run + SSE；
- 修改 9 维 Rubric 权重；
- 在未加版本号的情况下修改 Prompt。

变更流程：

1. 创建 Change Request；
2. 写清动机、影响模块、数据迁移、测试和回滚；
3. 更新本文档的决策、任务、追踪矩阵；
4. 获得确认后再编码；
5. 重大架构变更增加 ADR。

---

## 20. MVP 最终验收清单

### 20.1 功能

- [ ] 创建、查看、更新项目；
- [ ] 对话或文件启动任务；
- [ ] 生成合法 StoryBible；
- [ ] 生成正好 10 集大纲；
- [ ] 生成前 3 集完整剧本；
- [ ] 逐集输出 9 维评分、总分、问题和建议；
- [ ] 确定性选择最低分集；
- [ ] 自动修订且只产生一个新剧本版本；
- [ ] 连续性检查和重新评分；
- [ ] 版本历史与 Diff；
- [ ] TXT/DOCX 上传与分类；
- [ ] Markdown/DOCX 导出；
- [ ] SSE 进度、失败、重试、取消；
- [ ] 多轮会话和项目记忆。

### 20.2 技术

- [ ] LangGraph State 不存大文本；
- [ ] Artifact 不可变且依赖可追溯；
- [ ] PostgreSQL 是唯一事实源；
- [ ] Redis 丢失后可恢复；
- [ ] 所有 LLM 输出 Schema 校验；
- [ ] FakeLLM 支持确定性 E2E；
- [ ] 幂等、checkpoint 与重试有效；
- [ ] API/Event/OpenAPI 契约一致；
- [ ] Alembic 可升级和回滚；
- [ ] 日志、指标和 run_id 可诊断；
- [ ] 无密钥、全文和完整 Prompt 泄露；
- [ ] 核心/总体覆盖率达标；
- [ ] 性能指标达标。

### 20.3 产品 Demo

- [ ] 新用户在 10 分钟内理解并完成固定 Demo；
- [ ] 页面能解释系统当前在做什么；
- [ ] 失败时用户知道失败节点和是否可重试；
- [ ] 修订前后差异可直观看懂；
- [ ] 分数下降或连续性失败不会被展示为成功；
- [ ] 导出文件能直接交付阅读。

---

## 21. MVP 后续 Backlog

只有 Release Gate 通过后再考虑：

### V1

- 20 集大纲、10 集正文；
- StoryBible/大纲人工确认节点；
- 单集局部编辑与再生成；
- 更强的跨集 Continuity Graph；
- 用户自定义 Rubric 权重；
- 真实对象存储与任务队列；
- 登录、项目权限；
- Web Search/市场趋势 Tool；
- 合规规则库更新机制。

### V2

- 50 集大纲、20 集正文；
- 多版本 A/B 生成；
- 小说长文分段改编；
- 分镜 Prompt；
- 评估稳定性 Dashboard；
- 跨项目题材模板与案例管理；
- 团队评论与审批。

### V3

- 50 集完整剧本工业化生产；
- 剧本到视频 Prompt/视频平台；
- 多租户、审计、配额与计费；
- 市场数据反馈闭环；
- 经过验证的外部 MCP 服务生态。

---

## 22. 最终交付物清单

| 类别 | 文件/产物 |
| --- | --- |
| 源码 | backend、frontend、e2e |
| 基础设施 | docker-compose.yml、.env.example、Makefile |
| 数据 | Alembic migrations、knowledge fixtures、Demo fixtures |
| 契约 | OpenAPI、领域 JSON Schema、SSE Event Schema |
| Prompt | manifest、版本化 templates、golden outputs |
| 测试 | unit、contract、integration、workflow、E2E、security、performance |
| 文档 | README、DEV_PLAN、API、TEST、OPERATIONS、SECURITY、DEMO、KNOWN_LIMITATIONS |
| Demo 资产 | 足球少年固定输入、FakeLLM 输出、预期 Artifact 图 |
| 发布 | CHANGELOG、v0.1.0-rc1、测试报告 |

交付完成的判断依据不是“所有代码文件存在”，而是第 20 节全部勾选且 I / Release Gate 已签字通过。
