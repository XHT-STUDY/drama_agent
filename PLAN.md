# 对话式创作 Agent Implementation Plan

> **For agentic workers:** 开工前先把 Task 1 至 Task 12 登记为 DEV_PLAN Phase J。执行遵循仓库“一次只做一个任务”的规则，不并行修改共享文件。
>
> **Review context:** Scope、architecture 和 UI reviews 位于 `DESIGN.md`。本文档只描述执行内容。
>
> **Build Agent Contract:**
> - **Required:** Goal、Tech Stack、Execution Hints、全部 Tasks（Depends on + Spec + Files）。
> - **Optional:** `DESIGN.md` 中的架构与评审材料。
> - **Skip during execution:** 已完成的范围、架构和 UI 评审。

**Goal:** 将现有项目工作台升级为支持自然语言解释、澄清、修改大纲/剧本、确认执行、版本追踪、目标达成判断和一次受约束再规划的创作 Agent。

**Architecture:** 复用 Conversation/Message、Run/SSE、Artifact 和 Revision 基础设施。AgentTurn 负责所有响应分支的幂等，AgentAction 负责计划确认与执行审计；Planner 在数据库事务外运行，服务端负责白名单路由、过期检测和工作流执行。Run 使用数据库租约和 PostgreSQL checkpointer 恢复，Lifecycle 在终态生成 Outcome，并最多提出一个仍需确认的后续 Action。

**Tech Stack:** Python 3.11、FastAPI、Pydantic v2、SQLAlchemy Async、Alembic、PostgreSQL、LangGraph、langgraph-checkpoint-postgres、Next.js、React、TypeScript、TanStack Query、Tailwind CSS、pytest、Vitest、Playwright、FakeLLM。

**Depth:** Deep，跨 `backend/domain/db/application/api/skills/workflows`、`frontend`、`e2e` 和 `docs`。任务估算合计 21 个开发人日，包含联调与恢复演练的交付区间为 22～28 个开发人日。

## Architecture Overview

```text
AgentWorkspace
  -> Agent Turn API
      -> AgentCommandService -> AgentTurn(received/planning)
      -> AgentContextService -> ContextBuilder
      -> AgentCommandPlannerSkill                     outside DB transaction
      -> clarification/answer | AgentAction(proposed)
  -> Confirm API
      -> stale/idempotency/active-run DB guards
      -> WorkflowDispatcher -> DB claim/lease
          -> create / evaluate / conversational revision / outline revision
          -> PostgreSQL checkpoint + immutable Artifact + SSE
      -> AgentActionLifecycleService
          -> AgentOutcome -> result message
```

## Execution Hints

**Suggested mode:** Sequential

每个 Task 独立完成实现、测试和文档收尾后再进入下一个 Task。Wave 只表示可演示里程碑，不授权并行修改。

**Wave 1: Persistence and durable dispatcher foundation**
- Task 1: AgentTurn、AgentAction 与 Message 持久化契约
- Task 5: 数据库租约、持久化幂等与 PostgreSQL checkpoint

**Wave 2: Context, planning and public command API**
- Task 2: 项目上下文组装
- Task 3: 对话命令 Planner Skill
- Task 4: Agent Turn/Action 服务与 API

**Wave 3: Revision workflows**
- Task 6: 对话式剧本修订工作流
- Task 7: 大纲修订 Skill 与影响分析 Tool
- Task 8: 大纲修订工作流

**Wave 4: Outcome and bounded replan**
- Task 9: Run 终态回写、目标达成判断与一次后续计划

**Wave 5: Workspace integration**
- Task 10: 前端契约和数据 Hooks
- Task 11: 对话式创作工作台

**Wave 6: Exit gate**
- Task 12: E2E、评测、文档和回归门禁

执行顺序为 Task 1 → 5 → 2 → 3 → 4 → 6 → 7 → 8 → 9 → 10 → 11 → 12。每个 Wave 合并后系统都保持可用；未完成的 intent 不进入 Planner 的 `available_intents`，不能生成确认后必然失败的计划。

### Task 1: AgentTurn、AgentAction 与消息持久化契约

**Depends on:** none  
**Estimate:** 2d

