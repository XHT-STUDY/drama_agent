# 对话式创作 Agent 架构设计

> 状态：方向已确认，范围、架构与 UI 评审已完成。  
> 目标：将现有“一次性 Idea 提交 + 固定创作工作流”升级为可持续对话、可澄清、可计划、可确认、可执行、可追溯的创作 Agent 工作台。

## 1. 问题与目标

现有系统已经具备 Conversation/Message、Run/SSE、Artifact 版本、剧本评估与修订能力，但前端 `ChatInput` 只把输入作为首次创作 Idea，尚未形成真正的对话闭环。用户不能通过自然语言解释、修改大纲或修改剧本，也看不到 Agent 对修改目标、影响范围和执行步骤的判断。

本次改造必须交付以下行为：

1. 支持 `create_script`、`explain`、`revise_outline`、`revise_script`、`evaluate` 五类意图。
2. 信息不足时只追问，不创建 Run、不修改 Artifact。
3. 所有会产生 LLM 成本或 Artifact 变更的动作先展示结构化 Action Plan，并由用户确认。
4. 确认前重新校验目标 Artifact；来源版本已经变化时返回 `ACTION_STALE`，不执行旧计划。
5. 修改永远生成新 Artifact 版本，保留来源链、Prompt 版本和 Diff。
6. Run 结束后自动回写 AgentAction 状态，并追加包含结果、评分或失败原因的 assistant 消息。
7. 大纲修改只报告受影响剧集，不自动级联重写已有剧本。

## 2. 成功标准

- 50 条人工标注意图集上，生产模型意图准确率不低于 90%，剧集目标识别准确率不低于 95%；该评测不进入依赖外部模型的 CI。
- 模糊指令、跨项目 Artifact、过期 Action Plan、重复确认和项目存在活动 Run 都有确定性测试。
- 100% 的写操作在确认前不创建 Run、不创建 Artifact。
- 相同 `idempotency_key` 的重复 Turn 返回同一 AgentAction；同一 Action 重复确认只产生一个 Run。
- 所有修改产生新版本，原 Artifact checksum 保持不变。
- 解释、剧本修改、大纲修改三条核心路径有 Playwright E2E；现有创作、评估、修订 E2E 不回归。
- UI 支持键盘完成发送、确认、取消和切换上下文；状态变化通过 `aria-live` 对辅助技术可见。

## 3. 范围

### 3.1 本次建设

- 对话历史、会话切换和新建会话。
- 自然语言意图解析、目标解析、主动澄清和结构化行动计划。
- 读取当前项目状态并解释已有大纲、剧本和评估结果。
- 对指定剧本执行“必要时先评估 → 修订 → 连续性检查 → 重评”。
- 对最新有效大纲生成新版本，并计算受影响剧集和下游剧本。
- Action 确认、幂等、过期检测、Run 关联、结果回写和 SSE 展示。
- 对话式双栏工作台和移动端单栏/抽屉布局。
- 可重复的意图评测集、失败分类和项目指标报告。

### 3.2 明确不建设

- 不把系统改造成 Multi-Agent；仍由单编排器调用专业 Skill/Workflow。
- 不接入 MCP、外部 RAG、Redis 会话摘要或向量长期记忆。
- 不允许模型自由调用任意内部 API；执行动作由服务端白名单映射。
- 不支持通过聊天修改 Story Bible。
- 不自动重写受大纲变更影响的全部剧本，只给出影响清单和后续建议。
- 不建设富文本/WYSIWYG 编辑器；继续复用现有 Artifact 详情页和 Diff 页面。
- 不新增独立聊天产品或第二套消息存储。

## 4. 核心设计决策

### 4.1 聊天是控制面，不是内容事实源

Conversation/Message 保存用户意图、Agent 解释和执行反馈；Story Bible、大纲、剧本、评估和修订稿仍以 PostgreSQL Artifact 为唯一业务事实源。聊天文本不能直接覆盖 Artifact。

### 4.2 Planner 只产出选择器，不产出可执行权限

