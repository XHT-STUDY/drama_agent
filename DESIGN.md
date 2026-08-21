# 对话式创作 Agent 架构设计

> 状态：方向已确认，范围、架构与 UI 评审已完成。  
> 目标：将现有“一次性 Idea 提交 + 固定创作工作流”升级为可持续对话、可澄清、可计划、可确认、可执行、可追溯的创作 Agent 工作台。

## 1. 问题与目标

现有系统已经具备 Conversation/Message、Run/SSE、Artifact 版本、剧本评估与修订能力，但前端 `ChatInput` 只把输入作为首次创作 Idea，尚未形成真正的对话闭环。用户不能通过自然语言解释、修改大纲或修改剧本，也看不到 Agent 对修改目标、影响范围和执行步骤的判断。

本次改造必须交付以下行为：

1. 支持 `create_script`、`explain`、`revise_outline`、`revise_script`、`evaluate` 五类意图。
2. 每个请求先创建可幂等恢复的 AgentTurn；信息不足时只追问，不创建 AgentAction、Run 或 Artifact。
3. 所有会创建 Run、修改 Artifact 或触发高成本生成流程的动作先展示结构化 Action Plan，并由用户确认。Planner 和只读解释受单 Turn 预算限制，不要求二次确认。
4. 确认前重新校验目标 Artifact；来源版本已经变化时返回 `ACTION_STALE`，不执行旧计划。
5. 修改永远生成新 Artifact 版本，保留来源链、Prompt 版本和 Diff。
6. Run 结束后自动回写 AgentAction 状态，并追加包含结果、评分、目标达成情况或失败原因的 assistant 消息。
7. 大纲修改只报告受影响剧集，不自动级联重写已有剧本。
8. 目标只部分达成或被阻塞时，Agent 最多提出一个新的 Action Plan；后续计划仍需用户确认，不能自动执行。

## 2. 成功标准

- 50 条人工标注意图集上，生产模型意图准确率不低于 90%，剧集目标识别准确率不低于 95%；该评测不进入依赖外部模型的 CI。
- 模糊指令、跨项目 Artifact、过期 Action Plan、重复确认和项目存在活动 Run 都有确定性测试。
- 100% 的写操作在确认前不创建 Run、不创建 Artifact。
- 相同 `(project_id, idempotency_key)` 的重复 Turn 返回同一 AgentTurn 和既有响应，不重复追加消息或调用模型；同一 Action 重复确认只产生一个 Run。
- Planner 调用期间不持有数据库事务或 Conversation 行锁；同一会话并发追加消息不会产生重复 sequence。
- 同一项目在数据库约束下最多存在一个 `queued/running` Run；服务重启后，`queued` 或租约过期的 `running` Run 能被重新领取，并从最近成功节点恢复。
- 所有修改产生新版本，原 Artifact checksum 保持不变。
- 解释、剧本修改、大纲修改和“部分达成→提出后续计划”四条核心路径有 Playwright E2E；现有创作、评估、修订 E2E 不回归。
- UI 支持键盘完成发送、确认、取消和切换上下文；状态变化通过 `aria-live` 对辅助技术可见。

## 3. 范围

### 3.1 本次建设

- 对话历史、会话切换和新建会话。
- 自然语言意图解析、目标解析、主动澄清和结构化行动计划。
- 读取当前项目状态并解释已有大纲、剧本和评估结果。
- 对指定剧本执行“必要时先评估 → 修订 → 连续性检查 → 重评”。
- 对最新有效大纲生成新版本，并计算受影响剧集和下游剧本。
- Action 确认、幂等、过期检测、Run 关联、结果回写和 SSE 展示。
- Run 完成后的目标达成判断、证据汇总和一次受约束的后续计划。
- 对话式双栏工作台和移动端单栏/抽屉布局。
- 可重复的意图评测集、失败分类和项目指标报告。

### 3.2 明确不建设

- 不把系统改造成 Multi-Agent；仍由单编排器调用专业 Skill/Workflow。
- 不接入 MCP、外部 RAG、Redis 会话摘要或向量长期记忆。
- 不允许模型自由调用任意内部 API；执行动作由服务端白名单映射。
- 不建设无人值守的自动循环；后续计划最多一层，且每次写操作都要重新确认。
- 不支持通过聊天修改 Story Bible。
- 不自动重写受大纲变更影响的全部剧本，只给出影响清单和后续建议。
- 不建设富文本/WYSIWYG 编辑器；继续复用现有 Artifact 详情页和 Diff 页面。
- 不新增独立聊天产品或第二套消息存储。