**Files:**
- Create: `backend/app/domain/agent_command.py`
- Create: `backend/app/db/models/agent_turn.py`
- Create: `backend/app/db/models/agent_action.py`
- Create: `backend/app/db/repositories/agent_turns.py`
- Create: `backend/app/db/repositories/agent_actions.py`
- Create: `backend/migrations/versions/0005_agent_turn_actions.py`
- Modify: `backend/app/db/models/message.py`
- Modify: `backend/app/db/models/__init__.py`
- Modify: `backend/app/domain/conversation.py`
- Modify: `backend/app/application/conversation_service.py`
- Test: `backend/tests/integration/db/test_agent_turns.py`
- Test: `backend/tests/integration/db/test_agent_actions.py`
- Test: `backend/tests/integration/db/test_migration.py`
- Test: `backend/tests/integration/api/test_conversations.py`

**Spec:**

定义 `AgentTurnStatus`：`received | planning | needs_input | answered | action_proposed | failed`；定义 `AgentActionStatus`：`proposed | queued | running | completed | needs_review | failed | cancelled | stale | rejected`。定义 `AgentIntent`、`ActiveArtifactContext`、`ActionTarget`、`ActionStep`、`AgentActionPlan`、`AgentOutcome` 和响应 Schema，全部使用 `extra="forbid"`。Command 继续使用按 intent 判别的联合类型，只有服务端解析后的 Command 可以持久化。

新增 `agent_turns` 和 `agent_actions` 表，字段与状态机遵循 `DESIGN.md §6.3` 和 `§6.4`。`agent_turns(project_id, idempotency_key)` 唯一，并保存规范化 request_hash；相同 key 携带不同 payload 时返回 `IDEMPOTENCY_KEY_REUSED`。`agent_actions.run_id` 唯一；`(parent_action_id, replan_depth)` 唯一且 `replan_depth` 只能为 0 或 1。AgentTurn 保存 planning lease、attempt、最终响应消息和错误快照，clarification、answer、plan、error 都必须先有 Turn。

Message 增加 `kind` 和 `metadata` JSONB；已有消息回填 `kind=text`、`metadata={}`。追加消息时短暂锁定 Conversation 行，并增加 `(conversation_id, sequence)` 唯一约束；冲突最多重试一次。Repository 提供 `claim_planning_lease()`、`get_for_update()` 和受状态机约束的 `transition()`。Downgrade 会删除 Agent 元数据，文档必须明确它是结构回滚，不是无损数据回滚。

**TDD anchors:**

- `backend/tests/integration/db/test_agent_turns.py::test_duplicate_answer_turn_returns_original_response_without_second_llm_call`
- `backend/tests/integration/db/test_agent_actions.py::test_duplicate_confirmation_cannot_attach_two_runs`

### Task 2: 项目上下文组装与预算控制

**Depends on:** Task 1  
**Estimate:** 1d

**Files:**
- Create: `backend/app/application/agent_context_service.py`
- Modify: `backend/app/memory/context_builder.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/unit/application/test_agent_context_service.py`
- Test: `backend/tests/unit/memory/test_context_builder.py`

**Spec:**

`AgentContextService.build()` 接收 project、conversation 和可空 `ActiveArtifactContext`，输出 planner 使用的字符串上下文和 `ContextManifest`。上下文只包含最近 12 条消息、最新有效 Story Bible/大纲摘要、现有剧集/评估索引、活动 Artifact 摘要和用户当前目标；不把全部剧本、全部历史消息或工具实现放入 Planner Prompt。

增加配置默认值：`agent_context_budget_tokens=12000`、`agent_recent_message_limit=12`、`agent_turn_lease_seconds=120`、`agent_turn_max_tokens=16000`、`agent_max_replan_depth=1`。使用现有 ContextBuilder 分区与 manifest；当前用户请求和 active target 不能静默截断，超限返回 `CONTEXT_TOO_LARGE`。活动 Artifact 必须属于当前项目，且 type/episode 与请求一致，否则返回 `INVALID_ACTIVE_CONTEXT`。

测试无 Artifact、只有 Story Bible、10 集剧本、超长历史、跨项目 active ID、预算边界和裁剪顺序。

**TDD anchor:** `backend/tests/unit/application/test_agent_context_service.py::test_active_artifact_from_other_project_is_rejected`

### Task 3: 对话命令 Planner Skill

**Depends on:** Task 2  
**Estimate:** 1d

