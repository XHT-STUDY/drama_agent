# DramaAgent Memory 开发计划

> 状态：已批准，待实施  
> 日期：2026-09-18  
> 设计依据：[MEMORY_DESIGN.md](MEMORY_DESIGN.md)  
> 关联计划：[AGENT_NATIVE_IMPLEMENTATION_PLAN.md](AGENT_NATIVE_IMPLEMENTATION_PLAN.md) 阶段三

## 1. 交付范围

本计划完成三件事：建立 Memory 评测基线，修正对话 Memory 的生产链路，把已有剧情状态模型接成可持久化、可恢复、可按版本失效的能力。

当前不引入 Mem0、新向量数据库、图数据库或第二套任务系统。公开 API 只复用或小幅扩展现有 Artifact、Run 和错误响应。预计改动超过 8 个文件，并涉及 domain、application、memory、workflow、prompt、migration、test 和 frontend；实施时一次只推进一张任务卡。

## 2. 顺序与依赖

```text
M-01 Memory Eval 基线
        │
        ▼
M-02 对话 Memory 生产链路与累计摘要
        │
        ▼
M-03 剧情证据与持久状态
        │
        ▼
M-04 写作、修订、恢复与失效统一读取
        │
        ▼
M-05 上下文保护、可见性与退出验收
        │
        ▼
Mem0 决策门，只评估，不默认实施
```

每个阶段可以独立合并。M-01 只增加评测，不改变生产行为；M-02 改善对话记忆；M-03 让每集得到可恢复状态；M-04 统一消费者；M-05 完成用户可见性和发布门禁。

## 3. M-01：建立 Memory Eval 基线

**目标**：在修改生产实现前，测出当前 Memory 的准确性、使用效果、延迟和 token 成本。

**预计**：1.5 至 2 人日。

**新增文件**：

- `backend/tests/golden/memory/dialogue_cases.json`
- `backend/tests/golden/memory/story_cases.json`
- `backend/tests/evals/test_dialogue_memory_eval.py`
- `backend/tests/evals/test_story_memory_eval.py`
- `backend/scripts/evaluate_memory.py`
- `docs/MEMORY_EVAL_REPORT.md`

**修改文件**：

- `backend/pyproject.toml`，仅在需要新增 `memory_eval` marker 时修改。
- `docs/TEST_PLAN.md`，登记数据集、指标、运行方式和真实模型开关。

**实现要求**：

- 对话集至少 30 组，覆盖 24、48、96、192 条消息。
- 剧情集至少 10 组，每组 10 集，覆盖人物知识、伏笔、道具、时间线和锁定事实。
- 固定四个消融组：无 Memory、recent-only、当前实现、结构化目标实现。
- 自动化测试用 FakeLLM。真实模型运行必须通过显式环境开关，并记录模型、Prompt 版本、样本数、token 和耗时。
- 评分拆成写入、召回、使用和成本四层。不得只用单一 LLM Judge 总分。
- 确定性事实先由代码评分。语义等价问题才使用 Judge，Judge 输入保存为可复查结果。

**验收**：

- 当前实现可以完整跑完评测并产出 JSON 与 Markdown 报告。
- 报告分别给出各消息长度和各消融组结果。
- 项目隔离、版本来源和锁定事实有确定性断言。
- 同一 FakeLLM seed 重跑结果一致。
- 报告明确标注当前 `/agent/turns` 是否实际触发摘要。

**验证命令**：

```bash
cd backend
uv run pytest tests/evals/test_dialogue_memory_eval.py tests/evals/test_story_memory_eval.py
uv run python scripts/evaluate_memory.py --provider fake
```

**回滚**：删除评测文件和文档即可，不涉及生产数据。

## 4. M-02：修正对话 Memory 生产链路

**目标**：所有真实消息入口共享同一套 Memory 挂载；摘要改为累计语义；普通消息提交不等待摘要 LLM。

**预计**：1.5 至 2 人日。

**修改文件**：

- `backend/app/application/conversation_service.py`
- `backend/app/application/agent_command_service.py`
- `backend/app/application/agent_action_lifecycle.py`
- `backend/app/application/agent_context_service.py`
- `backend/app/api/v1/conversations.py`
- `backend/app/memory/short_term.py`
- `backend/app/memory/summary.py`
- `backend/app/domain/summary.py`
- `backend/app/prompts/templates/conversation_summary.md`
- `backend/app/prompts/manifest.yaml`
- `backend/tests/unit/memory/test_short_term.py`
- `backend/tests/integration/memory/test_summary.py`
- `backend/tests/integration/memory/test_summary_reaches_writer.py`
- `backend/tests/integration/api/test_agent_turns.py`