## 4. 核心设计决策

### 4.1 聊天是控制面，不是内容事实源

Conversation/Message 保存用户意图、Agent 解释和执行反馈；Story Bible、大纲、剧本、评估和修订稿仍以 PostgreSQL Artifact 为唯一业务事实源。聊天文本不能直接覆盖 Artifact。

### 4.2 Planner 只产出选择器，不产出可执行权限

模型输出 `intent`、`target_type`、`episode_number`、约束、建议步骤和澄清问题，不允许输出任意 URL、SQL、工具名或未经验证的 Artifact ID。服务端根据项目和选择器解析最新有效 Artifact，并把意图映射到允许的 Workflow。

### 4.3 AgentTurn 负责请求幂等，AgentAction 负责执行审计

每个 Turn 都先持久化 AgentTurn。它保存幂等键、用户消息、Planner 状态和最终响应，因此 clarification、answer、plan 和 error 都能在重试时返回原结果，不会重复调用模型。Planner 由短租约保护；进程在规划中退出后，租约过期的 Turn 可以由后续请求重新领取。

```text
received -> planning -> needs_input
                    |-> answered
                    |-> action_proposed
                    |-> failed
```

只有 plan 分支创建 AgentAction。AgentAction 持久化结构化计划、来源 Artifact 快照、关联 Run 和结果摘要，状态机为：

```text
proposed -> queued -> running -> completed
    |          |          |-> needs_review
    |          |          |-> failed
    |          |-> cancelled
    |-> stale
    |-> rejected
```

AgentAction 可通过 `parent_action_id` 关联一次后续计划，`replan_depth` 最大为 1。所有状态迁移由服务端校验；`proposed` 之前和用户确认之前没有 Run 或 Artifact 副作用。

### 4.4 所有写动作先确认

`create_script`、`revise_outline`、`revise_script`、`evaluate` 都会创建 Run 或新 Artifact，必须确认。Planner 和 `explain` 可以在同一 Turn 内直接调用模型，但必须受单 Turn token、费用和超时预算约束。

### 4.5 大纲变更不静默污染下游

新大纲落库后，确定性 `OutlineImpactTool` 比较新旧每集字段并返回：

- `changed_episode_numbers`
- `dependent_script_artifact_ids`
- `scripts_based_on_older_outline`
- `recommended_follow_up`

已有剧本仍保留原有效版本，UI 显示“基于旧大纲版本”的警告；用户可另行发起剧本修订。

### 4.6 并发遵循单项目单活动 Run

一个项目同时只能有一个 `queued/running` Run。确认时的应用层检查负责返回可读错误，PostgreSQL partial unique index 负责处理并发确认。重复确认通过 AgentAction 行锁和唯一 `run_id` 返回同一 Run。Message sequence 通过短暂锁定 Conversation 行和 `(conversation_id, sequence)` 唯一约束避免并发重复。

### 4.7 执行完成后判断目标是否达成

AgentOutcomeService 根据原始用户目标、Action Plan、结果 Artifact、Diff、评分和连续性检查生成结构化结果：

- `goal_status`: `achieved | partially_achieved | blocked`
- `evidence_artifact_ids`: 支撑判断的 Artifact
- `score_delta`: 可空的修订前后评分变化
- `remaining_constraints`: 尚未满足的用户约束
- `recommended_next_action`: 可空的后续意图、目标和约束

确定性证据优先；只有“用户语义约束是否满足”无法由现有校验器判断时才调用结构化 OutcomeEvaluator Skill。目标部分达成或被阻塞时，系统最多创建一个 `proposed` 子 Action，并使用当前最新 Artifact 重新建立来源快照。子 Action 仍需用户确认，不自动创建 Run。

## 5. 架构