**Files:**
- Create: `backend/app/skills/agent_command_planner.py`
- Create: `backend/app/prompts/templates/agent_command_planner.md`
- Create: `backend/tests/golden/agent_command_plan_valid.json`
- Create: `backend/tests/golden/agent_command_clarification.json`
- Modify: `backend/app/prompts/manifest.yaml`
- Modify: `backend/app/prompts/loader.py`
- Modify: `backend/app/llm/openai_compatible.py`
- Test: `backend/tests/unit/skills/test_agent_command_planner.py`
- Test: `backend/tests/contract/test_prompts.py`

**Spec:**

Planner 输出 `AgentPlannerOutput`，包含 `turn_type=clarification|answer|plan`、白名单 intent、目标 type/episode、约束列表、可读步骤、影响摘要和一个澄清问题。模型不得输出工具名、API、SQL 或可直接执行的任意 Artifact ID；服务端忽略并拒绝额外字段。

以下情况必须 `clarification`：修改请求没有可推断目标、指代“这里”但没有 active context、集数超出项目范围、同时要求互相冲突的修改。连续三次无法确定目标时，返回包含四个合法命令示例的 clarification，不自动猜测。

`requires_confirmation` 不由模型决定：服务端对除 explain 外的 intent 强制设为 true。使用 StructuredOutputParser 的现有重试；最终无效时返回 `INVALID_OUTPUT`，调用方不创建 AgentAction。

Planner 输入必须包含服务端生成的 `available_intents`。未完成对应 Workflow 的 intent 不得出现在输出 Schema 枚举中；Wave 2 只开放 `create_script | explain | evaluate`，Task 6/8 完成后再开放修订意图。Action Plan 的执行步骤由服务端根据 intent 和 Workflow 模板生成，模型只提供用户约束和影响摘要。

**TDD anchor:** `backend/tests/unit/skills/test_agent_command_planner.py::test_ambiguous_revision_returns_single_clarification_question`

### Task 4: Agent Turn、Action Service 与 API

**Depends on:** Task 1, Task 2, Task 3, Task 5

**Estimate:** 2d

**Files:**
- Create: `backend/app/application/agent_command_service.py`
- Create: `backend/app/api/v1/agent.py`
- Modify: `backend/app/api/v1/router.py`
- Modify: `backend/app/api/dependencies.py`
- Test: `backend/tests/integration/api/test_agent_turns.py`
- Test: `backend/tests/integration/api/test_agent_actions.py`

**Spec:**

实现：

- `POST /projects/{project_id}/agent/turns`
- `GET /agent/turns/{turn_id}`
- `GET /agent/actions/{action_id}`
- `POST /agent/actions/{action_id}/confirm`
- `POST /agent/actions/{action_id}/reject`

Turn 分三段执行，禁止在 LLM 调用期间持有事务或行锁：

1. 短事务 A 校验 project/conversation/active context，按 `(project_id, idempotency_key)` get-or-create AgentTurn 并比较 request_hash，短暂锁定 Conversation 并追加一次 user message，然后提交。相同 key 但 payload 不同返回 409 `IDEMPOTENCY_KEY_REUSED`。
2. 事务外原子领取 planning lease，构建 context 并调用 Planner。重复请求命中有效 lease 时返回 202；租约过期时允许重新领取。
3. 短事务 B 锁定 AgentTurn 并核对 lease owner，写入 clarification/answer/error，或创建 proposed AgentAction + action_plan message，然后终结 Turn。重复请求直接返回持久化结果，不再次调用 LLM。

`conversation_id=null` 时，事务 A 创建会话并取首条消息前 30 字作为标题。Planner 失败只把 Turn 标为 failed，不创建 AgentAction 或 Run。

Confirm 只使用服务端持久化 Plan；在一个短事务中锁定 Action，校验来源 Artifact snapshot，并创建 WorkflowRun 与 Action 关联。重复确认返回原 Run；来源变化时 Action→stale 并返回 409 `ACTION_STALE`。并发确认由 active Run partial unique index 和 Action.run_id unique constraint 兜底；约束冲突回滚后重新查询，返回原 Run 或 409 `PROJECT_HAS_ACTIVE_RUN`。Reject 只允许 proposed→rejected。

确认后的 intent→Run action 映射固定为：`create_script→create_script`、`evaluate→evaluate`、`revise_script→revise_script`、`revise_outline→revise_outline`；`explain` 不创建 Run。未知映射返回 `UNSUPPORTED_AGENT_INTENT`。