**设计与实现**：

1. 新增统一的 MessageService 工厂或依赖提供器。普通消息 API、Agent Turn 和 Action 结果消息不得各自构造不同语义的 `MessageService()`。
2. `AgentContextService` 通过注入的 `ShortTermStore.recent()` 读取最近消息；Redis miss 自动回源 PostgreSQL。测试可以注入 InMemory 实现。
3. ConversationSummary v2 将上一版累计摘要和新消息区间一起输入模型，输出仍由服务端回填 conversation、覆盖范围和来源字段。
4. 同一会话按覆盖终点和 Artifact 版本稳定选择摘要。跨会话不得比较 sequence。
5. 项目创作上下文按会话分别取得最新累计摘要，使用有界排序合并。默认优先当前会话，其余会话按 Artifact 创建时间取最近若干条，并标明来源会话。
6. 摘要调用移出消息写事务和响应关键路径。post-commit 调度失败时记录缺口；下一次摘要触发或创作 Run 根据 `covered_to_sequence` 补齐。
7. 幂等输入包含 conversation、目标覆盖终点、上一摘要 Artifact、Prompt 版本和新增消息 digest。
8. 旧 v1 摘要保持可读。首次生成 v2 时可把全部已覆盖原始消息和最新 v1 摘要作为迁移输入，不修改旧 Artifact。

**公开契约**：不新增端点。`conversation_summary` 内容 schema 增加 `content_schema_version`、`previous_summary_artifact_id` 和 `source_message_digest`。若前端不展示这些字段，无需修改前端 API 类型。

**失败要求**：

- 消息已提交后，Redis 或摘要失败都不能回滚消息。
- 摘要尚未补齐时，Agent 仍可使用最近消息；需要长历史的创作 Run 可以在后台补齐后继续。
- 同一覆盖区间并发触发只能得到一个有效 v2 摘要。

**验收**：

- 从前端使用的 `/agent/turns` 连续发送消息，达到阈值后产生摘要。
- 48 条消息后的最新摘要包含第一段摘要中的关键约束。
- 两个会话 sequence 相同时，项目级排序按 Artifact 时间和版本稳定，不串会话。
- Redis 清空、Redis 连接失败和服务重启后，最近消息从 DB 恢复。
- 普通消息响应耗时不包含摘要 LLM 调用。
- M-01 的对话指标达到设计门槛。

**验证命令**：

```bash
cd backend
uv run pytest tests/unit/memory/test_short_term.py tests/unit/application/test_agent_context_service.py
uv run pytest tests/integration/memory/test_summary.py tests/integration/memory/test_summary_reaches_writer.py
uv run pytest tests/integration/api/test_agent_turns.py
```

**回滚**：关闭 v2 摘要写入，继续读取 v1/v2；统一 MessageService 和 Redis 回源保留。新摘要 Artifact 不删除。

## 5. M-03：持久化剧情证据与 ContinuityState

**目标**：每个候选剧本版本都有可追溯的单集摘要和连续性状态，正文与派生状态失败分账。

**预计**：2.5 至 3.5 人日。

**映射**：对应 Agent Native 计划 W3-01、W3-02。本节补充 Memory Eval 和交付顺序，不改变 W3 的状态契约。

**修改范围**：

- `backend/app/domain/summary.py`
- `backend/app/domain/continuity.py`
- `backend/app/domain/story_bible.py`
- `backend/app/domain/enums.py`
- `backend/app/application/artifact_service.py`
- 新增 `backend/app/application/story_state_service.py`
- `backend/app/memory/continuity.py`
- `backend/app/skills/summarizer.py`
- 新增 `backend/app/prompts/templates/episode_summary_v2.md`
- `backend/app/prompts/manifest.yaml`
- `backend/app/db/repositories/artifacts.py`
- 新增 `backend/migrations/versions/0013_story_state_dedup.py`
- `backend/tests/contract/test_story_state_v2.py`
- `backend/tests/integration/memory/test_story_state_service.py`

**实现要求**：