```text
┌──────────────────────── Next.js Agent Workspace ────────────────────────┐
│ ConversationPanel │ ActionPlan/RunProgress │ ArtifactContext/Result     │
└──────────────┬──────────────────────┬──────────────────────┬────────────┘
               │ POST turn            │ POST confirm         │ GET artifacts
               v                      v                      v
┌──────────────────────────── FastAPI API ────────────────────────────────┐
│ Agent API     Conversation API      Run/SSE API        Artifact API     │
└──────────────┬───────────────────────────────────────────────────────────┘
               v
┌────────────────────── Application Services ─────────────────────────────┐
│ AgentCommandService ─ AgentContextService ─ WorkflowDispatcher          │
│          │                    │                    │                    │
│          v                    v                    v                    │
│ Turn/Action Stores      ContextBuilder       Durable Workflows          │
└──────────┬────────────────────┬───────────────┬─────────────────────────┘
           v                    v               v
┌─────────────────────────────────────────────────────────────────────────┐
│ PostgreSQL: Turns/Actions | Runs/Events | Artifacts | Checkpoints        │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.1 组件职责

| 组件 | 单一职责 |
|---|---|
| `AgentContextService` | 校验 active context，读取最近消息和最新 Artifact 摘要，调用 ContextBuilder 控制预算 |
| `AgentCommandPlannerSkill` | 输出结构化意图、目标选择器、约束、澄清问题和可读计划 |
| `AgentCommandService` | 用短事务保存 Turn、消息和 Action，执行幂等领取、白名单校验、确认和过期检测 |
| `WorkflowDispatcher` | 从数据库领取 Run、维护租约、恢复未完成任务，并统一路由 create/evaluate/revise/revise_outline |
| `AgentActionLifecycleService` | 将 Run 终态、结果 Artifact、失败原因和结构化 Outcome 回写 AgentAction 与 assistant 消息 |
| `AgentOutcomeService` | 汇总确定性证据，必要时调用 OutcomeEvaluator，并最多提出一个需要确认的后续 Action |
| `OutlineReviserSkill` | 根据旧大纲、Story Bible 和用户约束输出完整新大纲 |
| `OutlineImpactTool` | 确定性比较大纲版本并识别下游影响，不做语义生成 |
| `AgentWorkspace` | 显示消息、计划、确认、运行状态、活动 Artifact 和结果链接 |

## 6. API 与数据契约

### 6.1 Agent Turn

`POST /api/v1/projects/{project_id}/agent/turns`

请求：

```json
{
  "conversation_id": "uuid-or-null",
  "content": "把第三集男女主冲突提前，但不要暴露凶手身份",
  "active_context": {
    "artifact_type": "script_draft",
    "episode_number": 3,
    "artifact_id": "uuid"
  },
  "idempotency_key": "client-generated-key"
}
```

响应始终包含 `turn_id`、`status` 和最终结果快照。重复请求若命中仍在有效租约内的 `planning` Turn，返回 202 和原 `turn_id`；已结束的 Turn 直接返回原响应。最终 `turn_type` 只能是：

- `clarification`：返回一个明确问题，无 Action、无 Run。
- `answer`：只读解释，返回 assistant message。
- `plan`：返回 proposed AgentAction，等待确认。

### 6.2 Action

- `GET /api/v1/agent/turns/{turn_id}`：查询 Planner 状态、最终响应和关联 Action。
- `GET /api/v1/agent/actions/{action_id}`：查询 Action、Run 和结果摘要。
- `POST /api/v1/agent/actions/{action_id}/confirm`：执行过期检测后创建或复用 Run。
- `POST /api/v1/agent/actions/{action_id}/reject`：仅允许 proposed → rejected。

确认接口不接受客户端回传的 Plan 内容，只使用服务端持久化计划。

### 6.3 AgentTurn 持久化字段

```text
id, project_id, conversation_id, user_message_id
idempotency_key, request_hash, status, turn_type nullable
planner_output JSONB, response_message_id nullable
planning_lease_owner, planning_lease_expires_at, planning_attempt_count
error_code nullable, error_detail nullable
created_at, updated_at
```

`(project_id, idempotency_key)` 唯一。重复 key 的 request_hash 不一致时返回 409 `IDEMPOTENCY_KEY_REUSED`，不能返回旧响应。只有持有有效 planning lease 的请求可以调用 Planner 和写入最终响应；租约过期后允许原子重新领取。

### 6.4 AgentAction 持久化字段

```text
id, project_id, conversation_id, agent_turn_id
parent_action_id nullable, replan_depth
intent, status, requires_confirmation
plan JSONB, source_artifact_ids JSONB, result JSONB
run_id nullable unique
created_at, updated_at
```

来源快照至少保存 Artifact 的 `id/type/episode_number/version/checksum`。数据库约束 `(parent_action_id, replan_depth)` 唯一，保证终态 reconciliation 不会重复创建后续计划。

### 6.5 WorkflowRun 恢复字段与约束

- 增加 `idempotency_key`、`request_hash`、`lease_owner`、`lease_expires_at`、`attempt_count`。
- `(project_id, action, idempotency_key)` 在 key 非空时唯一。
- 对 `status IN ('queued', 'running')` 建立按 project_id 的 partial unique index。
- Dispatcher 使用 `FOR UPDATE SKIP LOCKED` 领取 queued 或租约过期的 running Run。
- LangGraph 使用 PostgreSQL checkpointer，`thread_id=run_id`，从最近成功节点恢复。

Message 增加：

- `kind`: `text | clarification | action_plan | action_result | error`
- `metadata`: 保存 `agent_turn_id`、`agent_action_id`、`run_id`、Artifact 链接等展示信息，不保存业务正文。

## 7. 关键数据流

### 7.1 Turn 与确认

```text
User input
  -> Phase A: short transaction
       validate project/conversation/active artifact
       get-or-create AgentTurn by (project_id, idempotency_key)
       briefly lock Conversation and append user message
       commit and release all locks
  -> Outside transaction
       atomically claim AgentTurn planning lease
       build bounded context
       call LLM structured planner
  -> Phase B: short transaction
       lock AgentTurn and verify lease owner
       clarification/answer: append assistant message and finalize Turn
       plan: resolve source snapshots, create AgentAction(proposed),
             append action_plan message and finalize Turn
  -> user confirms
  -> Phase C: short transaction
       lock Action, check source versions and active Run
       atomically create WorkflowRun(queued) and link Action
  -> Dispatcher claims Run lease from PostgreSQL
  -> workflow resumes by thread_id=run_id and creates immutable Artifact(s)
  -> lifecycle finalizes Action, evaluates outcome and appends result message
  -> partially_achieved/blocked: create at most one proposed child Action