**TDD anchors:**

- `backend/tests/integration/api/test_agent_turns.py::test_planner_runs_after_initial_transaction_commits`
- `backend/tests/integration/api/test_agent_turns.py::test_duplicate_clarification_turn_returns_original_response`
- `backend/tests/integration/api/test_agent_turns.py::test_reused_idempotency_key_with_different_payload_is_rejected`
- `backend/tests/integration/api/test_agent_actions.py::test_stale_plan_is_blocked_without_creating_run`
- `backend/tests/integration/api/test_agent_actions.py::test_concurrent_actions_cannot_create_two_active_runs`

### Task 5: 持久化 WorkflowDispatcher 与节点恢复

**Depends on:** Task 1

**Estimate:** 3.5d

**Files:**
- Create: `backend/app/application/workflow_dispatcher.py`
- Create: `backend/app/workflows/persistence.py`
- Create: `backend/migrations/versions/0006_durable_workflow_runs.py`
- Modify: `backend/app/db/models/workflow_run.py`
- Modify: `backend/app/api/v1/runs.py`
- Create: `backend/app/cli/checkpoints.py`
- Modify: `Makefile`
- Modify: `backend/app/api/v1/revisions.py`
- Modify: `backend/app/api/v1/exports.py`
- Modify: `backend/app/application/run_service.py`
- Modify: `backend/app/workflows/creation.py`
- Modify: `backend/app/workflows/revision.py`
- Modify: `backend/app/workflows/import_file.py`
- Modify: `backend/app/main.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Test: `backend/tests/integration/api/test_creation_run.py`
- Test: `backend/tests/integration/api/test_revisions.py`
- Test: `backend/tests/integration/workflow/test_creation_workflow.py`
- Test: `backend/tests/integration/workflow/test_dispatcher_recovery.py`

**Spec:**

把 `_active_workers`、`schedule_worker()` 和 `_execute_workflow()` 从 `api/v1/runs.py` 移到 application 层。API 只负责参数校验和创建 Run，进程内 task 只负责唤醒 Dispatcher，不能作为唯一调度事实源。保留 `create_script | evaluate | revise | import | export | platform_smoke` 的请求、状态、FakeLLM fixture、SSE 顺序和错误行为。

迁移为 WorkflowRun 增加 `idempotency_key`、`request_hash`、`lease_owner`、`lease_expires_at` 和 `attempt_count`。`(project_id, action, idempotency_key)` 在 key 非空时唯一；重复 key 的 request_hash 不一致时返回 `IDEMPOTENCY_KEY_REUSED`。对 `status IN ('queued', 'running')` 建立按 project_id 的 partial unique index。RunService 删除进程内幂等字典，数据库约束冲突后查询并返回原 Run。

`0006_durable_workflow_runs.py` 在创建 partial unique index 前检查已有 active Run 冲突，发现同一项目有多条 queued/running 时中止并输出项目 ID，不静默改状态。`make migrate` 在 Alembic 完成后调用 checkpoint setup CLI 创建官方 saver 表；`make migrate-check` 和 `make doctor` 验证 saver 表可读写。应用启动只检查 schema，不在运行期执行 DDL。

Dispatcher 使用 `SELECT ... FOR UPDATE SKIP LOCKED` 原子领取 queued 或租约过期的 running Run，定期续租。应用启动时扫描可领取 Run；多个实例竞争时只有一个实例获得 lease。执行成功后清除 lease 并进入终态；超过最大恢复次数后标记 failed，并写入明确错误码。

引入 `langgraph-checkpoint-postgres`，所有长工作流使用 AsyncPostgresSaver 编译，调用时设置 `thread_id=str(run_id)`。现有 `state_summary` 保留为诊断摘要，不再承担唯一恢复状态。节点完成后由 checkpointer 保存状态；恢复时跳过已完成节点。Artifact 写入继续使用 input hash 幂等，允许节点在提交外部副作用前后安全重试。

Task 6/8 完成前，`available_intents` 不暴露 `revise_script/revise_outline`。Dispatcher 对未知 action 返回 `UNSUPPORTED_ACTION`，不得静默 completed。现有独立修订 API 继续使用 `revise`，行为不变。

**TDD anchors:**

- `backend/tests/integration/api/test_creation_run.py::test_duplicate_idempotency_key_reuses_run_after_service_recreation`
- `backend/tests/integration/workflow/test_dispatcher_recovery.py::test_queued_run_is_claimed_after_service_restart`
- `backend/tests/integration/workflow/test_dispatcher_recovery.py::test_expired_running_lease_resumes_from_last_checkpoint`
- `backend/tests/integration/workflow/test_dispatcher_recovery.py::test_two_dispatchers_cannot_claim_same_run`

### Task 6: 对话式剧本修订工作流

**Depends on:** Task 4, Task 5  
**Estimate:** 1.5d

**Files:**
- Create: `backend/app/workflows/conversational_revision.py`
- Create: `backend/app/workflows/nodes/prepare_conversational_revision.py`
- Modify: `backend/app/workflows/state.py`
- Modify: `backend/app/workflows/revision.py`
- Modify: `backend/app/application/workflow_dispatcher.py`
- Modify: `backend/app/application/revision_service.py`
- Test: `backend/tests/integration/workflow/test_conversational_revision.py`
- Test: `backend/tests/integration/api/test_agent_actions.py`

**Spec:**

对 `revise_script` Action 构建专用子图：`prepare_target -> ensure_evaluation -> revise -> continuity_check -> re_evaluate -> END`。目标由服务端解析的 source script ID 决定，不能由 Planner 任意 UUID 决定。

如果目标剧本没有绑定评估，先仅评估目标集并持久化报告；随后将用户约束写入 RevisionPlan。候选稿保持 draft，连续性通过后提升 valid；失败保留诊断稿并将 Run 标记 needs_review。最终状态必须包含 source/new script、plan、continuity、evaluation 和 diff 所需 Artifact ID，供 lifecycle 生成结果消息。

重复执行使用 source snapshot、prompt version、user constraints 组成幂等输入；不得因重试覆盖原稿或产生相同输入的重复有效版本。

**TDD anchor:** `backend/tests/integration/workflow/test_conversational_revision.py::test_missing_evaluation_is_created_before_user_directed_revision`

### Task 7: 大纲修订 Skill 与影响分析 Tool

**Depends on:** Task 3  
**Estimate:** 1.5d

**Files:**
- Create: `backend/app/domain/outline_revision.py`
- Create: `backend/app/skills/outline_reviser.py`
- Create: `backend/app/tools/outline_impact.py`
- Create: `backend/app/prompts/templates/outline_reviser.md`
- Create: `backend/tests/golden/outline_revision_valid.json`
- Modify: `backend/app/prompts/manifest.yaml`
- Modify: `backend/app/prompts/loader.py`
- Modify: `backend/app/llm/openai_compatible.py`
- Test: `backend/tests/unit/skills/test_outline_reviser.py`
- Test: `backend/tests/unit/tools/test_outline_impact.py`
- Test: `backend/tests/contract/test_prompts.py`

**Spec:**

`OutlineRevisionInput` 包含旧大纲、Story Bible、用户约束和 source outline ID；输出完整 `EpisodeOutlineSet`，不允许只输出 patch。服务端校验集数不变、episode_number 唯一且连续、required characters 可追溯、Story Bible locked facts 未被反转；不变量失败抛出可诊断错误。

`OutlineImpactTool.execute(old, new, dependent_scripts)` 逐字段确定性比较并返回 changed episodes、变化字段、依赖旧大纲的剧本 ID 和 follow-up 建议。相同大纲返回空影响；文本空白差异规范化后不算变化。Tool 不调用 LLM。

**TDD anchor:** `backend/tests/unit/tools/test_outline_impact.py::test_changed_episode_reports_scripts_derived_from_old_outline`

### Task 8: 大纲修订工作流与版本落库

**Depends on:** Task 5, Task 7  
**Estimate:** 1d

**Files:**
- Create: `backend/app/workflows/outline_revision.py`
- Create: `backend/app/workflows/nodes/revise_outline.py`
- Modify: `backend/app/application/workflow_dispatcher.py`
- Modify: `backend/app/application/artifact_service.py`
- Modify: `backend/app/domain/enums.py`
- Test: `backend/tests/integration/workflow/test_outline_revision_workflow.py`
- Test: `backend/tests/integration/artifacts/test_artifact_api.py`

**Spec:**

实现 `revise_outline` Run：加载 source outline 和最新有效 Story Bible，调用 OutlineReviserSkill，先持久化新 outline Artifact，再运行 OutlineImpactTool，把 impact 存入 workflow state/AgentAction result。新 Artifact 的 sources 必须包含旧 outline `revises` 和 Story Bible `references`；旧 Artifact 内容/checksum 不变。

合法输出成为 latest valid；不变量失败的输出保存为 invalid 诊断版本，Run failed，不改变 latest valid。已有 script status 不变；查询结果必须能指出哪些 script 仍引用旧 outline。工作流不调用剧本生成或修订。

**TDD anchor:** `backend/tests/integration/workflow/test_outline_revision_workflow.py::test_outline_revision_creates_new_version_without_rewriting_scripts`

### Task 9: AgentAction 生命周期、Outcome 与一次后续计划

**Depends on:** Task 4, Task 5, Task 6, Task 8  
**Estimate:** 2d

**Files:**
- Create: `backend/app/application/agent_action_lifecycle.py`
- Create: `backend/app/application/agent_outcome_service.py`
- Create: `backend/app/skills/agent_outcome_evaluator.py`
- Create: `backend/app/prompts/templates/agent_outcome_evaluator.md`
- Create: `backend/tests/golden/agent_outcome_partial.json`
- Modify: `backend/app/application/workflow_dispatcher.py`
- Modify: `backend/app/events/publisher.py`
- Modify: `backend/app/application/conversation_service.py`
- Modify: `backend/app/prompts/manifest.yaml`
- Modify: `backend/app/prompts/loader.py`
- Test: `backend/tests/unit/application/test_agent_outcome_service.py`
- Test: `backend/tests/unit/skills/test_agent_outcome_evaluator.py`
- Test: `backend/tests/integration/events/test_agent_action_events.py`
- Test: `backend/tests/integration/api/test_agent_actions.py`

**Spec:**

当 config_snapshot 含 `agent_action_id` 时，Dispatcher 在 Run 状态变化时同步 Action：queued→running→completed/needs_review/failed/cancelled。Lifecycle 从 workflow final state 收集 Artifact ID、Diff、评分变化、连续性状态、大纲影响和错误证据。

AgentOutcomeService 先使用确定性证据生成 `goal_status=achieved|partially_achieved|blocked`、`evidence_artifact_ids`、`score_delta` 和 `remaining_constraints`。只有用户语义约束无法由现有校验器判断时才调用 AgentOutcomeEvaluatorSkill；模型不得改变评分、连续性结果或 Artifact 引用。

当目标为 partially_achieved/blocked、原 Action 的 `replan_depth=0` 且 evaluator 返回白名单意图时，服务端使用当前最新 Artifact 重新解析目标，创建 `parent_action_id=<原 action>`、`replan_depth=1` 的 proposed Action。后续 Action 只展示和等待确认，不自动创建 Run；深度为 1 的 Action 完成后不得继续生成子 Action。

结果回写以 action_id + run_id + terminal status + result hash 幂等。Worker 中断后，GET Action 或后台 reconciliation 补写 Outcome、assistant result message 和可空的后续 action_plan message。`(parent_action_id, replan_depth)` 唯一约束保证重复 reconciliation 不会重复提案。SSE payload 增加 agent_action_id 和 goal_status，现有消费者忽略新字段时仍兼容。

**TDD anchors:**

- `backend/tests/unit/application/test_agent_outcome_service.py::test_deterministic_evidence_is_preferred_over_llm_judgment`
- `backend/tests/integration/api/test_agent_actions.py::test_partial_outcome_creates_one_confirmable_child_action`
- `backend/tests/integration/api/test_agent_actions.py::test_reconciliation_does_not_duplicate_child_action_or_result_message`
- `backend/tests/integration/events/test_agent_action_events.py::test_terminal_run_reconciliation_appends_one_result_message`

### Task 10: 前端 Agent API 契约与数据 Hooks

**Depends on:** Task 4, Task 9

**Estimate:** 1d

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/lib/api-client.ts`
- Create: `frontend/src/hooks/use-agent-conversation.ts`
- Create: `frontend/src/hooks/use-agent-action.ts`
- Test: `frontend/tests/agent-api.test.ts`

