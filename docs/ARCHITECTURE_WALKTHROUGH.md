# DramaAgent 项目架构讲解（会话整理稿）

> 本文由一次架构讲解会话整理而成，内容按讲解顺序原样收录，共五部分：
>
> 1. 项目整体架构与核心功能总览
> 2. 对话式创作 Agent（Phase J）深度拆解
> 3. LangGraph 工作流直白拆解
> 4. 异步执行引擎：Run 调度 + SSE
> 5. 评估与自动修订
>
> 讲解基于 2026-09 时点的代码库（main 分支），所有 file:line 引用以当时代码为准。

---

## 目录

- [第一部分：项目架构讲解](#第一部分项目架构讲解)
- [第二部分：对话式创作 Agent（Phase J）深度拆解](#第二部分对话式创作-agentphase-j深度拆解)
- [第三部分：LangGraph 工作流直白拆解](#第三部分langgraph-工作流直白拆解)
- [第四部分：异步执行引擎——Run 调度 + SSE](#第四部分异步执行引擎run-调度--sse)
- [第五部分：评估与自动修订](#第五部分评估与自动修订)

---

# 第一部分：项目架构讲解

## 一、这是什么

**面向中文短剧创作的对话型 Agent 系统**。定位不是"单次 Prompt 生成器"，而是一条**有状态、多阶段、可恢复、可审计的创作流水线**：输入一个想法/大纲/原稿 → 产出 StoryBible → 10 集大纲 → 前 3 集剧本 → 逐集评估 → 自动修订 → 版本 Diff → 导出；外层再包一个自然语言对话 Agent，让用户用聊天的方式驱动这条流水线。

## 二、底层架构：分层 + 两条铁律

技术栈：FastAPI + Pydantic v2 / **LangGraph** / SQLAlchemy 2 (async) + Alembic / **PostgreSQL + pgvector** / Redis / Next.js + React + TS + TanStack Query / Tailwind。

```
Next.js 工作台 (frontend/src)
   │  REST + SSE（原生 EventSource）
   ▼
FastAPI API 层 (backend/app/api/v1)          ← 立即返回 run_id/action_id
   ▼
Application Services (application/)          ← Run/Turn/Action 生命周期、幂等、状态机
   ▼
LangGraph Workflows (workflows/)             ← 有状态节点图 + PostgreSQL checkpoint
   ▼
Agents + Skills (agents/, skills/)           ← 调 LLM，产出结构化内容（Pydantic 强校验）
   ▼
Tools (tools/)                               ← 纯确定性，不调 LLM（diff/字数/文件解析/导出…）
   ▼
Repositories (db/repositories/)              ← SQLAlchemy 2 async
   ▼
PostgreSQL(+pgvector) = 唯一事实源    Redis = 瞬态（SSE pub/sub、短期记忆、限流）
```

两条贯穿全部代码的铁律：

1. **PostgreSQL 是唯一事实源**。Redis 丢了最多丢 SSE 实时推送（有 DB 轮询兜底）和短期记忆缓存（可从 Message 表恢复），绝不造成资产损失。
2. **确定性归代码，生成归模型**。选哪集修订、总分怎么算、要不要修订、流程怎么路由——全是确定性纯函数；LLM 只负责产出结构化内容，且必须过 Pydantic 校验 + 服务端回填/钳制才能入库。

## 三、核心功能与实现

### 1. 创作主链路（LangGraph 工作流）

核心在 `workflows/creation.py:109` 的 `build_creation_workflow()`，一张 11 节点的状态图：

```
normalize → retrieve → story_bible → outline → write_episodes
    → evaluate_episodes ─┬─→ finalize
                         ├─(stage_gate 停在大纲门/剧本门)→ END 等确认
                         └─(需修订)→ select_revision → revise → continuity_check
                                        → re_evaluate ─(还有低分集且轮次未满)─┐
                                              ▲______________________________|
```

实现上的三个关键机制：

- **State 轻量化**（`workflows/state.py:12`）：`CreationState` 只存 Artifact ID、集号、`revision_round`、`completed_nodes` 等轻量字段，**大文本一律不进 State**，节点内按 ID 查库取。这是为了 checkpoint 不膨胀、断点恢复便宜。
- **统一节点骨架**：每个节点都是"取消守卫 → failed 短路 → `completed_nodes` 早退 → 发 `node.started` 事件 → Skill 执行 → 落 Artifact → 发完成事件"。`write_episodes_node` 逐集循环且每集写入前再查一次取消。
- **条件路由全是确定性函数**（`creation.py:45-185`）：比如大纲生成完可以停在"大纲确认门"（`stop_after="outline"`），用户确认后续跑。

### 2. 异步执行引擎：Run 调度 + SSE 进度

这是整个系统的"骨架"，实现相当讲究：

- **API 立即返回**：`POST /projects/{id}/runs`（`api/v1/runs.py:177`）只创建 `status=queued` 的 Run 落库，commit 后 best-effort 唤醒 Worker，返回 202 + `run_id`。
- **数据库是执行事实源**（`application/workflow_dispatcher.py:61`）：Worker 用 `FOR UPDATE SKIP LOCKED` 原子领取 Run，带租约（`lease_owner/lease_expires_at`）+ 心跳续租，进程崩了租约过期自动被别的 Worker 接管重跑。API 里的"唤醒"只是通知，就算丢了，扫描也会捡起来。
- **SSE 三层保障**（`api/v1/events/stream.py:30`）：事件先 INSERT 进 `workflow_events` 表（sequence 原子递增），再 best-effort 发 Redis pub/sub；SSE 端点按 `Last-Event-ID` 从 PG 补发历史（断线续传）+ Redis 实时推送 + **始终运行的 DB 轮询兜底**。Redis 全挂，进度照样不丢不乱序。
- **断点恢复不重调 LLM**：两层叠加——LangGraph 的 `AsyncPostgresSaver` checkpoint（`thread_id=run_id`，`workflows/persistence.py:26`）+ `state_summary` 逻辑恢复（`workflows/checkpoint.py:112`）。重试时把上次状态合并回来，节点入口 `completed_nodes` 早退 + `input_hash` 幂等去重，已完成的节点、已生成的 Artifact 都不会重来。
- **协作式取消**（`workflows/checkpoint.py:35`）：`RunCancelledError` 故意继承 `BaseException`，这样节点里的 `except Exception` 吞不掉它，保证取消后不会多写一个 Artifact。

### 3. Artifact 不可变版本模型

这是数据层的地基（`artifacts/store.py:49`）：

- **content 永不 UPDATE，修订 = INSERT 新版本行**。唯一约束 `(project_id, type, episode_number, version)`，并发冲突靠唯一约束兜底后重读重试。
- **幂等靠 `input_hash`**（`artifacts/versions.py:33`）：对 `{集号, 类型, 排序后的来源 ID, dedup_extra}` 做 SHA256，同输入重复请求直接返回已有记录——这也是断点恢复不重复建 Artifact 的底层保证。
- **`artifact_links` 表记溯源**（`db/models/artifact_link.py`）：`derived_from / evaluates / revises / continues` 有向边，评估、大纲影响分析、Diff 都靠它追溯"这个版本从哪来"。
- **Diff 纯确定性**（`artifacts/diff_service.py:34`）：剧本走场景级对齐 diff（行相似度匹配），解析失败回退全文行 diff，不调 LLM、不持久化。

### 4. 评估与自动修订

- **9 维度 Rubric 是 YAML 数据资产**（`knowledge/rubric/mvp_v2.yaml` + `domain/rubric.py`）：开场钩子 0.15 / 冲突强度 0.15 / 爽点密度 0.15 / 结尾钩子 0.15……权重和必须等于 1，五档锚点描述齐全，启动时强校验。
- **服务端回填，不信任 LLM 自报**（`skills/evaluator.py:337`）：LLM 只做"在锚点矩阵上定档 + 给证据引用"，`overall_score` 由服务端加权计算，`need_revision` 由确定性规则判定（总分 < 75 或有 high 级 issue 或合规分 < 60），维度分还会被 clamp 进所定档位的分带防止自相矛盾，证据引用做归一化溯源校验。
- **自动修订选集是纯函数**（`domain/revision.py:191`）：`min(需修订集, key=(overall_score, episode_number))`——最低分优先、平局取最小集号，零 LLM 调用。
- **修订闭环**：生成修订计划（issue 确定性映射为 operation，臆造的 operation 被剔除）→ Reviser 产出**完整新稿**（不是 patch）→ 以 draft 落库为新版本 → 连续性检查（确定性规则 + LLM 语义双保险）通过才提升 valid → 重评。重评掉分超过 5 分直接转"需人工复核"（防止越改越差还自动循环）。修订轮数 `MAX_REVISION_ROUNDS=1` 由路由函数控制。

### 5. LLM 基础设施（llm/）

五个文件各司一职：

- `protocol.py`：`LLMClient` 抽象只有一个方法 `generate_structured(schema, ...)`，**契约是"不抛普通异常，所有错误写 `result.error_code`"**。`OpenAICompatibleLLM` 和测试用 `FakeLLM`（按 prompt 名返回 golden fixture）实现同一协议——这是 1099 个测试不花一分钱 API 费的原因。
- `structured_output.py`：JSON 解析失败带"上次错在哪"的反馈自动重试 2 次；`finish_reason=length` 快速失败不重试（注释里记着 story_bible 白烧 3 次的事故复盘）。
- `retry.py`：429/超时/5xx 指数退避（0.5s × 2^n，上限 30s），**`Retry-After` 头优先**；4xx 判定为配置错误不可重试。
- `budget.py`：per-run 预算，软上限（默认 18 次调用）只告警，硬上限（24 次 / 20 万 token）在**每次真实调用前**检查，超限抛错终止整个 Run——这是成本保护阀。

### 6. RAG 三阶段检索（rag/ + workflows/nodes/retrieve.py）

- **入库**：Markdown/JSON 语料 → 按标题层级切块（保留 heading_path，单块上限 600 字）→ `text-embedding-3-small`（pgvector `Vector(1536)`，HNSW 索引）→ 文档/chunk 双层 content-hash 幂等（同 hash 跳过，标题同 hash 不同则只重建变化的块、**保留未变块的 embedding 省钱**）。
- **三阶段**（`rag/retriever.py:39`）：同一个检索器按创作阶段过滤不同知识分类——story_bible 阶段检索"类型模板/角色原型"，outline 阶段检索"开场钩子/类型模板"，writer 阶段检索"爽点/角色原型"。每阶段各存一个 `retrieval_trace` Artifact 可审计。
- **失败降级不阻断**：单阶段失败返回空字符串，检索器整体初始化失败返回空上下文——"删掉 RAG，主流程照样跑通"。这是一个明确的设计立场：检索是增强，不是依赖。

### 7. 记忆系统（memory/）

三层记忆 + 一个上下文装配器：

- **短期**：Redis 滑窗（最近 12 条消息，TTL 7 天），Redis miss 时从 PG Message 表恢复——"Redis 丢了不丢消息"。
- **中期**：会话摘要 Artifact，消息数到阈值才触发，每次只摘上次摘要之后的新消息（幂等键防重复摘要）。
- **项目记忆**：`ContinuityState`——锁定事实（**只增不减**）、开放伏笔编号、角色状态、时间线。每集写完不可变更新。
- **ContextBuilder**（`memory/context_builder.py:159`）：按任务类型分配 token 预算权重（writer 重设定和连续性，requirement 重用户请求），辅助段超限按句截断并明示"[已截断]"，但**当前目标正文永不静默截断**——放不下就抛 413，不假装读完。每次装配产出 `ContextManifest` 审计记录。

### 8. 对话式创作 Agent（Phase J，最复杂的一块）

设计文档 `DESIGN.md` 的核心论断是"**聊天是控制面，不是内容事实源**"。落地为 Turn → Plan → Confirm → Action → Run → Outcome 六步：

- **Planner 只产出选择器，不产出可执行权限**（`skills/agent_command_planner.py`）：意图是服务端白名单（`Literal` 硬编码 6 种），Planner 输出会被正则黑名单扫描（出现 UUID/artifact_id/URL 即拒绝），真正的执行计划（含目标 Artifact ID）由服务端 `_build_action_plan` 模板化生成——Planner 只贡献"用户想干嘛"。
- **先澄清再动手**（preflight clarification）：LLM 调用**之前**有一层确定性歧义检测——集数越界、"既要又要"冲突、一次点名多集多对象、指代词无上下文，统统先反问不让模型猜。支持中文数字集数解析，连续 3 轮澄清失败就给兜底命令列表。
- **Turn 幂等**（`application/agent_command_service.py:294`）：DB 唯一约束 `(project_id, idempotency_key)` + `request_hash`（同键不同载荷报错）+ planning 租约（并发同 key 只有一个胜者在规划，败者拿到胜者的持久化结果）。
- **写动作先确认**：所有 plan 类 Action 默认 `requires_confirmation=True`，确认时行锁内**逐个校验计划引用的 Artifact 快照是否还是最新版本**（任何不符 → Action 置 `stale`，"计划过期请重新发起"）——防止拿着旧大纲的上下文改新大纲。
- **并发约束**：部分唯一索引 `project_id UNIQUE WHERE status IN ('queued','running')` 在数据库层保证单项目单活动 Run，确认时把索引冲突翻译成用户可读的"项目已有任务运行中"。
- **Outcome 判断零模型调用**（`application/agent_outcome_service.py:77`）：goal_status 全部由确定性证据推导（Run 终态、新旧评分差、大纲影响分析），逐条创作要求一律标 `unverified`——"执行完成、评分上涨都不自动视为满足语义要求"，诚实降级为 `partially_achieved`。保留的 LLM Evaluator 即使启用也被权限封顶（无权改 goal_status）。
- **大纲修订防污染**（`workflows/nodes/revise_outline.py`）：要求输出完整大纲集（不接受 patch），服务端校验不变量（集数不变、集号连续、锁定事实未被否定词反转——bigram 相似度 0.75 阈值）；受影响的下游剧本**不静默重写**，而是产出 follow-up 建议变成一个"需确认的子 Action"等用户点头。

### 9. 前端工作台（frontend/）

- **三区布局**（`features/agent/AgentWorkspace.tsx:578`）：左 2 栏作品导航 + 中 6 栏作品画布 + 右 4 栏常驻对话，窄屏降级为 tab。**导航事实全在 URL**（`?artifact=&scene=&panel=&compare=` 六个参数），刷新/分享/后退都能还原现场。
- **阅读不被消息打扰**：Run 跑完出新稿只弹"打开本轮新稿"横幅，不自动跳转——看稿的主动权在用户。
- **SSE**：原生 `EventSource`（自带重连 + Last-Event-ID），`_deriveNodeProgress` 纯函数从事件流推导 14 节点进度条。Turn 发送 202 时 1.5s 轮询直到终态，失败重发复用同一个 `crypto.randomUUID()` 幂等键。
- **一键回滚**：`NEXT_PUBLIC_AGENT_WORKSPACE_ENABLED=false` 切回旧版界面，两套 UI 共用同一后端。

### 10. 安全 / 可观测 / 扩展

- **安全**（`core/security.py` + `prompts/loader.py`）：Prompt 注入防护靠"内容边界隔离"——所有用户输入在 Prompt 里用`【用户内容开始/结束】`定界符包裹 + 免责声明；上传文件磁盘路径用 UUID 不用客户端文件名 + 双重路径逃逸校验；DOCX 拒绝宏文档、拒绝 ZIP 伪装的 TXT；日志出口全量正则脱敏（`RedactFilter` 挂根 handler）。
- **可观测**：自实现的无依赖 Prometheus 指标（9 个，禁止 run_id 等高基数标签），`GET /runs/{id}/diagnostics` 从事件表聚合出每节点耗时/LLM 调用数/token/错误码——不新增存储。轻量 tracing 用 ContextVar 串 request_id → run_id → node_name 进日志。
- **扩展**：Skill（可调 LLM）和 Tool（必须纯确定性、强制声明 JSON Schema）双注册表契约；MCP 外部工具适配器把 JSON-RPC 2.0 工具映射为内部 Tool，**默认关闭**（`mcp_enabled=False`），开启前主流程零影响。

## 四、这套架构真正的"底层"

剥开所有功能，这个项目反复在做同一件事：**把"不可靠的 LLM"关进"确定性的笼子"**。具体是五个不变量：

1. **所有状态变迁走 DB 状态机白名单**（Run/Turn/Action 三套 `*_TRANSITIONS`），非法流转在行锁内被拒；
2. **所有写操作幂等**（input_hash、idempotency_key、收据事件、部分唯一索引），任何一层重试/并发/崩溃都不会产生重复资产；
3. **所有 LLM 输出不可直接生效**——过 Pydantic 校验、服务端回填计算字段、证据溯源验证、越权输出忽略；
4. **所有失败机器可读**（统一 `error_code` 分类）、可恢复（checkpoint + 幂等）、可观测（事件全量落库可重放）；
5. **所有增强可降级**（RAG 删掉能跑、Redis 丢了不丢数据、检索失败返回空）。

这五个不变量也是它测试能全绿（后端 1099 passed，全用 FakeLLM 零 API 费）的根本原因——**被测的是确定性骨架，模型只是可替换的生成器**。

---

# 第二部分：对话式创作 Agent（Phase J）深度拆解

## 0. 出发点：它到底解决什么问题

MVP 完成后系统已经能跑完整创作流水线，但有个根本缺陷：**前端 `ChatInput` 只把输入当作"首次创作 Idea"**——它是一次性表单，不是对话。用户不能用自然语言说"把第三集男女主的冲突提前，但别暴露凶手身份"，看不到 Agent 对修改目标、影响范围、执行步骤的判断。

而基础设施其实都齐了：Conversation/Message、Run/SSE、Artifact 版本、评估修订。所以 Phase J 的本质不是造新能力，而是**把"一次性表单"升级成"可持续对话、可澄清、可计划、可确认、可执行、可追溯"的控制闭环**（DESIGN.md §1）。

## 1. 总设计哲学：聊天是控制面，不是内容事实源

这是第一决策（§4.1），其余一切都从它推导：

> Conversation/Message 保存用户意图、Agent 解释和执行反馈；Story Bible、大纲、剧本、评估仍以 Artifact 为唯一业务事实源。**聊天文本不能直接覆盖 Artifact。**

为什么？三个理由：

1. **内容资产需要版本化、溯源、评估、导出**——消息表天然不具备这些结构；
2. **LLM 的聊天输出不可信**，让它直接改资产等于把数据库写权限交给一个会幻觉的东西；
3. **对话是易失的**，用户应该能删会话、换会话，而作品不受影响。

由此推出整个架构形态：聊天只产生"决策"（意图、计划、确认），所有内容变更都走已验证的 Run → Workflow → Artifact 通道。

## 2. 对象模型：五个实体，各管一段

```
Conversation ─< Message          对话记录（kind: text/clarification/action_plan/action_result/error）
     │
     └─< AgentTurn               请求收据：received → planning → needs_input / answered / action_proposed / failed
              │
              └─< AgentAction    执行审计：proposed → queued → running → completed/needs_review/failed
              │                  （另有 cancelled / stale / rejected；parent_action_id + replan_depth ≤ 1）
              │
              └─< WorkflowRun    执行体：复用 MVP 的调度/checkpoint/SSE（run_id unique 关联回 Action）
                       │
                       └─< Artifact      内容事实：不可变版本
```

**Turn 和 Action 为什么要分成两个实体？**（§4.3）因为它们的生命周期根本不同：

- **Turn 负责"请求幂等"**——一次用户输入不管结局是澄清、回答、计划还是失败，都要能重放返回原结果，不重复调模型。大多数 Turn 根本不会产生 Action（澄清和纯解释占很大比例）。
- **Action 负责"执行审计"**——每次写操作持久化结构化计划、来源 Artifact 快照（id/type/episode/version/checksum）、关联的 Run 和结果摘要。它可能跨多幕执行（门上暂停 → 续跑 → 终态）。

一个管"请求维度"，一个管"副作用维度"，混在一个实体里会导致澄清请求也背着执行状态机走。

## 3. 运作时序：一句话进来之后发生了什么

这是 `DESIGN.md §7.1` 的核心数据流，也是理解全部设计的钥匙：

```
用户输入「把第三集冲突提前，但不要暴露凶手」
│
├─ Phase A：短事务（毫秒级）
│    校验项目/会话/active_context（artifact 必须属于本项目、类型集数匹配）
│    get-or-create AgentTurn（(project_id, idempotency_key) 唯一约束 + request_hash）
│    短暂锁定 Conversation 行，追加用户消息（sequence 唯一约束防并发重复）
│    commit —— 释放所有锁
│
├─ 事务外（可能持续数秒到数十秒）
│    原子领取 planning 租约（UPDATE...RETURNING，只有 received 或租约过期可领）
│    确定性短路层：整句"确认/继续/重试"→ 直达服务，不调模型
│    preflight 澄清：集数越界/"既要又要"/多目标/指代词无上下文 → 直接追问，不调模型
│    组装有预算的上下文（AgentContextService：最近消息 + 项目索引，不含剧本全文）
│    调 LLM Planner（零事务持有！）
│    输出过四层校验（见 §4.1）
│
├─ Phase B：短事务
│    锁 Turn 行、验证自己仍是租约持有人
│    ├─ clarification → 追加澄清消息，Turn → needs_input
│    ├─ answer       → 追加解释消息（引文经 verify_quote 验证后回填出处），Turn → answered
│    └─ plan         → 服务端模板化生成 AgentActionPlan → 创建 proposed Action
│                      → 追加 action_plan 消息，Turn → action_proposed
│
├─ 用户点「确认」（Phase C：短事务）
│    行锁 Action → 防重复（已有 run_id 直接返回原 Run）
│    快照过期检测：计划引用的每个 Artifact 快照 vs 当前 latest valid（不符 → stale）
│    continue 计划：二次校验 Run 仍在门上 + stage_generation 世代校验
│    原子创建 WorkflowRun(queued)（部分唯一索引兜底并发）→ 关联 Action.run_id
│
├─ 执行（复用 MVP 引擎）
│    Dispatcher FOR UPDATE SKIP LOCKED 领取 → 租约 + 心跳 → workflow.ainvoke(thread_id=run_id)
│    conversational_revision / outline_revision 工作流产出不可变新版本
│
└─ 回写（J-09 Lifecycle）
     按幕次键幂等回写 Action 终态 + action_result 消息
     AgentOutcomeService 确定性判断 goal_status
     partially_achieved/blocked → 至多创建一个 proposed 子 Action，等用户确认
```

**为什么 Planner 必须在事务外？**（§12 点名的风险："跨 LLM 长事务会造成锁等待"）LLM 调用几秒起步，如果握着 DB 事务/行锁，同一会话的并发消息、甚至不相关请求都会被拖死。所以拆成三段短事务 + 中间无事务的窗口期，而"进程在窗口期挂了怎么办"由 planning 租约解决：租约过期后下一个同幂等请求原子接管，重新规划。

## 4. 七个关键机制：是什么、怎么做、为什么

### 4.1 Planner 只产出选择器，不产出可执行权限

这是整个 Agent 安全模型的基石（§4.2）。四层防线：

| 层 | 机制 | 位置 |
|---|---|---|
| Schema | Planner 输出结构里**根本没有执行句柄**——`PlannerTarget` 只有 target_type + episode_number，docstring 明写"不携带 Artifact ID" | `domain/agent_planner.py:39` |
| 扫描 | 正则黑名单递归扫描输出全部字符串，命中 UUID/URL/SQL/api 路径即拒绝 | `skills/agent_command_planner.py:100` |
| 生成 | 真正的执行计划由**服务端模板**按 intent 分支生成，目标 Artifact 全部由服务端解析最新 valid 版本 | `application/agent_command_service.py:1225` |
| 白名单 | intent 必须在**服务端动态生成的**白名单内——`continue` 意图只在项目存在停在确认门的 Run 时才开放，否则"继续"永远不会被判成一个无法执行的计划 | `agent_command_service.py:769` |

**为什么？** §3.2 写得很直白："不允许模型自由调用任意内部 API"。传统做法是 prompt 里写"请不要输出危险内容"——那是求一个概率模型别犯错。这里的选择是让模型在**结构上不可能**越权：它拿到的输出通道里就没有执行字段。哪怕被 prompt 注入，它能做的最多是"选错目标类型"，而选错的结果要么被语义校验拒绝，要么走正常确认流程被用户看见。

### 4.2 确定性优先：能不调模型就不调模型

LLM 之前有两层确定性拦截：

- **Shortcut 层**（`skills/agent_shortcut.py:81`）：整句锚定正则 + 长度 ≤16 匹配"确认/继续/重试"类短语，直达 `confirm_action`/`continue_gated_run`，且"最新 pending 优先"（避免把过期计划确认成第二个并行 Run）。
- **Preflight 澄清层**（`skills/agent_command_planner.py:137`）：在调模型**之前**检查确定性歧义——集数越界、"既要…又要…"冲突、一次点名多集多对象、指代词（"这里/当前稿"）无活动上下文。命中即直接返回澄清问题。注意一个细节：**"裸'剧本'不算明确目标"**——因为每集一份，还差集数，这种半模糊状态也追问。连续 3 轮没解决就给兜底命令列表，防止无限澄清循环。

**为什么？** 三个账：省钱（每次澄清省一次 LLM 调用）、省时（确定性路径零延迟）、**可测**（模糊指令、过期计划、重复确认全部有确定性测试，§2 成功标准要求这一点——确定性逻辑才能写确定性断言）。还有一个更深的理由：把确定性部分从模型身上剥离之后，剩下的"意图理解"才是模型真正该干的活，50 条标注意图 90% 准确率的评测指标也才成立。

### 4.3 Turn 幂等：三段式 + 租约

- DB 层 `(project_id, idempotency_key)` 唯一约束；`request_hash` 是规范化载荷的 SHA256——**同键不同载荷返回 409**，不是返回旧响应（防止前端 bug 复用幂等键吞掉新请求）。
- `INSERT ... ON CONFLICT DO NOTHING` 原子创建，并发同 key 的败者 rollback 后回读胜者结果。
- planning 租约：`transition` 要求写 planning 状态必须持有效租约；领取是 `UPDATE...RETURNING` 原子操作。

**为什么租约放 DB 行上而不是 Redis？** 因为 §2 成功标准要求"进程在规划中退出后，租约过期的 Turn 可以由后续请求重新领取"，而本项目的铁律是 PostgreSQL 唯一事实源——把锁状态放进 Redis 就引入了第二个事实源。DB 行锁方案慢一点，但崩溃语义简单可靠：行还在，租约字段过期，接管即可。

### 4.4 写动作先确认 + 快照过期检测

所有 plan 类 Action 默认 `requires_confirmation=True`；确认接口**不接受客户端回传的计划内容**，只执行服务端持久化的那份（§6.2）。

确认时的过期检测是最精妙的一处：行锁内逐个把计划保存的 `ArtifactSnapshot(id, version, checksum)` 与当前 latest valid 比对，任何不符 → Action 置 `stale` + 抛 `AgentActionStaleError`（"项目内容已更新，请重新生成计划"）。

**为什么？** 还原一个真实场景：用户让 AI 出了"改第 3 集"的计划 → 转头自己在画布上手动改了第 3 集（或跑了个别的 Run 产生了新版本）→ 回来点确认。没有快照校验，系统会**基于旧版本的上下文执行修订计划**，产出一个混合了新旧认知的"缝合稿"。stale 检测把"计划的有效期"工程化了——计划不是永久有效的承诺，而是对某个时刻项目状态的快照提案。

### 4.5 大纲变更不静默污染下游

这是产品层面最克制的一个决策（§4.5 + §3.2 明确"不自动重写受影响的剧本"）。链条：

1. `OutlineReviserSkill` 输出**完整大纲集**（Schema 层不接受 patch），服务端校验不变量：集数不变、集号连续 1..N、locked_facts 未被"原文+否定词"改写（bigram 相似度 0.75 阈值 + 否定标记表，`domain/outline_revision.py:113`）；
2. 不变量校验失败带反馈重试最多 2 次，耗尽后**最后被拒的输出落库为 invalid 诊断版本**——latest valid 不变；
3. 成功落库后，`OutlineImpactTool`（纯 Python，零 LLM）逐字段比较 11 个大纲字段，产出 `changed_episode_numbers / dependent_script_ids / follow_ups`；
4. 受影响剧本**不动**，影响以 follow-up 建议进入 Outcome，变成一个 `proposed` 子 Action 等用户确认。

**为什么这么克制？** 一次大纲改动可能牵连全部 10 集剧本，自动级联重写意味着：一次性烧掉巨量 token（可能几十次 LLM 调用），且方向错了就是 10 集全毁。对创作工具来说，**"影响可视化 + 用户选择"比"自动正确"更符合信任模型**——作者对"哪集该跟着改"往往有自己的判断。invalid 版本保留而非丢弃也是同理：你能看到"模型到底想改成什么样"，诊断 Prompt 问题时有据可查。

### 4.6 Outcome：确定性证据优先，语义诚实降级

这是最反直觉、也最能体现设计品味的一块。`AgentOutcomeService.evaluate`（`application/agent_outcome_service.py:77`）**当前是零模型调用**的：

- goal_status 由确定性证据推导：Run failed/cancelled → blocked；needs_review → partially_achieved；改了大纲但还有剧本引用旧大纲 → partially_achieved（附 follow_ups）；否则 achieved。
- **逐条语义创作要求一律标 `unverified`**（"缺少可核验的正文证据检查，需要你阅读本轮稿件后判断"），且只要有 unverified，achieved 就降级为 partially_achieved——"语义未验证不得自称完全达成"。

**为什么 W1-04 停用了 LLM OutcomeEvaluator？** 模块注释写得很清楚：在"读正文比对要求"的可复核检查建出来之前，让模型自评"是否达成了用户的要求"就是**让 AI 自己给自己打分**——它会倾向于说达成。宁可诚实地把判断交还用户，也不生产一个看起来可信的幻觉结论。而且权限上做了封顶：即使将来重新启用 Evaluator，它的输出 Schema 也没有 goal_status 字段——模型只能给 `constraint_judgments` 建议，goal_status 永远由服务端合并，"模型不得改变 goal_status，服务端合并时忽略模型的越权输出"（`domain/agent_command.py:301`）。

配套的引文验证 `tools/text_evidence.py` 同样体现这个哲学：`verify_quote` 把归一化（去空白标点+小写）后的引文做子串匹配四态判定——"引用是否真在剧本里只有一种答案"，这是纯确定性函数说了算的。

### 4.7 回写幂等：幕次键 + 崩溃补偿

Run 终态回写 Action 是最容易出重复/遗漏的地方，实现用了"幕次键"：

```
_run_phase = v2:{status}[:{gate}]:g{stage_generation}:a{attempt_count}
```

**为什么需要这么细？** 一个 Action 会跨多幕：停在剧本确认门（回写一次"第一批完成"）→ 用户确认续跑 → 终态（再回写一次）。如果只按 `run_status` 查重，续跑后的新结局会被误判为"已写入"而丢失。幕次键让每一幕都有自己的幂等槽位。配套的崩溃补偿：`get_action` 发现 Action 停在 queued/running 但 Run 已终态 → 自动触发 `reconcile` 补写——**Run 终态是事实源，消息只是它的投影，投影丢了就重放**。

子提案的幂等用 `(parent_action_id, replan_depth)` 唯一约束 + `begin_nested` 捕获 IntegrityError，保证 reconciliation 重放也绝不会创建出第二个后续计划（`replan_depth` 上限 1，§3.2"不建设无人值守的自动循环"）。

## 5. 失败模式：每个环节都预设了崩溃

§8 的边界表 + §10 的生产失败分析是这个设计完备性的最好证明——每条失败路径都有明确的"用户看到什么"：

| 崩溃点 | 系统行为 | 用户看到 |
|---|---|---|
| Planner 超时/输出非法 | Turn → failed + error 消息，不创建 Action | "未能理解本次请求，请重试" |
| 进程在规划中退出 | planning 租约过期，下次请求接管 | 202 规划中，稍后出结果 |
| 确认时来源已更新 | 快照比对失败 → Action stale | "项目内容已更新，请重新生成计划" |
| 两个确认并发 | 部分唯一索引拦截，败者回读 | 一人成功，另一人复用同一 Run |
| Worker 执行中崩溃 | 租约过期被接管，checkpoint 恢复 | 无感，SSE 继续推 |
| Artifact 落库后、消息写入前崩溃 | reconciliation 幂等补写 | 刷新后看到结果 |
| 大纲修订破坏不变量 | invalid 诊断版本，不设 latest valid | "修改未应用 + 违反了哪些不变量" |
| Outcome 证据不足 | 确定性证据保留，标 blocked/unverified | "暂时无法判断目标是否完全达成"，不自动继续 |

注意一个贯穿模式：**每一行的"用户看到"都是一句人话**。系统内部是状态机、租约、唯一约束，翻译到界面全是可执行的下一步指令——这是"工程严谨性"和"产品可用性"的分界线设计。

## 6. 收束：为什么是这样一套设计

把所有"为什么"再往上抽一层，是四个原则：

1. **信任模型：LLM 是不可信的提案者，服务端是唯一执行者。** "选择器 vs 权限"分离、输出黑名单、服务端模板化计划、goal_status 权限封顶，全是同一个原则在不同层的投影。安全性不靠 prompt 求模型，靠结构上让它够不着。

2. **幂等无处不在：每个请求都有持久化收据。** Turn 幂等键、Action 行锁 + run_id 唯一、Run 幂等键、幕次键、收据事件、部分唯一索引——从 HTTP 重试到进程崩溃再到多实例竞争，任何一层的重放都不产生重复副作用。这套系统假定**一切都会失败两次**，然后让失败变得无害。

3. **人在回路，画在不可逆处。** 自动化的边界不是画在"技术上能不能"而是画在"错了贵不贵"：读操作（explain）直接执行、受预算约束；写操作（建 Run/改稿）必须确认；自动循环被禁止（后续计划最多一层，`replan_depth=1`）；大纲级联重写降级为建议。因为 LLM 的错误是**低成本高频率**的，而执行的代价是**高成本**的，确认步骤就是两者之间的保险丝。

4. **诚实降级优于自信幻觉。** 语义验不了就标 unverified、评分证据拿不到就 score_delta=None、澄清 3 轮不成就给命令列表、检索失败返回空上下文继续跑。系统的可信度不来自"永远给答案"，而来自"知道自己哪里没把握"。

还有一个容易被忽略的元决策：**这套东西没有引入任何新基础设施**。§12 评审结论是"沿用现有分层，不新增外部服务"——Turn/Action 是两张新表，执行复用 MVP 的 Dispatcher/checkpoint/SSE。§3.2 明确拒绝了 Multi-Agent、MCP、向量长期记忆。Phase J 的复杂度几乎全部花在**状态机和幂等**上，而不是新轮子上——这让它成为"最复杂但不是最庞大"的一块。

---

# 第三部分：LangGraph 工作流直白拆解

## 0. 先说 LangGraph 在这个项目里到底是什么

直白讲：**LangGraph 就是一个"带断点续传的流程图引擎"**。你把一个长流程拆成一堆节点（每个节点干一件事），画成一张图，图会按顺序执行节点，每走完一步就把当前进度存档。进程崩了，下次从存档处接着跑。

这个项目用它跑的就是那条创作流水线：**需求归一化 → 检索 → StoryBible → 大纲 → 写剧本 → 评估 → 修订 → 定稿**。一次 Run 要跑几分钟、调几十次 LLM，中途随时可能崩、可能被取消、可能要停下来等用户确认——LangGraph 解决的就是"这种长流程怎么跑得稳"。

整个项目一共 5 张图，全部共用同一个 State 类型、同一批节点函数：

| 图 | 节点数 | 干什么 | 入口 |
|---|---|---|---|
| `creation` | 11 | 完整创作主流程 | `create_script` |
| `revision` | 4 | 独立修订（含自动选集 + 循环） | `revise` |
| `conversational_revision` | 5 | 聊天驱动的剧本修订（指定集，不循环） | `revise_script` |
| `outline_revision` | 1 | 聊天驱动的大纲修订 | `revise_outline` |
| `import_file` | 1 | 上传文件解析分类 | `import` |

## 1. 搭建三件套：State、节点、图

### 1.1 State —— 图的"记事本"（`workflows/state.py`）

State 就是一个 TypedDict，定义了整张图共享的记忆。**这是整个工作流层最重要的一个设计决定：只存 ID，不存正文。**

看真实字段（`state.py:12`）就明白它存什么：

```python
class CreationState(TypedDict, total=False):
    # 标识
    run_id: str
    project_id: str
    # Artifact 只存 UUID，不存内容！
    story_bible_artifact_id: str | None      # StoryBible 的 ID
    outline_set_artifact_id: str | None      # 大纲的 ID
    script_artifact_ids: dict[str, str]      # {"1": "uuid...", "2": "uuid..."}
    evaluation_artifact_ids: dict[str, str]  # 评估报告的 ID
    # 流程控制
    revision_round: int                      # 修订到第几轮
    stage_gate: str                          # 停在哪个确认门
    status: str                              # running / failed / needs_user_input
    error_code: str | None                   # 机器可读错误码
    # 重试幂等
    completed_nodes: list[str]               # 已完成的节点名单
```

**为什么死活不把剧本正文塞进 State？** 因为 LangGraph 的 checkpointer **每执行完一个节点，都会把整个 State 序列化后存进数据库**。一份剧本几万字，写 3 集 + 评估 3 次 + 修订 1 轮，一个 Run 要存十几个快照——State 里带正文等于每个快照都复制一遍全部正文，数据库和序列化开销直接爆炸。而恢复的时候，其实只需要知道"做到哪一步了、产物 ID 是什么"，正文按 ID 去 `artifacts` 表查一次就行。所以：**State 记"进度"，Artifact 存"作品"**。

`total=False` 的意思是所有字段都可选——这配合 LangGraph 的核心机制：**每个节点只返回"我改变了什么"的增量 dict，框架负责合并进 State**。比如 `normalize_node` 最后只返回 `{"requirement_artifact_id": "...", "completed_nodes": [...]}`，不用把整个 State 抄一遍。

### 1.2 节点 —— 干活的工人（`workflows/nodes/`）

每个节点就是一个 async 函数：**输入 State，输出增量 dict**。所有节点长一个模子（`normalize.py:32` 是最标准的样本），骨架七步：

```python
async def normalize_node(state) -> dict:
    # ① 拿依赖：不是 import 全局对象，而是从 LangGraph 运行时的
    #    configurable 里取（db 会话、LLM agent、ArtifactService、事件发布器）
    ctx = get_config()["configurable"]

    # ② 取消守卫：用户点了取消？抛 RunCancelledError 立即中断
    raise_if_cancelled(state["run_id"])

    # ③ 失败短路：上游已经失败了，我就不干了
    if state.get("status") == "failed":
        return {}

    # ④ 幂等早退：重试场景下我已经干过了，跳过
    if "normalize" in state.get("completed_nodes", []):
        return {}

    # ⑤ 发事件：告诉前端"我开始了"（autocommit=True 立即提交）
    await publisher.publish(db, run_id=..., event_type="node.started", ...)

    # ⑥ 真正干活：调 Skill（内部调 LLM）→ 结果过 Pydantic 校验 → 存 Artifact
    result = await skill.execute({...})
    artifact = await artifact_svc.create_validated_artifact(db, ...)

    # ⑦ 返回增量：新产出的 ID + 把自己加进 completed_nodes
    return {"requirement_artifact_id": str(artifact.id),
            "completed_nodes": state.get("completed_nodes", []) + ["normalize"]}
```

几个直白的要点：

- **依赖从 `configurable` 注入而不是写死 import**——这样测试时可以塞 FakeLLM、换测试数据库，节点代码一行不改。
- **②③④ 三个守卫的顺序有讲究**：先查取消、再查失败、再查是否已完成——保证"取消 > 失败 > 幂等"的优先级。
- **节点从不抛异常给框架**（除了取消），出错时返回 `node_failure("normalize", e)`（`checkpoint.py:97`），里面带机器可读的 `error_code`。错误是**数据**，不是异常流——这样才能落库、才能被路由函数读取、才能翻译成用户能看的话。
- **每个节点都被 `timed_node()` 包一层**（`node_timing.py`）：记耗时指标（Prometheus 的 `workflow_node_duration_seconds`）、把节点名压进日志 tracing 上下文。只包计时不改语义。

**`write_episodes` 是最特殊的节点**（`write_episode.py:42`）——一个节点内部 for 循环写 3 集剧本。为什么不拆成 3 个节点？因为**集数是运行时才知道的**（用户可选 1/2/3/5/10），而图的结构必须静态定义。所以取"图静态、循环动态"的折中，并靠两个机制补上断点能力：

- 每集写完**立即落库 + 立即发事件**（前端能逐集看到"第 1 集完成"）；
- 循环开头检查 `existing_scripts`（State 里已有的集直接跳过），循环内**每集写入前再查一次取消**。中途崩了，已写的集都在库里，重试时从断掉的集继续。

### 1.3 图 —— 把节点串成流程（`workflows/creation.py`）

图的搭建就是三种原语的堆叠，`creation.py:109` 一共 60 行：

```python
builder = StateGraph(CreationState)

builder.add_node("normalize", timed_node("normalize", normalize_node))  # 注册节点
builder.set_entry_point("normalize")                                     # 入口

builder.add_edge("retrieve", "story_bible")            # 无条件边：A 完了必到 B
builder.add_conditional_edges(                          # 条件边：A 完了看情况
    "normalize",
    _should_continue_after_normalize,                   # 路由函数：读 State 返回节点名
    {"retrieve": "retrieve", "__end__": END},
)

return builder.compile(checkpointer=checkpointer)       # 编译，注入存档器
```

**路由函数全是普通的 if-else 纯函数**（不调 LLM！），比如评估完之后去哪（`creation.py:89`）：

```python
def _should_route_after_eval(state):
    if state.get("stage_gate") == "scripts":     # 剧本分批门 → 停车等用户
        return "__end__"
    if state.get("needs_revision_decision"):     # 有集不合格 → 进修订
        return "select_revision"
    if state.get("status") == "failed":
        return "__end__"
    return "finalize"                            # 全过关 → 收尾
```

**为什么要强调"路由不调 LLM"？** 因为流程走向是**控制流**，让 LLM 决定"下一步干嘛"等于把方向盘交给一个会幻觉的东西。这个项目里 LLM 只出现在节点**内部**（生成内容），节点的**流转**全部是确定性代码——这才是"每次跑流程结果可预期"的根本。

## 2. 主图完整流程：11 个节点各干什么

```
normalize ──► retrieve ──► story_bible ──► outline ──► write_episodes
 需求归一化     RAG三阶段     世界观圣经      10集大纲     逐集写剧本
 →需求Artifact  检索参考素材   →SB Artifact   →大纲Artifact  →剧本Artifacts
    │                                                        │
    │缺关键信息/失败→END                        stop_after=outline→END(大纲门)
    ▼                                                        ▼
                                             evaluate_episodes 逐集评估
                                                          │
                        ┌─────────────────────────────────┤
                        │ stage_gate=scripts → END(剧本门)  │
                        ▼                                  ▼
                    finalize ◄──────────── select_revision 纯函数选最低分集
                    更新集数、发completed事件      │
                                                revise 生成修订计划+完整新稿
                                                  │
                                            continuity_check 连续性检查
                                                  │ pass
                                             re_evaluate 重评新稿
                                                  │
                                    ┌─────────────┼─────────────────┐
                              还有低分且未满轮   全部通过          满轮仍低分/掉分>5
                                    │             │                 │
                              回 select_revision  finalize         END(转人工复核)
```

修订循环有两个容易被忽略的设计：

- **选集是纯函数**：`select_revision_node` 里就一行核心逻辑——在所有不合格的集里取 `(分数最低, 集号最小)`，零 LLM；
- **失败也留痕**：连续性检查没过的新稿**不会被扔掉**，而是保留为 invalid 状态的诊断版本——你能看到"模型这次想改成什么样"才好调 Prompt。

## 3. 核心点：这块真正的技术含量

### 核心点 ①：双层断点恢复——"崩了不重跑、不重花钱"

这是最核心的机制，**两层叠加**：

**第一层：LangGraph checkpointer**（`workflows/persistence.py`）。编译图时注入 `AsyncPostgresSaver`，执行时 `configurable` 里带 `thread_id=run_id`。效果：**每执行完一个节点，State 快照自动存进 PG 的 checkpoints 表**。重试时用同一个 `thread_id` 恢复，框架自动从"最后完成的节点之后"继续——已完成的节点连函数都不用调。

**第二层：逻辑级幂等兜底**（`checkpoint.py` 的 `save_checkpoint` 把最终 State 写进 `workflow_run.state_summary`，重试时 Dispatcher 把它合并回初始 State）。因为光有框架级恢复还不够稳，节点里还有三道自己的保险：

- `completed_nodes` 早退（④）；
- Artifact 的 `input_hash` 幂等（同输入重复创建直接返回已有记录，不会生成第二个版本）；
- `write_episodes` 的 `existing_scripts` 跳过 + 失败时返回**已完成的集**（`write_episode.py:246`——异常路径也把 `completed_scripts` 带回去，已完成的工作不丢）。

直白说：**checkpointer 管"从哪继续"，completed_nodes + input_hash 管"继续时不重复花钱"**。一次 Run 可能已经烧了 15 次 LLM 调用后崩在第 16 次，重试时前 15 次的成果分毫不动。

### 核心点 ②：每个节点都在"直播"——事件即进度

每个节点的开始/完成/失败、每个 Artifact 的诞生，都会 `publisher.publish(...)` 发事件：先 INSERT 进 `workflow_events` 表（永久可回放），再推 Redis，SSE 推给前端。这就是前端进度条"第 2 集剧本已完成（45%）"的数据源。

有个细节最能体现工程严谨：**所有事件发布都带 `autocommit=True`**。因为节点跑在一个跨越整个 Run 的长数据库会话里，如果事件跟着大事务走，要么前端看不到实时进度，要么会话关闭时事件跟着回滚。`autocommit=True` 让事件"发布即持久"——**事件是承诺，必须先落袋**。

### 核心点 ③：图会"中途停车等用户"——END 不一定是结束

注意主图里有两种 END，语义完全不同：

- **真结束**：全部通过 → finalize → Run completed；
- **停车等确认**：`stop_after=outline` 停在大纲门、`stage_gate=scripts` 停在剧本分批门、修订满轮仍低分停在人工复核——这些 END 之后 Run 转 `needs_review`，**State 完整保存在 checkpoint 里**。用户确认后（前端带 `expected_stage_generation` 调 continue 接口），Dispatcher 清掉门字段和上一批的完成标记，重新入队，**从 checkpoint 接着跑下一批**。

这就是"10 集分批写、每批确认"和"先看大纲再决定写不写剧本"的实现基础——**长流程被切成了"执行段 + 人审段"的交替**，而图本身不用改。

### 核心点 ④：协作式取消——一个继承 `BaseException` 的例外

取消的实现（`checkpoint.py:35`）就三样东西：一个模块级字典 `_cancel_registry`（run_id → True）、API 层取消端点往里写标记、每个节点入口（以及逐集循环内）调 `raise_if_cancelled` 查标记。

真正的巧思在异常类型上：`RunCancelledError` **故意继承 `BaseException` 而不是 `Exception`**。为什么？因为节点里到处是 `except Exception`（第⑦步的失败兜底），如果取消错误也继承 Exception，就会被这些兜底捕获、当成普通失败处理——**取消之后可能还会多写一个 Artifact**。继承 BaseException 后它穿透所有 `except Exception`，一路炸到 Worker 顶层，被标记为 cancelled。一行继承选择，保住"取消即止"的语义。

### 核心点 ⑤：四张图共用一套零件

`revise / continuity_check / re_evaluate` 三个节点同时出现在 creation、revision、conversational_revision 三张图里，连路由函数 `_should_route_after_continuity` 都是直接 import 复用的（`creation.py:36` 还特意注释了"复用避免环形 import"）。好处直白：**修订逻辑只写一遍，三条路径（自动修订/独立修订/聊天修订）行为完全一致**，测试也只需覆盖一份。

但共用不等于无脑相同，各图有刻意的差异，最典型的是对话式修订图（`conversational_revision.py`）：

- 目集由服务端解析（聊天确认时已做快照过期检测），**不进** `select_revision` 自动选集；
- 目标集缺评估时先补一次评估（`ensure_evaluation`），不重新评估全部；
- **重评后直接 END，不进自动修订循环**——"用户让你改一次就改一次，改完不好再来一轮对话"，控制权留给用户。

而独立修订图反而比主图多一个条件边（`revision.py:41`："无候选集时直接 END 避免空转"），注释还特意说明**主图的 select_revision 不加这个边**——因为主图只在"已判定需修订"时才可达该节点，加了反而有把 Run 卡死的风险。同一节点、两张图、两种接法，每处不对称都写了为什么。

### 核心点 ⑥：一个诚实的已知限制

`revision.py:13` 的注释值得单独讲：

> `completed_nodes` 是扁平列表，同一节点合法二次访问无法表达——MAX_REVISION_ROUNDS=1 下成立；未来 MAX>1 需引入"轮次键名"。

修订循环会第二次走到 `select_revision`，但 `completed_nodes` 是个列表，"select_revision 第 1 轮完成"和"第 2 轮完成"表达不出来——所以断点恢复的幂等保证只在修订轮数 = 1 时严格成立。团队没有掩盖这个限制，而是把它写进代码注释、把配置锁死在 1。**知道自己的机制在哪里失效，并把它圈起来**，这本身就是这个项目的典型风格。

## 4. 一句话总结

LangGraph 在这个项目里**不是"多 Agent 编排框架"，而是"带持久化的长事务引擎"**：State 是事务的进度账本（只记账不存货），节点是带七步防护的工人（守卫→直播→干活→记账），图是确定性画好的流程（LLM 只在节点里干活，永远不摸方向盘），checkpointer + 幂等双保险保证崩几次都不重复烧钱，停车门把长流程切成"机器跑一段、人确认一段"的交替。

图的外面还有一圈完整配套：**Dispatcher 决定"谁来做、什么时候做"**（数据库领任务 + 租约），**Skill 决定"具体怎么干"**（Prompt + LLM + Pydantic 校验），**Artifact 决定"成果存哪"**（不可变版本）。理解了这圈分工，整个后端就通了。

---

# 第四部分：异步执行引擎——Run 调度 + SSE

## 0. 先弄清楚要解决什么问题

这个系统里最重的操作是"创作"：一次完整创作要生成需求、StoryBible、10 集大纲、3 集剧本、逐集评估、可能还有修订——总共要调用 LLM 几十次，耗时 5 到 20 分钟。

这带来四个用普通 HTTP 接口无法解决的问题：

1. HTTP 请求不能挂 20 分钟等结果（浏览器和网关都会超时断开）；
2. 执行到一半服务进程重启、崩溃，任务不能凭空消失；
3. 前端用户不能盯着转圈 20 分钟，需要实时看到"第 2 集剧本写完了"这样的进度；
4. LLM 调用花钱，崩溃后重试不能把已经生成好的部分重新算一遍。

这个引擎的全部设计就是回答这四个问题。核心思路只有一句话：**把"任务"本身存进数据库，HTTP 只负责登记任务，真正的执行由后台 Worker 从数据库里读任务、执行、更新状态；执行过程中产生的每个事件也写进数据库，前端通过 SSE 长连接实时读取这些事件。**

## 1. 地基：`workflow_run` 表和它的状态机

整个引擎围绕一张表转。`workflow_run` 的关键字段（`db/models/workflow_run.py`）：

| 字段 | 作用 |
|---|---|
| `status` | 当前状态：queued / running / completed / failed / cancelled / needs_review |
| `action` | 要执行什么：create_script / revise_outline / export… |
| `config_snapshot` | 任务参数快照（用户输入、集数等） |
| `idempotency_key` + `request_hash` | 请求去重用 |
| `lease_owner` + `lease_expires_at` | 记录哪个 Worker 正在执行、执行权什么时候到期 |
| `attempt_count` | 已经被执行过几次（限制无限重试） |
| `state_summary` | 上次执行到哪了的 JSON 快照（配合断点恢复） |

状态之间的转换不是随便改的，`run_service.py:39` 有一张白名单表：

```python
_VALID_TRANSITIONS = {
    "queued":       {"running", "cancelled"},
    "running":      {"completed", "failed", "needs_review", "cancelled"},
    "completed":    set(),                # 终态，不可再变
    "failed":       {"queued"},           # 只允许 retry 回队列
    "cancelled":    set(),
    "needs_review": {"queued"},           # 人工确认后可以继续跑
}
```

任何不在这张表里的状态变化都会被拒绝（409 `INVALID_TRANSITION`）。这张表是引擎的规则核心——后面所有环节（领取、取消、重试、确认续跑）本质上都在做合法的状态转换。

## 2. 第一步：API 接收请求，登记任务

用户在前端点"开始创作"，前端发 `POST /projects/{id}/runs`（`api/v1/runs.py:187`）。这个接口**不做任何创作工作**，只做登记，按顺序过五道检查（`run_service.py:81`）：

**第一道：请求去重。** 前端每次请求带一个随机生成的 `idempotency_key`。服务端先查这个 key 是否已经用过——用过的 key 且请求参数一样（参数算出 SHA256 指纹 `request_hash` 比对），直接返回之前那个 Run，不会创建重复任务；key 用过但参数不一样，返回 409 错误（说明前端代码有 bug 在复用 key）。这样**用户连点两次"开始创作"、或网络超时后前端自动重发，都只会产生一个任务**。

**第二道：校验项目存在，且没被删除。**

**第三道：单项目单活跃任务检查。** 查询该项目有没有 `status IN (queued, running)` 的 Run，有就返回 409 "项目已有活跃 Run"。原因：创作是顺序流程（先大纲后剧本），两个任务并行会互相覆盖产物。注意这只是一道**应用层预检查**，真正的兜底在数据库：`workflow_run` 表上有一个部分唯一索引，`project_id` 在 status 为 queued/running 时必须唯一（`db/models/workflow_run.py:40`）。两个请求同时通过预检查、同时 INSERT，数据库层面必定有一个失败——失败的请求会转为 409 返回（`run_service.py:162-180` 专门捕获这个冲突再查一次）。**为什么预检查之外还要数据库约束？因为应用层检查和写入之间有时间差，两个并发请求可以都通过检查然后都写入；数据库约束没有时间差，它是并发的最终裁判。**

**第四道：插入一条 `status="queued"` 的记录。** 此刻任务只是被登记了，没有任何东西开始执行。

**第五道：提交数据库事务，然后尝试"叫醒"Worker。** 注意 `runs.py:225-226` 的顺序和注释：

```python
await db.commit()          # 先提交——任务已确定落库
schedule_worker(...)       # 再叫醒——best-effort，失败也不影响
```

然后接口返回 **HTTP 202**（含义是"已接受，尚未完成"）加一个 `run_id`。整个接口耗时毫秒级。

到这里 API 的工作就结束了。前端拿到 `run_id` 之后做的第一件事：向 `GET /runs/{run_id}/events` 发起 SSE 连接（下一节讲），之后就靠这条连接看进度。

## 3. 第二步：Worker 从数据库领取任务

每个后端进程启动时都会创建一个 `WorkflowDispatcher`（`workflow_dispatcher.py:41`），它有一个身份标识 `owner`（主机名 + 随机串），核心方法 `claim_next()`（`:61`）做一件事：**从数据库里找一个可以执行的任务，并声明"现在归我执行"。**

找任务的 SQL 条件是：`status = "queued"`，**或者** `status = "running"` 但 `lease_expires_at` 已经过期。第二个条件初看奇怪，它就是**崩溃恢复**的机制：

- Worker 领到任务后写 `lease_owner = 自己`、`lease_expires_at = 现在 + 30 秒`。这叫**租约（lease）**：数据库记录"这个任务由我在执行，我的执行授权 30 秒后到期"。
- Worker 执行期间每 10 秒（租约的三分之一，`_heartbeat`，`:174`）更新一次到期时间，只要它活着，租约就一直续。
- 如果 Worker 进程崩溃了，没人续租，30 秒后 `lease_expires_at` 变成过去时——这个 running 状态的 Run 就重新满足领取条件，**下一个扫数据库的 Worker（可能是另一个进程）会把它领走**。这就是为什么"running 但租约过期"也要领取：它是一个死掉的任务遗留物，需要有人接手。

还有一个并发问题：如果两个 Worker 进程**同一瞬间**执行 `claim_next`，怎么保证它们不会领到同一个任务？答案是 SQL 里的 `FOR UPDATE SKIP LOCKED`（`:79, :105`）。这是 PostgreSQL 的行级锁语法，含义是：多个 Worker 同时扫描同一批候选任务时，**每个任务行只会被其中一个 Worker 锁定，其他 Worker 直接跳过这行、去看下一个任务**。不需要任何分布式锁组件，数据库自己保证了"一个任务同一时刻只被一个 Worker 领取"。

领取动作本身是四次字段更新（`:112-115`）：`status: queued → running`、写 `lease_owner`、写 `lease_expires_at`、`attempt_count += 1`。其中 `attempt_count` 有对应的上限逻辑：领取时发现某任务 `attempt_count >= 3`，不再执行它，直接标记 `failed`，错误码 `WORKFLOW_RECOVERY_EXHAUSTED`（`:77-99`）。这个上限防止一种最坏情况：某个任务有必现的崩溃 bug，如果没有上限，它会"崩溃 → 租约过期 → 被领取 → 再崩溃"无限循环下去。

## 4. 第三步：执行任务

领到任务后，Worker 做三件事（`run_once`，`:181`）：

1. 启动一个心跳协程（就是上面说的每 10 秒续租）；
2. 调用 `_execute_workflow`（`:327`，整个文件 1227 行的主体）；
3. 任务结束后停掉心跳。

`_execute_workflow` 的执行流程，按顺序：

1. **验证执行权**：重新读这条 Run，确认它还是 `running` 且 `lease_owner` 是自己。防止一种竞态：Worker A 假死很久，租约被 Worker B 接管，A 又缓过来了——A 必须在这里发现"任务已经不是我的了"然后退出。
2. **注册 LLM 预算**：`enter_run(run_id, 软上限18次, 硬上限24次, 硬上限20万token)`。之后这个 Run 里每一次 LLM 调用都会被计数，超过硬上限直接抛错终止——这是防止失控循环烧钱的闸门。
3. **读恢复基底**：从 `run.state_summary` 读出上次执行保存的状态（做到哪个节点了、产出了哪些 Artifact ID）。如果是首次执行这就是空的。
4. **选图并执行**：按 `action` 选对应的 LangGraph 工作流（create_script 选 creation 图，revise_script 选对话式修订图……），然后 `workflow.ainvoke(state, config)` 开始跑。`config` 里带 `thread_id = run_id`——这就是上一讲说的 LangGraph checkpointer 的存档钥匙，每个节点跑完自动存档到数据库。
5. **根据最终 State 判定终态**：工作流跑完后，代码检查 State 里的标志字段——有 `error_node` 就转 `failed`；有 `needs_user_input` 转 failed（带缺失字段）；停在大纲门/剧本门转 `needs_review`；连续性检查没过转 `needs_review`；正常走完转 `completed`。每次转换都走第 1 节那张状态机白名单表，并发对应事件。
6. **收尾**：把 LLM 调用统计发成 `run.llm_stats` 事件（诊断接口靠它统计每次任务花了多少次调用、多少 token）、清理预算登记和取消标记。

这里要明确一个架构事实：**API 进程和 Worker 在同一个 Python 进程里**（`get_dispatcher` 是进程级单例），但这只是部署上的省事，不是架构依赖。因为任务的存在、归属、进度全部记录在数据库，把 Worker 拆成独立进程部署不需要改任何一行业务代码（DESIGN.md §12 明确写了这是刻意设计）。

## 5. "数据库是执行事实源"是什么意思

`runs.py:226` 的 `schedule_worker()` 函数体是这样的（`workflow_dispatcher.py:277`）：

```python
def schedule_worker(run_id, action, config_snapshot) -> None:
    del run_id, action, config_snapshot    # 参数直接删掉，根本不用
    get_dispatcher().wake()                # 只是在本进程里启动一个扫描循环
```

参数被 `del` 掉是刻意写出来的：**这个调用不传递任何任务信息**。`wake()` 做的事只是在本进程的事件循环里创建一个 asyncio 任务，循环调用 `claim_next()` 扫数据库，扫到任务就执行，执行完继续扫，扫不到就停（`_drain`，`:231`）。

这个设计成立的原因是：**就算 `wake()` 一次都没被调用，任务也不会丢**。因为任务的真实状态在数据库里，只要任何一个地方发生扫描——本进程启动时 `startup_dispatcher()` 会扫一次、本进程处理完任何一个任务后的 drain 循环会再扫、任何一次 API 请求触发的 `wake()` 会扫、部署了多个实例时别的实例会扫——排队的任务迟早被捡起来。`wake()` 唯一的作用是把"迟早"缩短成"立刻"。

对比一下反面设计就能理解为什么这样做：如果 API 直接把任务塞给某个 Worker 进程的内存队列，那么 Worker 崩溃时内存里的任务就丢了、重启后 API 和 Worker 必须重新对账、多实例部署时还得引入任务分配机制。把数据库放在中间，这三个问题都不存在。

## 6. 第四步：执行过程中产生事件

任务在执行时，几乎每一步都在产生"事件"（event）：`run.created`（任务已登记）、`run.running`（开始执行）、每个节点的 `node.started` / `node.completed`、每个 Artifact 产出的 `artifact.created`（"第 2 集剧本已完成"）、失败的 `node.failed` / `run.failed`、结束的 `run.completed`。

`EventPublisher.publish()`（`events/publisher.py:80`）每次被调用时做两件事：

**第一件：写入 PostgreSQL 的 `workflow_events` 表。** 每个事件带一个 `sequence` 序号——分配方式是先 `SELECT MAX(sequence) WHERE run_id = ... FOR UPDATE` 再加 1（`:142-149`），行锁保证同一个 Run 的并发事件序号绝不重复。这个序号是整个 SSE 体系的坐标系统：历史事件靠它排序，断线补发靠它定位，"从哪继续"靠它判断。**事件一旦写入就永久保存**，SSE 断了、进程重启了、甚至第二天再来查，事件都在。

这里有一个必须理解的细节——`autocommit=True` 参数（`:96-101` 的注释用了全大写强调）：工作流节点运行在一个跨越整个任务的**长数据库会话**里，如果事件 INSERT 跟着这个长事务一起提交，那么在事务提交前（可能几分钟后）外部查询根本看不到这些事件，而且会话异常关闭时全部回滚。所以工作流节点发事件必须传 `autocommit=True`：插入后立即 `commit`，然后立刻开一个新事务继续干活。**事件是给外人看的进度记录，必须先于工作结果落库。**

**第二件：往 Redis 发布一条消息**（`:162`），频道名 `run:{run_id}`，内容就是这条事件。注意注释里的 "best effort"：整个调用包在 try/except 里，**失败直接忽略**（`:171-172`）。Redis 在这里只是一个"让实时推送更快"的加速器，不是必需品——原因见下一节。

## 7. 第五步：SSE 把事件推给前端

先解释 SSE 本身：**Server-Sent Events 是一种 HTTP 长连接**。前端用浏览器的 `EventSource` 对象向服务端发一个普通 GET 请求，服务端不关闭连接，持续往响应体里写文本行，浏览器实时收到。它自带两个特性：连接断了浏览器**自动重连**；重连时浏览器会自动带上一个 `Last-Event-ID` 请求头，告诉服务端"我最后收到的事件是哪条"。

SSE 端点在 `events/stream.py:141`。它的实现分三个阶段：

**阶段一：补发历史（`:44-50`）。** 连接建立后，先拿请求里的 `Last-Event-ID`（或 URL 参数）去 `workflow_events` 表查这个事件之后的所有事件，按 sequence 升序全部发给客户端。这一步保证了：**用户刷新页面、网络断了几秒、甚至 Run 都跑完了才打开页面，都能按正确顺序收到完整的事件历史**。比如用户在"第 1 集完成"之后断网 30 秒，重连时浏览器带上 `Last-Event-ID`，服务端把断网期间的 5 条事件补齐，前端的进度条从"第 1 集"无缝跳到最新状态，中间不丢不重。

**阶段二：实时推送（`:53-124`）。** 服务端并发跑两个后台任务，把新事件塞进同一个队列：

- **Redis 监听器**（`:59`）：订阅 `run:{run_id}` 频道，Worker 每发布一条事件，这里毫秒级收到；
- **数据库轮询器**（`:74`）：每隔 `sse_heartbeat_seconds` 查一次数据库，条件是 `sequence > 上次已见的最大序号`，把新事件也塞进队列。

注意 `stream.py:110` 这行注释："**始终运行 DB 轮询作为回退**"——轮询不是 Redis 挂了才启动的备胎，而是和 Redis 监听并存、一直在跑的。这样设计是刻意的冗余：Redis 正常时，轮询器查出来的事件大多已经在队列里发过了（队列消费端靠事件 ID 去重），白白多查了几次数据库；但 Redis 挂掉、清空、或者消息丢失时，前端最多延迟一个心跳周期就还是能收到全部事件。**用一点数据库轮询开销，换"消息通道零依赖"**。

主循环（`:113-118`）从队列取事件写给客户端；如果一个心跳周期内没有任何事件，就写一行 `: heartbeat`——这是 SSE 协议里的注释行，浏览器不当作事件，但中间的代理和网关会认为连接有流量，不会掐掉空闲连接。

**阶段三：结束信号（`:126-138`）。** 当前端检测到 `run.completed` / `run.failed` 等终态事件后会主动关连接；服务端这边在流结束时也会查一次 Run 状态，如果已终态就补发一个命名事件 `run_ended`，前端收到后确保关闭 `EventSource`，不留死连接。SSE 连接数还被记成 Prometheus 指标 `sse_connections_active`（`:156`）。

## 8. 把各环节串起来看：每个部件坏了会怎样

这套设计的正确性，最终体现在"任何单个部件失效，系统行为都明确"：

| 失效场景 | 系统行为 | 用户感知 |
|---|---|---|
| API 在 `db.commit()` 后、`wake()` 前崩溃 | 任务已在数据库里是 queued，启动扫描或任意一次扫描会捡起它 | 无感，只是开始得稍晚 |
| Worker 执行到第 10 个节点时进程崩溃 | 已完成节点存进了 checkpoint；租约 30 秒后过期，任务被重新领取，从断点继续，已完成的 LLM 调用不重复 | SSE 短暂停顿后继续推进 |
| Worker 假死（没崩但卡住了） | 同上，租约过期后被其他实例接管；旧 Worker 恢复后想写终态，会被 `lease_owner` 校验拒绝（`WORKFLOW_LEASE_LOST`，`run_service.py:216`） | 无感 |
| 任务反复崩溃 | `attempt_count` 到 3 后标 `WORKFLOW_RECOVERY_EXHAUSTED` 失败 | 看到"重试已用尽"的明确失败 |
| Redis 整个挂掉 | 事件照常写 PostgreSQL；SSE 的 DB 轮询兜底继续推送 | 进度更新从毫秒级变成秒级 |
| 前端断网 30 秒后恢复 | 浏览器自动重连并带 `Last-Event-ID`，服务端从事件表补发缺口 | 进度条无缝续上 |
| 用户连点两次"开始创作" | 幂等键 + `request_hash` 去重，只有一个 Run | 看到一个任务 |
| 两个用户同时给同一项目发起创作 | 部分唯一索引保证数据库层必有一个失败，转为 409 | 一方看到"项目已有任务运行中" |
| 用户点取消 | queued 的直接转 cancelled；running 的设内存标记，工作流在下一个节点入口中断，已产出的 Artifact 保留 | 看到任务已取消，之前生成的部分还在 |

## 9. 总结：这套引擎的三个设计决定

回过头看，全部机制建立在三个决定上：

1. **任务状态放数据库，不放内存。** API 记任务、Worker 领任务、租约管执行权、事件表记进度——全部是数据库行。内存里的东西（进程内的 asyncio 任务、取消标记）只承担加速和通知职责，任何内存数据丢失都不影响正确性。这直接换来了：进程随便崩、实例随便加、部署随便重启。

2. **"请求-响应"和"执行-推送"彻底分离。** HTTP 请求只负责创建任务（202 即返回，毫秒级）；执行结果通过 SSE 长连接推送。两者之间的唯一纽带是 `run_id`。这直接换来了：前端不需要轮询、后端不需要为长任务维持请求上下文、超时问题根本不存在。

3. **每条消息有序号，每条消息先落库。** 事件的 `sequence` 让"补发历史""断点续传""轮询去重"都变成一次带 `WHERE sequence > N` 的普通查询。Redis 只做实时加速，丢了也不影响顺序和完整性。这直接换来了：消息通道可以坏，信息不丢。

---

# 第五部分：评估与自动修订

## 0. 这个环节解决什么问题

前面的环节把剧本写出来了，但 LLM 写出来的东西质量不稳定——可能钩子无力、可能冲突平淡、可能违反了前面几集埋的设定。这个环节要回答三个问题：

1. **写得怎么样**——逐集打分，标准明确、可解释；
2. **哪一集最差**——从不合格的集里挑出最该修的；
3. **怎么改、以及改坏了怎么办**——修订有依据，改完必须验证，验证不通过不允许它悄悄上线。

对应到工作流里就是四个节点：`evaluate_episodes`（评估）→ `select_revision`（选集）→ `revise`（修订）→ `continuity_check`（检查）→ `re_evaluate`（重评），后面跟着路由函数决定继续循环还是结束。

## 1. 评估：怎么给一集剧本打分

### 1.1 评分标准是一份 YAML 数据文件，不是一段提示词

评估标准（rubric）存在 `knowledge/rubric/mvp_v2.yaml`，定义了 **9 个维度**，每个维度有权重、5 档锚点描述、可观察信号：

| 维度 | 权重 | 考察什么 |
|---|---|---|
| opening_hook 开头钩子 | 0.15 | 开场是否在前 1/3 建立悬念或冲突 |
| conflict_intensity 冲突强度 | 0.15 | 冲突是否尖锐、有升级、有具体代价 |
| payoff_density 爽点密度 | 0.15 | 高光/反转/情绪释放点的数量与分布 |
| ending_hook 结尾钩子 | 0.15 | 结尾是否留下驱动追看下一集的悬念 |
| main_clarity 主线清晰度 | 0.10 | 本集目标/障碍/推进逻辑是否完整 |
| character_appeal 角色吸引力 | 0.10 | 角色是否有辨识度、动机是否可信 |
| pacing 节奏 | 0.10 | 有无无效场景和重复对白 |
| visualizability 可视化程度 | 0.05 | 能否直接拍摄 |
| compliance_safety 合规安全 | 0.05 | 内容是否合规 |

每个维度配两样东西。**第一是 5 档锚点**——每个档位有一句文字定义，例如"开头钩子"的 4 档是"开场在前 1/3 内建立明确冲突或悬念，观众有清晰观看动机"，5 档是"开场即抛出强悬念/尖锐冲突"。**第二是可观察信号**——提示模型去看剧本里可核对的具体事实，比如"危机事件出现在第几场第几句""是否有打脸/身份揭晓等明确爽点类型"。

另外定义了**分带映射**：1 档 = 0–44 分，2 档 = 45–59，3 档 = 60–74，4 档 = 75–89，5 档 = 90–100。这个映射后面有重要作用。

为什么把标准做成数据文件而不是写在提示词里？三个原因：**标准有版本号**（每份评估报告记录 `rubric_version`，将来标准改了，历史报告仍然知道自己按哪个版本评的）；**启动时可校验**（程序加载时强制检查 9 个维度齐全、权重之和等于 1、5 档锚点都有文字、分带无缝覆盖 0–100，缺任何一样直接拒绝启动）；**调整标准不动代码**。

### 1.2 评估怎么跑：模型的任务被限定得很窄

`evaluate_episodes` 节点对每一集剧本执行一次评估。输入有四样：这一集的剧本、这一集的大纲、StoryBible、以及**客观特征**——场景数、对白占比这类数字由确定性工具先算好传入，不用模型去数。

模型要做的事只有一个：**对 9 个维度中的每一个，对照锚点描述判断这一集落在哪一档（1–5），给出档内分数，写明定位理由（必须解释为什么不是上一档、为什么不是下一档），并引用剧本原文作为证据。**

注意模型**不给总分、不判断要不要修订**。这两件事归服务端。

### 1.3 服务端回填：模型输出里哪些部分直接被覆盖

模型返回报告后，`skills/evaluator.py` 有一段"服务端回填"代码，按确定性规则改写报告。这是整个评估环节最核心的设计，共六条：

**① 总分由服务端计算。** `compute_overall_score`（`domain/evaluation.py:194`）就是加权平均：每个维度分乘以权重再求和，保留一位小数。模型输出的总分字段即使存在也不会被采用。为什么？大量实践表明 LLM 自报总分系统性地偏高，而"9 个维度分加权求和"是一个任何人都能复查的算式——**分数从哪来，答案是公开的**。

**② 要不要修订由三条固定规则判定。** `compute_need_revision`（`domain/evaluation.py:215`）：总分低于 75，或者存在问题列表里任何一条严重级别为 high 的问题，或者合规安全维度低于 60——满足任何一条，`need_revision = true`。没有模型参与，同样的输入永远得到同样的结论。

**③ 分数和档位必须自洽，冲突时以档位为准。** 模型可能说"这个维度是 3 档（基本可用）"然后打 80 分——80 属于 4 档的分带，自相矛盾。处理方式是 `clamp_score_to_band`：把 80 拉回 3 档的 60–74 区间内（取 74）。理由写在注释里：档位是模型读了锚点描述后的语义判断，分数只是数值表达，**两者冲突时相信"对照文字描述做出的定位"，因为每个分数都能从"档位 + 分带"这条公开规则推导出来**。

**④ 档位描述文本用 Rubric 原文回填。** 报告里"命中的锚点"字段不采用模型的转述，直接用 YAML 文件里那一档的原文（`evaluator.py:226`）。前端展示给用户的锚点描述和评估标准严格一致。

**⑤ 证据引用逐条溯源。** 模型声称"第 3 场这句台词说明钩子无力"，服务端把这句引用做归一化处理（去掉空白和标点后）和剧本原文做匹配：在声称的场次匹配到就通过；场次写错了但别的场次能匹配到，自动纠正场次号；全文都找不到，标记 `verified=False`，前端按"未验证引用"样式显示。校验失败不阻断流程，但**每条证据的可信状态是明确的**。

**⑥ 质量门禁兜底补漏。** 模型可能漏报问题，所以代码补两条规则：任何维度分低于 70 而报告里没有该维度的问题条目，自动补一条；模型上报的信号里如果写着"大纲声明 8 项关键内容只兑现了 3 项"，但问题列表里没有对应条目，自动补一条大纲缺口问题（`evaluator.py:289`）。

这六条合起来的分工原则是：**模型做它可靠的事（把文本和文字标准对照、找证据、给诊断），代码做它可靠的事（算术、阈值比较、格式校验、溯源匹配）。** 每一个最终写进数据库的数字和结论，要么是代码算的，要么是经过代码校验的。

## 2. 自动修订：五个节点串成的闭环

评估发现有集不合格后，进入修订分支。这一段的目标可以概括成一句话：**改一轮，改完必须证明没有改坏，证明不了就停下来交给人。**

### 2.1 选哪一集修：一个纯函数

`select_revision_candidate`（`domain/revision.py:191`）的全部逻辑：

```python
candidates = [r for r in reports if r.need_revision]
return min(candidates, key=lambda r: (r.overall_score, r.episode_number))
```

从不合格的集里取总分最低的；分数相同取集号小的。没有 LLM，没有策略讨论。选中的集号写进 State（`revision_candidate_episode`），`revision_round` 加一。

为什么要强调"不调用 LLM"？因为这是流程走向的分叉点——同样的评估结果必须永远得到同样的选择。如果让模型来挑，就出现了"这次修第 2 集、同样情况下次修第 3 集"的不可复现行为，测试写不了，用户也无法理解"为什么修它"。

### 2.2 修订计划：每个动作必须能回答"你为什么要改这里"

修订计划是一个操作列表，每个操作包含：目标场次、修改指令、预期效果、**以及它所依据的评估问题编号（issue_id）**。

模型可以生成这个计划，但生成后要过一道过滤：`filter_grounded_operations`（`domain/revision.py:249`）。规则只有一条：**操作引用的 issue_id 必须全部来自这集评估报告里真实存在的问题**。引用了报告里不存在的编号（模型编造依据）、或者一个依据都没引用的操作，直接剔除。

如果所有操作都被剔光了，还有一个备用方案：`operations_from_issues` 纯函数直接把评估报告的每条问题机械地翻译成操作——指令就是问题里的 `suggestion` 字段。也就是说，**修订计划最差的情况也只是"逐条执行评估建议"，永远不会出现无依据的修改指令**。

这个设计解决的问题很具体：不加约束时，LLM 生成的修订任务经常是"优化整体节奏、增强戏剧张力"这类无法执行也无法验证的空话。绑定 issue_id 之后，每个操作都能回答"改哪里、为什么改、改完预期什么变化"，而且事后可以对照检查"声称要改的问题到底改了没有"。

### 2.3 生成新稿：写一份全新的，存成一个新版本

修订模型收到原稿、修订计划、StoryBible 和锁定事实（不可修改的内容清单），输出一份**完整的新稿**——不是差异补丁。新稿落库为该集 `script_draft` 的一个新版本，状态 `draft`，溯源链接记录它"修订自哪份原稿、依据哪份计划"。

落库用 draft 状态而不是直接生效，是因为它还没通过检查——这就是下一节。

### 2.4 连续性检查：新稿必须先证明没有破坏一致性

`continuity_check` 节点（`nodes/continuity_check.py:89`）在候选稿生效前验证三件事：

1. **锁定事实是否保留**——原稿里明确记录的事实（"男主不知道凶手身份"这类），新稿是否还在遵守；
2. **大纲事件是否兑现**——本集大纲要求的关键事件是否出现在新稿里；
3. **必需角色是否出场**——大纲指定本集必须出场的角色是否真的出现了。

检查分两层。**规则层**先跑：把事实文本和新稿文本都做归一化（去空白标点），先做子串匹配；匹配不到再算"内容字符覆盖率"——去掉停用词（只收录纯虚词，避免误伤实词）后，事实文本的字有多大比例出现在新稿里，覆盖率超过 0.5 算保留（允许措辞调整，不允许内容消失）。**语义层**兜底：规则测不出来的问题——比如把事实反着说了但措辞完全不同——交给一次 LLM 语义检查。

这里有个容易被忽略的实现细节：检查前会把**前几集的剧本摘要重新构建出来**（`_reconstruct_continuity_state`，`:49`）作为检查上下文，保证判断"第 2 集是否和第 1 集连贯"时真的掌握第 1 集的内容，而不是只看第 2 集孤立的文本。

检查结果分两种，处理方式都值得注意：

- **不通过**：候选稿**不删除**，保持 draft 状态留作诊断（能查到模型当时想改成什么样），检查报告也落库，然后设置 `needs_manual_review=True`，工作流终止，Run 转为 `needs_review`。**不会出现"检查没过就自动再改一遍"的循环。**
- **通过**：把候选稿的 `status` 字段从 draft 改成 valid。注意只改这一个元数据字段——**正文内容一个字不动**，这正是 Artifact 不可变模型的体现：旧版本永远在，"生效"只是状态标记的移动。

### 2.5 重评：用同一把尺子重新量

新稿生效后，`re_evaluate` 节点（`nodes/re_evaluate.py:43`）对它执行一次**全新的评估**——和第 1 节讲的评估流程完全相同，9 维度、锚点、服务端回填，一样不少。

两个具体机制：

**新稿必然得到全新评估。** 因为新稿是一个新的 Artifact ID，评估的幂等键（输入指纹）里包含被评估的 Artifact ID，所以不可能命中旧报告的缓存复用。修订后的分数一定是新算的。

**旧分数从修订计划里取，不从别处找。** 修订计划生成时记录了它依据的那份评估报告 ID（`source_evaluation_artifact_id`），重评时旧分就从这个 ID 读（`:85-88`）。这个细节是防一个时序问题：State 里存的评估 ID 可能已经被更新过，用计划里锁定的 ID 才保证比较的是"修订前那份报告"。

**比较规则**（`:127`）：`旧分 − 新分 > 5.0`，设置 `needs_manual_review=True`，原因写清楚"第 N 集修订后重评显著下降：68.0 → 61.5（下降 6.5 分，超过阈值 5 分）"。这条规则处理的是一种真实会发生的情况：模型按计划改了钩子，结果把节奏改坏了，总分反而跌了。**系统不判断"谁对谁错"，只报告数字变化，然后把决定权交还给人。**

### 2.6 循环控制：什么时候停、停成什么状态

`re_evaluate` 之后路由函数（`workflows/revision.py:72`）检查 State，四个出口：

| 情况 | 去向 | 用户看到的 |
|---|---|---|
| 执行出错 | END，Run 转 `failed` | 失败原因 + 错误码，可重试 |
| 重评掉分超过 5 / 连续性没过 | END，Run 转 `needs_review` | "需要人工复核" + 具体原因 |
| 还有别的集不合格，但修订轮数已用完（当前上限 1 轮） | END，Run 转 `needs_review` | "仍有 X 集低于标准，已达自动修订轮数上限" |
| 还有集不合格且轮数未满 | 回 `select_revision` 修下一集 | 进度事件继续推送 |
| 全部合格 | `finalize`，Run 转 `completed` | 创作完成 |

注意所有非正常出口都收敛到 `needs_review` 并附带原因文字——**系统不会在"拿不准"的时候替用户做决定，但也不会无声地停在那里**。

轮数上限为什么是 1？两个原因：成本（每轮修订是两次 LLM 调用加两次评估）和收益递减（自动修订没有人类反馈，多轮迭代容易在同一类问题上打转）。代码注释同时诚实声明了一个实现约束：断点恢复机制里的 `completed_nodes` 是个平铺列表，无法区分"同一节点的第 1 轮完成"和"第 2 轮完成"，所以"崩溃后重试不重复执行"的保证只在 1 轮时严格成立——上限调大之前必须先改造这个机制（`revision.py:13`）。

## 3. 用具体数字走一遍全流程

假设 3 集剧本评估结果是：第 1 集 82 分、第 2 集 68 分、第 3 集 90 分，第 2 集的评估里有两条问题（一条 high、一条 medium）。

1. **评估完成**，`needs_revision_decision = true`（第 2 集 68 < 75）。
2. **选集**：唯一不合格的是第 2 集 → 选中它，`revision_round = 1`。
3. **修订计划**：模型生成 3 个操作，其中 1 个引用了报告里不存在的 issue_id，被剔除；剩下 2 个有效操作（分别依据那条 high 问题和 medium 问题）。计划落库，锁定事实列表从 StoryBible 复制进来。
4. **生成新稿**：完整新稿以 draft 状态存为第 2 集 script_draft 的新版本。
5. **连续性检查**：规则层确认原稿中的两条锁定事实在新稿中覆盖率达标、大纲 4 个关键事件全部出现、3 个必需角色都出场；语义层没有发现矛盾 → 通过，新稿 status 改为 valid。
6. **重评**：新稿全新评估，得 79 分。旧分 68 从修订计划引用的旧报告读出。`68 − 79 = −11`，没有下降 → 不转人工。重算全项目：82、79、90 全部 ≥ 75 且无 high 问题 → `needs_revision_decision = false`。
7. **finalize**：Run 转 `completed`。数据库里第 2 集现在有两个版本：v1（68 分，仍可查、可回退）、v2（79 分，latest valid）。

失败分支替换第 6 步：如果重评只得到 60 分，`68 − 60 = 8 > 5` → Run 转 `needs_review`，用户看到"第 2 集修订后重评显著下降：68.0 → 60.0（下降 8.0 分，超过阈值 5 分）"，并可以对照 diff 决定回退还是人工再改。

## 4. 这个环节的四条设计原则

1. **语义判断和数值判断分开。** 判断"这段文字符合'冲突尖锐'的描述吗"是模型擅长的；判断"80 是否大于 75""9 个数加权后是多少""哪一集分数最低"是代码擅长的。前者交给模型但要求引用证据，后者全部由代码执行且公式公开。

2. **每个自动决定都有据可查。** 总分能从维度分和权重推出；修订的每个操作绑定评估报告里的具体问题；连续性检查的每条违规指向具体事实；转人工的原因写明数字和阈值。任何一个结论，用户（或开发者）都能沿着"结论 → 规则 → 输入"的链路复查。

3. **验证不过就不生效，且不自动重试。** 修订稿要先过连续性检查才提升 valid，重评掉了 5 分以上就停。系统不试图"自动改到好为止"，因为无人监督的迭代循环成本确定、收益不确定——停下来交给人是更便宜的选择。

4. **任何情况下不销毁旧数据。** 原稿永远保留；检查失败的新稿保留为 draft 供诊断；每次评估生成独立报告并记录 rubric 版本。"当前生效"只是指向最新 valid 版本的一个读法，不是数据的唯一形态。