```

Planner 失败发生在 AgentAction 和 Run 创建前，并写入 AgentTurn 的错误结果。执行阶段的 LLM 或 Artifact 写入失败发生在 Run 创建后，由 Workflow 标记 Run failed，Lifecycle 写入可见错误消息。服务在任一短事务提交后退出时，Turn planning lease、Run lease 和终态 reconciliation 都允许后续实例幂等接管。

### 7.2 剧本修改

```text
latest/active script
  -> ensure bound evaluation (missing => evaluate target)
  -> revision plan
  -> candidate script(draft)
  -> continuity check
  -> pass: promote valid + re-evaluate + diff
  -> fail: keep diagnostic draft + needs_review
```

### 7.3 大纲修改

```text
latest valid outline + Story Bible + user constraints
  -> OutlineReviserSkill
  -> schema/invariant validation
  -> new outline Artifact(valid or invalid)
  -> OutlineImpactTool(old, new)
  -> AgentAction result + warning for dependent scripts
```

## 8. 边界与失败模式

| 组件 | 正常路径 | 边界/错误 | 用户可见行为 |
|---|---|---|---|
| Turn API | 幂等创建 Turn 并返回回答/计划 | 空输入、超长输入、跨项目会话 | 422/404，不调用模型 |
| Turn lease | 事务外执行 Planner | 重复请求、进程在规划中退出 | 有效租约返回 202；过期后幂等接管 |
| Active context | 使用当前页面 Artifact | ID 与项目、类型或集数不匹配 | 409 `INVALID_ACTIVE_CONTEXT` |
| Planner | 返回合法结构 | 超时、Schema 连续失败 | Turn→failed 并保存 error 消息，不创建 Action |
| Clarification | 返回一个问题 | 用户再次模糊回答 | 继续追问，最多 3 次后给出可选命令示例 |
| Confirm | 创建 Run | 重复点击、并发 Action、Action 非 proposed | 复用原 Run，或由数据库约束返回明确 409 |
| Dispatcher | 领取并续租 Run | 服务退出、租约过期、多个实例竞争 | 重新领取并从最近 checkpoint 恢复 |
| Stale check | 来源版本未变化 | 大纲/剧本已产生新 latest | Action→stale，要求重新规划 |
| Script revision | 生成新版本 | 缺评估、连续性失败、降分 | 自动补评估；失败转 needs_review，不覆盖原稿 |
| Outline revision | 生成新大纲 | 集数变化、重复集号、破坏锁定事实 | 保存 invalid 诊断，不设为 latest valid |
| Completion | 回写 Outcome 和结果消息 | Worker 在 Artifact 后、消息前中断 | reconciliation 幂等补写，并避免重复后续计划 |
| Message append | sequence 递增 | 同一会话并发发送 | 行锁 + 唯一约束；冲突重试一次 |

## 9. 测试映射

| 路径 | 覆盖 |
|---|---|
| 意图/目标/澄清结构化输出 | `[TESTED]` Skill unit + Prompt contract + golden fixtures |
| Context 预算与 active context 校验 | `[TESTED]` unit |
| Turn 在 clarification/answer/plan/error 各分支的幂等 | `[TESTED]` integration API |
| Planner 不持有长事务、planning lease 过期接管 | `[TESTED]` integration DB/API |
| Action 状态、重复确认、过期计划 | `[TESTED]` integration DB/API |
| 并发确认只创建一个活动 Run | `[TESTED]` PostgreSQL integration |
| 进程退出后重新领取 Run 并跳过已完成节点 | `[TESTED]` workflow recovery integration |
| 既有 run/revision API 回归 | `[REGRESSION]` existing integration tests |
| 缺评估的剧本修改 | `[TESTED]` workflow integration |
| 大纲新版本与影响分析 | `[TESTED]` unit + workflow integration |
| Outcome 证据、目标状态和一次后续计划 | `[TESTED]` unit + integration |
| Run 完成/失败消息回写 | `[TESTED]` integration events/API |
| 对话→确认→剧本 Diff | `[E2E]` Playwright |
| 对话→确认→大纲影响报告 | `[E2E]` Playwright |
| 模糊请求只追问 | `[E2E]` Playwright |
| 部分达成→提出后续计划→再次确认 | `[E2E]` Playwright |
| 生产模型意图与目标达成评测 | `[MANUAL EVAL]` 50-case dataset，报告模型/版本/成本/目标达成率 |

## 10. 生产失败分析

### Path: `AgentCommandService.create_turn`

- Failure: Planner 超时、输出无法校验，或服务在规划中退出。
- Test exists: 是。
- Error handling: Planner 在数据库事务外执行。校验失败时短事务写入 Turn failed 和 error message；进程退出时 planning lease 过期，后续同幂等请求可重新领取。
- User sees: 规划中返回 202；失败时显示“未能理解本次请求，请重试或使用示例表达”。

### Path: `AgentCommandService.confirm_action`

- Failure: 用户确认时目标 Artifact 已更新，或两个 Action 同时确认。
- Test exists: 是。
- Error handling: 在事务和行锁内比较 source snapshot；Action 过期时标为 stale。并发 Run 由 partial unique index 拦截，失败事务重新查询并返回现有 Run 或 `PROJECT_HAS_ACTIVE_RUN`。
- User sees: “项目内容已更新，请重新生成计划”，或“项目已有任务运行中”。

### Path: `WorkflowDispatcher` execution

- Failure: 服务在 Run 创建后、调度前退出，或 Worker 在节点执行中退出。
- Test exists: 是。
- Error handling: Dispatcher 从数据库领取 queued/租约过期的 running Run；LangGraph 使用 `thread_id=run_id` 从最近 checkpoint 恢复，Artifact 写入继续受 input hash 幂等保护。
- User sees: Run 保持 queued/running，恢复后继续收到 SSE；超过恢复预算时转 failed。

### Path: `AgentActionLifecycleService` completion

- Failure: Artifact 已成功落库，但 Action 结果消息写入失败。
- Test exists: 是。
- Error handling: Run 终态为事实源；GET Action 和 reconciliation 重试补写一次，通过 result hash 去重 Outcome、消息和后续 Action。
- User sees: 临时显示 Run 已完成；刷新后恢复结果消息。

### Path: `AgentOutcomeService.evaluate`

- Failure: OutcomeEvaluator 超时、证据不足或建议了不支持的意图。
- Test exists: 是。
- Error handling: 保留确定性证据并将目标标为 `blocked`；不支持的建议被白名单拒绝，不创建后续 Action。后续计划通过 `parent_action_id + replan_depth` 幂等去重。
- User sees: 结果仍可查看，并明确显示“暂时无法判断目标是否完全达成”；不会自动继续执行。

### Path: `OutlineReviserSkill`

- Failure: 模型删除剧集、生成重复集号或破坏锁定事实。
- Test exists: 是。
- Error handling: Pydantic + 服务端不变量校验；invalid Artifact 保留诊断但不成为 latest valid。
- User sees: 修改未应用，并展示违反的不变量。

## 11. Plan Review: Scope

**SCOPE REVIEW: SELECTIVE EXPANSION**

**Reasoning:** “聊天 + 修改大纲/剧本”解决了真实的持续协作缺口，且能复用现有 Conversation、Run、Revision、SSE 和 Artifact；但原始设想缺少确认、审计、过期检测和效果验证，直接实现会退化成聊天外壳或产生旧版本误改。

**Proposed changes:**

1. 增加 AgentTurn 请求收据与 AgentAction 确认状态机，分别负责全分支幂等和执行审计。
2. 增加 Outline 影响分析、目标达成判断和一次受约束的后续计划，形成可观察的执行循环。
3. 限制首版为五种白名单意图，排除 Multi-Agent、MCP、RAG、Story Bible 修改和自动级联重写。

**Risk if ignored:** 无持久化 Turn 时澄清和回答无法幂等；跨 LLM 长事务会造成锁等待；进程内 Worker 无法提供可信恢复；只回写结果而不判断目标时，系统仍停留在命令路由层。

## 12. Plan Review: Architecture

**结论：PASS WITH REQUIRED SAFEGUARDS**

架构沿用现有分层，不新增外部服务。关键保护包括：Planner 无执行权限、所有写动作确认、Turn 与 Action 分离、短事务、数据库并发约束、PostgreSQL checkpoint、Artifact 不可变和终态幂等恢复。

WorkflowDispatcher 仍运行在应用进程内，但数据库是任务领取和恢复的事实源。进程内 task 只负责唤醒执行，不再承担唯一调度状态；未来替换为独立 Worker 时不需要修改 AgentCommandService、AgentAction 或公共 API。

## 13. UI 设计

### 13.1 桌面布局

- `>= 1280px`：12 列网格，Conversation 7 列、Context/Action 5 列，页面最大宽度从现有 `max-w-5xl` 调整为 `max-w-7xl`。
- `1024-1279px`：Conversation 8 列、Context 4 列。
- `< 1024px`：单列消息流；Context 通过右侧抽屉打开。
- `< 640px`：底部固定 Composer，Action Plan 卡片内按钮纵向排列，所有触控目标至少 44px。

### 13.2 信息层级

1. 当前会话与 Agent 状态。
2. 消息流和待确认计划。
3. 当前活动 Artifact 与影响范围。
4. Run 细节、历史版本和次级导航。

Action Plan 必须显示：目标、约束、步骤、来源版本、预计影响、是否产生费用/新版本。确认是唯一主按钮；拒绝和重新描述为次级按钮。

### 13.3 视觉规范

- 沿用灰白工作台和蓝色主操作，不引入新的品牌色。
- 在 `globals.css` 定义 surface/text/border/accent/success/warning/danger CSS variables；正文对比度满足 WCAG AA。
- 间距只使用 4/8/12/16/24/32/48。
- 状态切换使用 150ms，抽屉使用 250ms；尊重 `prefers-reduced-motion`。
- 正文使用现有 `Noto Sans SC`/system fallback，消息正文 14px/1.6，标题 20-24px/1.2。

## 14. Design Review

| 维度 | 分数 | 依据与落地修正 |
|---|---:|---|
| Aesthetic | 8/10 | 延续创作工作台而非通用聊天模板；用 Artifact/Plan 卡片形成项目识别度 |
| Typography | 8/10 | 明确 14px 正文、20-24px 标题和行高；不新增字体网络依赖 |
| Color | 8/10 | 使用 CSS variables 和 WCAG AA；警告、失败不只依赖颜色，还带图标与文本 |
| Spacing | 9/10 | 统一 4px 基础间距阶梯，移除组件内任意值 |
| Layout | 9/10 | 对话为主、上下文为辅，桌面 7:5，移动端单任务视图 |
| Motion | 8/10 | 仅用于状态反馈和抽屉，150/250ms，支持 reduced motion |
| Responsiveness | 8/10 | 三段布局和移动端固定 Composer 已明确 |
| Accessibility | 8/10 | 键盘发送/确认、可见焦点、aria-live、44px 触控目标 |
| Content Hierarchy | 9/10 | 消息→计划→结果链清晰，一屏只有一个主要确认动作 |

**OVERALL: 8.3/10，Design is implementation-ready.**

## 15. 前提与回滚

本设计假设用户需要通过自然语言完成跨页面的解释和修改。如果实际测试显示用户更偏好直接编辑，聊天仍作为高级控制入口，现有 Story Bible、大纲、剧本、版本和导出页面全部保留，因此可通过 Feature Flag 隐藏 Agent Workspace，而无需迁移或回滚 Artifact 数据。

具体回滚开关为 `NEXT_PUBLIC_AGENT_WORKSPACE_ENABLED`，默认 `true`；设为 `false` 后恢复现有 `ChatInput + RunProgress` 项目页。后端新增表和 API 保留，不影响旧客户端。