**Spec:**

为 Conversation、Message、AgentTurn、AgentAction、AgentOutcome、ActiveArtifactContext 和 ActionResult 添加与 Pydantic 一致的 TypeScript 类型。`agentApi` 支持 createTurn/getTurn/getAction/confirm/reject；`conversationsApi` 支持 create/list/messages。

Hooks 管理当前会话、分页消息、发送幂等 key、Turn planning 202 polling、Action polling/SSE、确认防重复点击和 query invalidation。重复 Turn 使用相同 key 并复用原消息；confirm 返回已有 Run 时不创建重复本地状态；409 ACTION_STALE 显示可恢复错误并保留用户原输入；组件卸载时停止 polling。

**TDD anchor:** `frontend/tests/agent-api.test.ts::confirming_same_action_twice_reuses_run`

### Task 11: 对话式创作工作台

**Depends on:** Task 9, Task 10  
**Estimate:** 2d

**Files:**
- Create: `frontend/src/features/agent/AgentWorkspace.tsx`
- Create: `frontend/src/features/agent/ConversationPanel.tsx`
- Create: `frontend/src/features/agent/MessageList.tsx`
- Create: `frontend/src/features/agent/AgentComposer.tsx`
- Create: `frontend/src/features/agent/ActionPlanCard.tsx`
- Create: `frontend/src/features/agent/ArtifactContextPanel.tsx`
- Modify: `frontend/src/app/projects/[id]/page.tsx`
- Modify: `frontend/src/features/conversation/ChatInput.tsx`
- Modify: `frontend/src/app/globals.css`
- Modify: `frontend/src/app/layout.tsx`
- Modify: `.env.example`
- Test: `frontend/tests/agent-workspace.test.tsx`