- typed delta 区分设定事实、正文事件、角色知识、关系变化、伏笔变化和作者未来计划。
- 每条变化带 source Artifact、场景或字段引用及生效集数。
- 事实、角色和伏笔使用稳定 ID；旧文本字段通过确定性 adapter 读取。
- Summarizer 不能创建不存在的角色引用，不能把未来计划写成已发生事件，不能填写用户确认字段。
- `StoryStateService` 负责调用 Summarizer、校验引用、执行纯 reducer，并在短事务中保存 EpisodeSummary、ContinuityState、ArtifactLink 和事件。
- 正文保存与派生保存分开。派生失败保留正文，状态显示 pending/failed。
- 以项目、类型、集数和 input hash 保证同一来源只产生一份有效 v2 派生结果。

**验收**：

- 第 1 集发生秘密事件，第 2 集某角色才得知时，作者事实与角色知识分开记录。
- 源稿、Prompt 版本或前态变化会产生新的派生结果；相同输入重试不重复调用模型。
- 项目 A 的来源不能出现在项目 B 状态中。
- 关系、伏笔、时间线和人物知识进入状态并通过 Pydantic 完整校验。
- 派生失败后正文 checksum 不变，恢复只补派生。
- M-01 剧情数据集可消费 v2 状态。

**验证命令**：

```bash
cd backend
uv run pytest tests/contract/test_story_state_v2.py
uv run pytest tests/unit/skills/test_summarizer.py tests/unit/memory/test_continuity.py
uv run pytest tests/integration/memory/test_story_state_service.py
uv run pytest tests/integration/db/test_migration.py
```

**回滚**：关闭 v2 写入，保留 v1/v2 兼容读取。已生成的证据 Artifact 不删除，不用旧 writer 覆盖 v2 语义。

## 6. M-04：统一写作、修订、恢复和失效读取

**目标**：所有消费剧情状态的路径都按确切工作集读取同一份前态，采用变化后只重建必要后缀。

**预计**：2.5 至 3.5 人日。

**映射**：对应 Agent Native 计划 W3-03、W3-04、W3-06。

**修改范围**：

- `backend/app/workflows/nodes/write_episode.py`
- `backend/app/workflows/nodes/continuity_check.py`
- `backend/app/workflows/nodes/prepare_conversational_revision.py`
- `backend/app/workflows/nodes/select_revision.py`
- `backend/app/workflows/nodes/revise.py`
- `backend/app/workflows/state.py`
- `backend/app/application/workflow_dispatcher.py`
- `backend/app/application/run_service.py`
- `backend/app/application/story_state_service.py`
- `backend/app/memory/context_builder.py`
- `backend/app/domain/context.py`
- `backend/app/tools/outline_impact.py`
- `backend/app/db/repositories/artifacts.py`
- 相关 API、workflow 和 memory 集成测试

**实现要求**：

- Writer 进入第 N 集前加载 `through=N-1` 的状态。已有剧本只跳过正文生成，不能跳过派生证据完整性检查。
- 连续性检查和 Reviser 使用同一 StoryStateService，不再用标题与动作截断重放。
- Run State 新增 `continuity_state_artifact_id`、`episode_summary_artifact_ids` 和 `derivation_pending_episode`。旧 `continuity_state_text` 只供旧 Run 兼容。
- 采用第 N 集新版本后，N 及后续状态相对新工作集标记 stale；来源完全相同的前缀可复用。
- `GET /projects/{id}/story-state` 只解析状态，不调用模型。显式刷新复用现有 Run 机制。
- ContextBuilder 将当前用户约束、适用锁定事实、当前目标和必要前态设为 protected/required。
- RAG 仍是可选增强。确切稿件或状态读取失败时 fail closed，不能降级为空上下文继续写。

**错误码**：

- `REQUIRED_CONTEXT_MISSING`
- `STORY_STATE_STALE`
- `STORY_STATE_GAP`
- `PROTECTED_CONTEXT_TOO_LARGE`

继续沿用现有 ErrorResponse，不新建错误协议。

**验收**：

- 一次写 5 集与 1+1+3 分批写作使用相同前态语义。
- 服务重启后续写，角色知识、伏笔和关系状态不丢失。
- 修订第 N 集时不把候选新稿先写入 `through=N-1` 前态。
- 采用变化只使必要后缀待复核，历史工作集仍可读取。
- 必需来源缺失时，测试桩断言 Writer 没有被调用。
- 可选 RAG 断开时仍可生成并带 warning。