模型输出 `intent`、`target_type`、`episode_number`、约束、建议步骤和澄清问题，不允许输出任意 URL、SQL、工具名或未经验证的 Artifact ID。服务端根据项目和选择器解析最新有效 Artifact，并把意图映射到允许的 Workflow。

### 4.3 新增 AgentAction 作为审计和确认边界

AgentAction 持久化结构化计划、来源 Artifact 快照、状态、关联 Run 和结果摘要，状态机为：

```text
needs_input                         （不创建 AgentAction）
proposed -> queued -> running -> completed
    |          |          |-> failed
    |          |-> cancelled
    |-> stale
    |-> rejected
```

所有状态迁移由服务端校验；`proposed` 之前和用户确认之前没有业务副作用。

### 4.4 所有写动作先确认

`create_script`、`revise_outline`、`revise_script`、`evaluate` 都可能产生费用或新 Artifact，必须确认。`explain` 是只读动作，可以在同一 Turn 内直接返回解释。

### 4.5 大纲变更不静默污染下游

新大纲落库后，确定性 `OutlineImpactTool` 比较新旧每集字段并返回：

- `changed_episode_numbers`
- `dependent_script_artifact_ids`
- `scripts_based_on_older_outline`
- `recommended_follow_up`

已有剧本仍保留原有效版本，UI 显示“基于旧大纲版本”的警告；用户可另行发起剧本修订。

### 4.6 并发遵循单项目单活动 Run

沿用现有约束：一个项目同时只能有一个 `queued/running` Run。确认时发现活动 Run 返回 409；重复确认通过 AgentAction 行锁和 Run 唯一关联返回同一 Run。Message sequence 通过锁定 Conversation 行和唯一约束避免并发重复。

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
│   AgentAction Store     ContextBuilder       LangGraph Workflows        │
└──────────┬────────────────────┬───────────────┬─────────────────────────┘
           v                    v               v
┌─────────────────────────────────────────────────────────────────────────┐
│ PostgreSQL: Messages | AgentActions | Runs/Events | Immutable Artifacts │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.1 组件职责

| 组件 | 单一职责 |
|---|---|
| `AgentContextService` | 校验 active context，读取最近消息和最新 Artifact 摘要，调用 ContextBuilder 控制预算 |
| `AgentCommandPlannerSkill` | 输出结构化意图、目标选择器、约束、澄清问题和可读计划 |
| `AgentCommandService` | 保存消息/Action，执行白名单校验、确认、幂等和过期检测 |
| `WorkflowDispatcher` | 从 API 中抽离异步 Worker 调度，统一路由 create/evaluate/revise/revise_outline |
| `AgentActionLifecycleService` | 将 Run 终态、结果 Artifact 和失败原因回写 AgentAction 与 assistant 消息 |
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

响应 `turn_type` 只能是：

- `clarification`：返回一个明确问题，无 Action、无 Run。
- `answer`：只读解释，返回 assistant message。
- `plan`：返回 proposed AgentAction，等待确认。

### 6.2 Action

- `GET /api/v1/agent/actions/{action_id}`：查询 Action、Run 和结果摘要。
- `POST /api/v1/agent/actions/{action_id}/confirm`：执行过期检测后创建或复用 Run。
- `POST /api/v1/agent/actions/{action_id}/reject`：仅允许 proposed → rejected。

确认接口不接受客户端回传的 Plan 内容，只使用服务端持久化计划。

### 6.3 AgentAction 持久化字段

```text
id, project_id, conversation_id, user_message_id
intent, status, requires_confirmation
plan JSONB, source_artifact_ids JSONB, result JSONB
run_id nullable, idempotency_key
created_at, updated_at
```

Message 增加：

- `kind`: `text | clarification | action_plan | action_result | error`
- `metadata`: 保存 `agent_action_id`、`run_id`、Artifact 链接等展示信息，不保存业务正文。

## 7. 关键数据流

### 7.1 Turn 与确认