**Spec:**

按 `DESIGN.md §13` 实现双栏工作台：左侧会话选择、消息历史、Composer 和内嵌 RunProgress；右侧展示最新 Story Bible/大纲/剧本索引、当前 active context、Action Plan、影响和结果链接。现有 `ChatInput` 的首次创作能力迁移到 AgentComposer，保留目标集数设置；不保留两个同时可提交的输入框。

ActionPlanCard 展示目标、来源版本、约束、步骤、预计影响，并提供确认/拒绝。确认期间按钮禁用；stale、needs_review、failed 都有明确恢复入口。结果消息展示 achieved/partially_achieved/blocked、证据链接、评分变化和尚未满足的约束；可空的后续 Action Plan 紧跟结果消息展示，但不会自动确认。结果链接到现有 Story Bible、outline、script、versions 和 Diff 页面。

桌面 7:5/8:4、平板抽屉、移动端单栏和底部 Composer；支持 Enter 发送、Shift+Enter 换行、可见 focus、`aria-live` 状态播报、44px 触控目标和 reduced motion。空会话展示四个可点击命令示例。

增加 `NEXT_PUBLIC_AGENT_WORKSPACE_ENABLED=true`；设为 false 时项目页渲染原 `ChatInput + RunProgress`，用于无数据迁移的界面回滚。两种模式共用后端 API 和 Artifact，不复制状态。