**验证命令**：

```bash
cd backend
uv run pytest tests/integration/workflow/test_story_state_recovery.py
uv run pytest tests/integration/workflow/test_staged_creation.py tests/integration/api/test_run_continue.py
uv run pytest tests/integration/artifacts/test_story_state_invalidation.py
uv run pytest tests/unit/memory/test_context_budget.py tests/unit/revision/test_continuity_check.py
```

**回滚**：旧语义 Run 继续走旧读取路径。新语义 Run 在安全边界暂停，保留 v2 状态只读和恢复能力，不把 v2 Run 交给只认识 `continuity_state_text` 的旧 worker。

## 7. M-05：状态可见性与退出验收

**目标**：作者能看到当前状态基于哪一稿、截至哪一集、哪些内容待复核，并完成全量回归和发布说明。

**预计**：1 至 1.5 人日。

**映射**：对应 Agent Native 计划 W3-07。

**修改范围**：

- `frontend/src/features/story-bible/StoryBibleView.tsx`
- `frontend/src/features/story-bible/CharacterCard.tsx`
- `frontend/src/features/agent/AgentWorkspace.tsx`
- `frontend/src/types/api.ts`
- 对应查询 hook 和组件测试
- `docs/API_CONTRACT.md`
- `docs/TEST_PLAN.md`
- `docs/KNOWN_LIMITATIONS.md`
- `docs/MEMORY_EVAL_REPORT.md`
- `docs/DEV_PLAN.md`
- `docs/DEV_LOG.md`

**界面要求**：

- 显示状态截至第 N 集、采用工作集或候选工作集。
- 区分作者事实、角色已知信息、作者未来计划和模型推断。
- 显示 ready、pending、failed、stale，并提供对应恢复入口。
- 不把“嵌入”“摘要任务”“Memory job”作为作者主流程概念。
- 来源可以跳转到确切 Artifact 和场景。

**退出门槛**：

- M-01 的所有确定性门槛通过。
- 真实模型固定集至少执行一次，报告包含模型、Prompt、样本、成本和时间。
- 后端 lint、typecheck、全量测试通过。
- 前端 lint、typecheck、组件测试通过。
- PostgreSQL 与 Redis 集成测试通过。
- 旧项目、旧 Run 和 v1 摘要保持可读。
- 迁移升级和安全 downgrade 路径验证通过。

**验证命令**：

```bash
make lint
make typecheck
make test
make e2e REPEAT=1
```

**回滚**：关闭状态面板和新写入入口，保留新 Artifact 与兼容读取。数据库迁移不得删除历史派生证据。

## 8. Mem0 决策门

M-05 完成后才评估 Mem0。满足以下条件才允许进入 POC：

1. 产品已经需要跨项目记住同一用户的长期创作偏好。
2. M-01 显示现有累计摘要在该场景的关键偏好召回率低于 90%。
3. 使用现有 PostgreSQL 与 pgvector 的最小方案仍不能达标。
4. 可以把 Mem0 限制为 soft memory，不参与权威剧情状态和工作流决策。
5. 服务不可用时能无损退回现有路径。

POC 只做 shadow mode，不影响生产 Prompt。比较 recall、precision、最新事实胜率、虚构率、p95 延迟、token 和外部调用成本。没有明确收益时不引入依赖。

## 9. 迁移与兼容原则

- 旧 Artifact 不 UPDATE，不在迁移中调用模型。
- v1/v2 通过 `content_schema_version` 分派读取模型。
- 新索引只约束 v2 派生记录，不能让历史重复数据阻断升级。
- 新 Run 写入 `story_state_semantics_version=2`，旧 Run 保持旧语义。
- downgrade 不能让数据库中已存在的 v2 内容变得不可读；必要时拒绝破坏性 downgrade。
- 所有新增 Prompt 都有版本号、manifest 注册和固定 FakeLLM fixture。

## 10. 完成后的稳定边界

- Message 与 Artifact 保存事实。
- Redis 加速最近消息读取。
- ConversationSummary 压缩历史意图。
- EpisodeSummary 保存正文提取证据。
- ContinuityState 表示确切工作集下的剧情状态。
- RAG 提供可选知识，不决定项目事实。
- ContextBuilder 决定本轮模型看到什么，并记录取舍。
- Mem0 若以后引入，只保存跨项目软偏好。