```text
User input
  -> [validate project/conversation/active artifact]
  -> [lock conversation + append user message]             side effect #1
  -> [build bounded context]
  -> [LLM structured planner]                               may timeout/invalid
  -> clarification/answer: append assistant message        side effect #2
  -> plan: resolve source artifacts
           create AgentAction(proposed)                     side effect #2
           append action_plan message                       side effect #3
  -> user confirms
  -> [lock action + check source versions + active Run]
  -> create WorkflowRun(queued) once                        side effect #4
  -> dispatch worker
  -> workflow creates immutable Artifact(s)                 side effect #5
  -> lifecycle finalizes Action + assistant result          side effect #6
```

任何 LLM 失败发生在 Action/Run 创建前；Artifact 写入失败由 Workflow 标记 Run failed，并由 lifecycle 写入可见错误消息。

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
| Turn API | 保存消息并返回回答/计划 | 空输入、超长输入、跨项目会话 | 422/404，不调用模型 |
| Active context | 使用当前页面 Artifact | ID 与项目、类型或集数不匹配 | 409 `INVALID_ACTIVE_CONTEXT` |
| Planner | 返回合法结构 | 超时、Schema 连续失败 | 保存 error 消息，可重试，不创建 Action |
| Clarification | 返回一个问题 | 用户再次模糊回答 | 继续追问，最多 3 次后给出可选命令示例 |
| Confirm | 创建 Run | 重复点击、活动 Run、Action 非 proposed | 复用原 Run或返回明确 409 |
| Stale check | 来源版本未变化 | 大纲/剧本已产生新 latest | Action→stale，要求重新规划 |
| Script revision | 生成新版本 | 缺评估、连续性失败、降分 | 自动补评估；失败转 needs_review，不覆盖原稿 |
| Outline revision | 生成新大纲 | 集数变化、重复集号、破坏锁定事实 | 保存 invalid 诊断，不设为 latest valid |
| Completion | 回写结果消息 | Worker 在 Artifact 后、消息前中断 | 终态 reconciliation 可幂等补写结果 |
| Message append | sequence 递增 | 同一会话并发发送 | 行锁 + 唯一约束；冲突重试一次 |

## 9. 测试映射

| 路径 | 覆盖 |
|---|---|
| 意图/目标/澄清结构化输出 | `[TESTED]` Skill unit + Prompt contract + golden fixtures |
| Context 预算与 active context 校验 | `[TESTED]` unit |
| Turn 幂等、跨项目、无副作用 | `[TESTED]` integration API |
| Action 状态、重复确认、过期计划 | `[TESTED]` integration DB/API |
| 既有 run/revision API 回归 | `[REGRESSION]` existing integration tests |
| 缺评估的剧本修改 | `[TESTED]` workflow integration |
| 大纲新版本与影响分析 | `[TESTED]` unit + workflow integration |
| Run 完成/失败消息回写 | `[TESTED]` integration events/API |
| 对话→确认→剧本 Diff | `[E2E]` Playwright |
| 对话→确认→大纲影响报告 | `[E2E]` Playwright |
| 模糊请求只追问 | `[E2E]` Playwright |
| 生产模型意图准确率 | `[MANUAL EVAL]` 50-case dataset，报告模型/版本/成本 |

## 10. 生产失败分析

### Path: `AgentCommandService.create_turn`

- Failure: Planner 超时或输出无法校验。
- Test exists: 是。
- Error handling: StructuredOutputParser 重试后写入 error message，事务不包含 AgentAction/Run。
- User sees: “未能理解本次请求，请重试或使用示例表达”。

### Path: `AgentCommandService.confirm_action`

- Failure: 用户确认时目标 Artifact 已被另一个 Run 更新。
- Test exists: 是。
- Error handling: 在事务和行锁内比较 source snapshot；Action 标为 stale。
- User sees: “项目内容已更新，请重新生成计划”。

### Path: `WorkflowDispatcher` completion

- Failure: Artifact 已成功落库，但 Action 结果消息写入失败。
- Test exists: 是。
- Error handling: Run 终态为事实源；GET Action 和 reconciliation 重试补写一次且通过 result hash 去重。
- User sees: 临时显示 Run 已完成；刷新后恢复结果消息。

### Path: `OutlineReviserSkill`