**TDD anchor:** `frontend/tests/agent-workspace.test.tsx::ambiguous_turn_renders_clarification_without_confirmation_button`

### Task 12: E2E、Agent 评测、文档与退出门禁

**Depends on:** Task 1 至 Task 11

**Estimate:** 2.5d

**Files:**
- Create: `e2e/agent-workspace.spec.ts`
- Create: `backend/tests/evals/agent_commands.json`
- Create: `backend/tests/evals/test_agent_command_eval.py`
- Create: `backend/tests/evals/agent_outcomes.json`
- Create: `backend/tests/evals/test_agent_outcome_eval.py`
- Create: `docs/AGENT_EVAL_REPORT.md`
- Modify: `backend/app/api/v1/runs.py`
- Modify: `backend/tests/integration/api/test_fake_scenario.py`
- Modify: `docs/API_CONTRACT.md`
- Modify: `docs/TEST_PLAN.md`
- Modify: `docs/DEV_PLAN.md`
- Modify: `docs/DEV_LOG.md`

**Spec:**

Playwright 覆盖：首次创作计划→确认→完成；模糊修改→澄清且无 Run；重复 Turn 不重复消息；指定第三集修改→必要评估→新稿→Diff；修改大纲→新版本→受影响剧集警告；重复确认只产生一个 Run；刷新页面恢复消息和进行中的 Run；部分达成→显示证据和剩余约束→提出一个后续计划→再次确认。

`agent_commands.json` 至少 50 条，覆盖五类 intent、中文指代、明确/模糊剧集、active context、冲突约束和越界输入。`agent_outcomes.json` 至少 30 条，覆盖 achieved/partially_achieved/blocked、证据充分性、语义约束、非法后续意图和 replan 深度上限。

CI 用 FakeLLM 验证契约、路由、Turn 幂等、并发确认、lease 接管、checkpoint 恢复和一次后续计划。真实模型评测通过显式 marker 执行，报告记录 provider/model/prompt version、各 intent 的 precision/recall/F1、目标准确率、澄清召回率、goal_status 人工一致率、后续计划可接受率、平均 tokens、P50/P95 延迟和失败分类，不能写模拟数字。

执行并记录：`make lint`、`make typecheck`、`make test`、`make e2e REPEAT=5`。API、Prompt、Schema 或 migration 变化同步文档；按仓库规则更新 DEV_PLAN 进度和 DEV_LOG 的“做了什么/为什么/学到什么”。

**TDD anchors:**

- `e2e/agent-workspace.spec.ts::script_revision_from_chat_produces_version_diff`
- `e2e/agent-workspace.spec.ts::partial_outcome_proposes_one_confirmable_follow_up`

## Task Summary

| # | Task | Depends On | Estimate | Status |
|---:|---|---|---:|---|
| 1 | AgentTurn、AgentAction 与消息持久化 | none | 2d | Done |
| 2 | 项目上下文与预算 | 1 | 1d | Done |
| 3 | Planner Skill | 2 | 1d | Done |
| 4 | Turn/Action Service/API | 1,2,3,5 | 2d | Done |
| 5 | 持久化 Dispatcher 与 checkpoint | 1 | 3.5d | Done |
| 6 | 对话式剧本修订 | 4,5 | 1.5d | Done |
| 7 | 大纲修订与影响 Tool | 3 | 1.5d | Done |
| 8 | 大纲修订工作流 | 5,7 | 1d | Done |
| 9 | Action 生命周期、Outcome 与再规划 | 4,5,6,8 | 2d | Pending |
| 10 | 前端 API 与 Hooks | 4,9 | 1d | Pending |
| 11 | Agent Workspace UI | 9,10 | 2d | Pending |
| 12 | E2E、评测与文档 | 1-11 | 2.5d | Pending |

**Total estimate:** 21 developer-days。加入联调、故障演练和真实模型评测缓冲后，建议按 22～28 个开发人日安排，并拆成 4 个可演示里程碑：

1. **M1（Task 1、5）**：现有 Run API 获得持久化幂等、数据库任务领取和 checkpoint 恢复；旧 UI 行为不变。
2. **M2（Task 2～4）**：开放 explain/create_script/evaluate 的对话、澄清和可确认计划 API；修订意图暂不暴露。
3. **M3（Task 6～9）**：开放剧本/大纲修订，结果包含目标达成判断，并可提出一次需要确认的后续计划。
4. **M4（Task 10～12）**：完整工作台、E2E、恢复演练和可用于简历的真实模型评测报告。

## Self-Review

- Spec coverage：五类意图、全分支 Turn 幂等、短事务、并发约束、checkpoint、Outcome、一次后续计划、UI 和评测均映射到任务。
- Placeholder scan：通过，所有任务均使用可验证的具体描述。
- Type consistency：AgentTurnStatus、AgentActionStatus、AgentIntent、ActionPlan、AgentOutcome、ActiveArtifactContext 在 Task 1 定义，后续统一引用。
- Dependency integrity：无循环依赖；按 Task 1 → 5 → 2 → 3 → 4 → 6 → 7 → 8 → 9 → 10 → 11 → 12 顺序执行，每个 Wave 独立可用。
- Testability：每个任务都有具体 TDD anchor；真实模型指标隔离为手动评测；进程退出和多 Dispatcher 竞争有数据库集成测试。
- Rollback：构建时 Feature Flag 可隐藏 Workspace；旧页面和既有 API 保留。停止新 Agent 流量后可回滚应用代码，AgentTurn/Action、checkpoint 和消息 metadata 作为审计数据保留；执行 destructive downgrade 会丢失这些新增数据，不能称为无损回滚。