- Failure: 模型删除剧集、生成重复集号或破坏锁定事实。
- Test exists: 是。
- Error handling: Pydantic + 服务端不变量校验；invalid Artifact 保留诊断但不成为 latest valid。
- User sees: 修改未应用，并展示违反的不变量。

## 11. Plan Review — Scope

**SCOPE REVIEW: SELECTIVE EXPANSION**

**Reasoning:** “聊天 + 修改大纲/剧本”解决了真实的持续协作缺口，且能复用现有 Conversation、Run、Revision、SSE 和 Artifact；但原始设想缺少确认、审计、过期检测和效果验证，直接实现会退化成聊天外壳或产生旧版本误改。

**Proposed changes:**

1. 增加 AgentAction 持久化和确认状态机，这是可恢复、可审计执行的必要边界。
2. 增加 Outline 影响分析和单 Agent/对话命令评测，这是证明改造价值的最小闭环。
3. 限制首版为五种白名单意图，排除 Multi-Agent、MCP、RAG、Story Bible 修改和自动级联重写。

**Risk if ignored:** 无确认时高成本或高影响动作可能误执行；无版本快照时可能基于旧稿修改；无评测时无法证明系统比按钮式 Workflow 更 Agentic 或更有效。

## 12. Plan Review — Architecture

**结论：PASS WITH REQUIRED SAFEGUARDS**

架构沿用现有分层，不新增外部服务。关键保护是：Planner 无执行权限、所有写动作确认、Action 与来源版本持久化、单项目单活动 Run、Artifact 不可变、completion 可幂等恢复。上述保护均已映射到具体自动化测试，无未处理的静默失败路径。

最主要的扩展瓶颈是当前进程内 Worker；本次保持现状以控制范围，但 WorkflowDispatcher 必须从 API 层抽离，以便未来替换为队列而不改 AgentCommandService。

## 13. UI 设计

### 13.1 桌面布局

- `>= 1280px`：12 列网格，Conversation 7 列、Context/Action 5 列，页面最大宽度从现有 `max-w-5xl` 调整为 `max-w-7xl`。
- `1024–1279px`：Conversation 8 列、Context 4 列。
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
- 正文使用现有 `Noto Sans SC`/system fallback，消息正文 14px/1.6，标题 20–24px/1.2。

## 14. Design Review

| 维度 | 分数 | 依据与落地修正 |
|---|---:|---|
| Aesthetic | 8/10 | 延续创作工作台而非通用聊天模板；用 Artifact/Plan 卡片形成项目识别度 |
| Typography | 8/10 | 明确 14px 正文、20–24px 标题和行高；不新增字体网络依赖 |
| Color | 8/10 | 使用 CSS variables 和 WCAG AA；警告、失败不只依赖颜色，还带图标与文本 |
| Spacing | 9/10 | 统一 4px 基础间距阶梯，移除组件内任意值 |
| Layout | 9/10 | 对话为主、上下文为辅，桌面 7:5，移动端单任务视图 |
| Motion | 8/10 | 仅用于状态反馈和抽屉，150/250ms，支持 reduced motion |
| Responsiveness | 8/10 | 三段布局和移动端固定 Composer 已明确 |
| Accessibility | 8/10 | 键盘发送/确认、可见焦点、aria-live、44px 触控目标 |
| Content Hierarchy | 9/10 | 消息→计划→结果链清晰，一屏只有一个主要确认动作 |

**OVERALL: 8.3/10 — Design is implementation-ready.**

## 15. 前提与回滚

本设计假设用户需要通过自然语言完成跨页面的解释和修改。如果实际测试显示用户更偏好直接编辑，聊天仍作为高级控制入口，现有 Story Bible、大纲、剧本、版本和导出页面全部保留，因此可通过 Feature Flag 隐藏 Agent Workspace，而无需迁移或回滚 Artifact 数据。

具体回滚开关为 `NEXT_PUBLIC_AGENT_WORKSPACE_ENABLED`，默认 `true`；设为 `false` 后恢复现有 `ChatInput + RunProgress` 项目页。后端新增表和 API 保留，不影响旧客户端。
