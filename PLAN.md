# 对话式创作 Agent Implementation Plan

> **For agentic workers:** Use `/taku-build` to implement this plan. The build agent should choose sequential, parallel, or hybrid execution unless the user explicitly overrides it.
>
> **Review context:** Scope、architecture 和 UI reviews 位于 `DESIGN.md`。本文档只描述执行内容。
>
> **Build Agent Contract:**
> - **Required:** Goal、Tech Stack、Execution Hints、全部 Tasks（Depends on + Spec + Files）。
> - **Optional:** `DESIGN.md` 中的架构与评审材料。
> - **Skip during execution:** 已完成的范围、架构和 UI 评审。

**Goal:** 将现有项目工作台升级为支持自然语言解释、澄清、修改大纲/剧本、确认执行、版本追踪和结果反馈的受约束创作 Agent。

**Architecture:** 复用 Conversation/Message、Run/SSE、Artifact 和 Revision 基础设施，新增 AgentAction 作为计划确认与审计边界。LLM Planner 只输出结构化意图和目标选择器，服务端负责解析 Artifact、白名单路由、过期检测和工作流执行。

**Tech Stack:** Python 3.11、FastAPI、Pydantic v2、SQLAlchemy Async、Alembic、PostgreSQL、LangGraph、Next.js、React、TypeScript、TanStack Query、Tailwind CSS、pytest、Vitest、Playwright、FakeLLM。

**Depth:** Deep — 跨 `backend/domain/db/application/api/skills/workflows`、`frontend`、`e2e` 和 `docs`，预计 12–15 个开发人日。

## Architecture Overview

```text
AgentWorkspace
  -> Agent Turn API
      -> AgentContextService -> ContextBuilder
      -> AgentCommandPlannerSkill
      -> AgentCommandService -> Message + AgentAction(proposed)
  -> Confirm API
      -> stale/idempotency/active-run guards
      -> WorkflowDispatcher
          -> create / evaluate / conversational revision / outline revision
          -> immutable Artifact + SSE
      -> AgentActionLifecycleService -> result message
```

## Execution Hints

**Suggested mode:** Hybrid

**Wave 1 — Persistence and dispatcher foundation**
- Task 1: AgentAction 与 Message 持久化契约
- Task 5: Worker 调度器从 API 层抽离

**Wave 2 — Context and planning intelligence**
- Task 2: 项目上下文组装
- Task 3: 对话命令 Planner Skill

**Wave 3 — Public command surface and outline capability**
- Task 4: Agent Turn/Action 服务与 API
- Task 7: 大纲修订 Skill 与影响分析 Tool
- Task 10: 前端契约和数据 Hooks

**Wave 4 — Workflow execution**
- Task 6: 对话式剧本修订工作流
- Task 8: 大纲修订工作流

**Wave 5 — Lifecycle and workspace integration**
- Task 9: Run 终态回写与结果消息
- Task 11: 对话式创作工作台

**Wave 6 — Exit gate**
- Task 12: E2E、评测、文档和回归门禁

各 Wave 内只有无文件冲突、无数据依赖的任务允许并行；涉及 `prompts/manifest.yaml`、`runs.py` 或 `api-client.ts` 的任务按依赖顺序执行。

### Task 1: AgentAction 与消息持久化契约

**Depends on:** none  
**Estimate:** 1.5d

**Files:**
- Create: `backend/app/domain/agent_command.py`
- Create: `backend/app/db/models/agent_action.py`
- Create: `backend/app/db/repositories/agent_actions.py`
- Create: `backend/migrations/versions/0002_agent_actions.py`
- Modify: `backend/app/db/models/message.py`
- Modify: `backend/app/db/models/__init__.py`
- Modify: `backend/app/domain/conversation.py`
- Modify: `backend/app/application/conversation_service.py`
- Test: `backend/tests/integration/db/test_agent_actions.py`
- Test: `backend/tests/integration/db/test_migration.py`
- Test: `backend/tests/integration/api/test_conversations.py`

**Spec:**

定义 `AgentIntent`：`create_script | explain | revise_outline | revise_script | evaluate`；定义 `AgentActionStatus`：`proposed | queued | running | completed | failed | cancelled | stale | rejected`。定义 `ActiveArtifactContext`、`ActionTarget`、`ActionStep`、`AgentActionPlan`、`AgentActionResponse`，所有 Schema `extra="forbid"`。`AgentActionPlan.command` 使用按 intent 判别的联合类型：`CreateScriptCommand(user_input, outline_count, script_count)`、`ExplainCommand(target)`、`ReviseOutlineCommand(source_outline_id, constraints)`、`ReviseScriptCommand(source_script_id, episode_number, constraints)`、`EvaluateCommand(scope=project|episode, episode_number)`；只有服务端解析后的 Command 可以持久化。

新增 `agent_actions` 表，字段与状态机遵循 `DESIGN.md §6.3`；`run_id` 可空且一个 Action 最多关联一个 Run，`(project_id, idempotency_key)` 唯一。Message 增加 `kind` 和 `metadata` JSONB；迁移为已有消息回填 `kind=text`、`metadata={}`，downgrade 可无损删除新表和新列。

修复现有 `max(sequence)+1` 并发风险：追加消息时锁定 Conversation 行，并增加 `(conversation_id, sequence)` 唯一约束；冲突最多重试一次。Repository 提供 `get_for_update()`、`find_by_idempotency_key()`、`transition()`，非法状态迁移抛出 `AGENT_ACTION_INVALID_TRANSITION`。

**TDD anchor:** `backend/tests/integration/db/test_agent_actions.py::test_duplicate_confirmation_cannot_attach_two_runs`

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

增加配置默认值：`agent_context_budget_tokens=12000`、`agent_recent_message_limit=12`。使用现有 ContextBuilder 分区与 manifest；当前用户请求和 active target 不能静默截断，超限返回 `CONTEXT_TOO_LARGE`。活动 Artifact 必须属于当前项目，且 type/episode 与请求一致，否则返回 `INVALID_ACTIVE_CONTEXT`。

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

**TDD anchor:** `backend/tests/unit/skills/test_agent_command_planner.py::test_ambiguous_revision_returns_single_clarification_question`

### Task 4: Agent Turn、Action Service 与 API

**Depends on:** Task 1, Task 2, Task 3  
**Estimate:** 1.5d

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
- `GET /agent/actions/{action_id}`
- `POST /agent/actions/{action_id}/confirm`
- `POST /agent/actions/{action_id}/reject`

Turn 在同一事务中校验 project/conversation、追加 user message、调用 context/planner，并按输出追加 clarification/answer 消息或创建 proposed AgentAction + action_plan 消息。`conversation_id=null` 时创建标题取首条消息前 30 字的会话；相同 idempotency key 返回原 Turn/Action，不重复调用 LLM。

Confirm 只使用服务端持久化 Plan；锁定 Action，验证状态、项目、来源 Artifact 是否仍为对应 latest valid、项目是否已有活动 Run。重复确认返回原 Run；来源变化时 Action→stale 并返回 409 `ACTION_STALE`；存在活动 Run 返回 409 `PROJECT_HAS_ACTIVE_RUN`；这些分支都不创建第二个 Run。Reject 只允许 proposed→rejected。

确认后的 intent→Run action 映射固定为：`create_script→create_script`、`evaluate→evaluate`、`revise_script→revise_script`、`revise_outline→revise_outline`；`explain` 不创建 Run。未知映射返回 `UNSUPPORTED_AGENT_INTENT`。

**TDD anchor:** `backend/tests/integration/api/test_agent_actions.py::test_stale_plan_is_blocked_without_creating_run`

### Task 5: WorkflowDispatcher 从 API 层抽离

**Depends on:** none  
**Estimate:** 1d

**Files:**
- Create: `backend/app/application/workflow_dispatcher.py`
- Modify: `backend/app/api/v1/runs.py`
- Modify: `backend/app/api/v1/revisions.py`
- Modify: `backend/app/application/run_service.py`
- Test: `backend/tests/integration/api/test_creation_run.py`
- Test: `backend/tests/integration/api/test_revisions.py`
- Test: `backend/tests/integration/workflow/test_creation_workflow.py`

**Spec:**

把 `_active_workers`、`schedule_worker()` 和 `_execute_workflow()` 从 `api/v1/runs.py` 移到 application 层 `WorkflowDispatcher`，API 只负责参数校验与调用 service/dispatcher。保留 `create_script | evaluate | revise | platform_smoke` 的请求、状态、FakeLLM fixture、SSE 顺序和错误行为，确保现有 API 与 E2E 不变。

Dispatcher 接口为 `schedule(run_id, action, config_snapshot)`；重复 schedule 同一 run_id 不创建第二个 task。预留 `revise_script` 和 `revise_outline` 路由，但在 Task 6/8 完成前对相应 action 返回明确 `UNSUPPORTED_ACTION`，不得静默 completed。现有独立修订 API 继续使用 `revise`，行为不变。

RunService 的 idempotency 从进程内字典改为数据库查询或唯一约束可验证的持久方案；进程重启后相同 key 仍返回原 Run。

**TDD anchor:** `backend/tests/integration/api/test_creation_run.py::test_duplicate_idempotency_key_reuses_run_after_service_recreation`

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

### Task 9: AgentAction 生命周期、终态回写与结果消息

**Depends on:** Task 4, Task 5, Task 6, Task 8  
**Estimate:** 1d

**Files:**
- Create: `backend/app/application/agent_action_lifecycle.py`
- Modify: `backend/app/application/workflow_dispatcher.py`
- Modify: `backend/app/events/publisher.py`
- Modify: `backend/app/application/conversation_service.py`
- Test: `backend/tests/integration/events/test_agent_action_events.py`
- Test: `backend/tests/integration/api/test_agent_actions.py`

**Spec:**

当 config_snapshot 含 `agent_action_id` 时，Dispatcher 在 Run 状态变化时同步 Action：queued→running→completed/failed/cancelled。终态从 workflow final state 收集 Artifact ID、评分变化、连续性状态、大纲影响和可读摘要，保存到 Action.result，并追加 `kind=action_result|error` 的 assistant message。

结果回写必须幂等：以 action_id + run_id + terminal status 生成 result hash；Worker 中断后，GET Action 发现 Run 已终态但无 result 时触发一次 reconciliation。不得重复追加结果消息。SSE payload 增加 agent_action_id，现有消费者忽略该字段时仍兼容。

**TDD anchor:** `backend/tests/integration/events/test_agent_action_events.py::test_terminal_run_reconciliation_appends_one_result_message`

### Task 10: 前端 Agent API 契约与数据 Hooks

**Depends on:** Task 4  
**Estimate:** 0.75d

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/lib/api-client.ts`
- Create: `frontend/src/hooks/use-agent-conversation.ts`
- Create: `frontend/src/hooks/use-agent-action.ts`
- Test: `frontend/tests/agent-api.test.ts`

**Spec:**

为 Conversation、Message、AgentTurn、AgentAction、ActiveArtifactContext 和 ActionResult 添加与 Pydantic 一致的 TypeScript 类型。`agentApi` 支持 createTurn/getAction/confirm/reject；`conversationsApi` 支持 create/list/messages。

Hooks 管理当前会话、分页消息、发送幂等 key、Action polling/SSE、确认防重复点击和 query invalidation。confirm 返回已有 Run 时不创建重复本地状态；409 ACTION_STALE 显示可恢复错误并保留用户原输入；组件卸载时停止 polling。

**TDD anchor:** `frontend/tests/agent-api.test.ts::confirming_same_action_twice_reuses_run`

### Task 11: 对话式创作工作台

**Depends on:** Task 9, Task 10  
**Estimate:** 1.5d

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

ActionPlanCard 展示目标、来源版本、约束、步骤、预计影响，并提供确认/拒绝。确认期间按钮禁用；stale、needs_review、failed 都有明确恢复入口。结果消息链接到现有 Story Bible、outline、script、versions 和 Diff 页面。

桌面 7:5/8:4、平板抽屉、移动端单栏和底部 Composer；支持 Enter 发送、Shift+Enter 换行、可见 focus、`aria-live` 状态播报、44px 触控目标和 reduced motion。空会话展示四个可点击命令示例。

增加 `NEXT_PUBLIC_AGENT_WORKSPACE_ENABLED=true`；设为 false 时项目页渲染原 `ChatInput + RunProgress`，用于无数据迁移的界面回滚。两种模式共用后端 API 和 Artifact，不复制状态。

**TDD anchor:** `frontend/tests/agent-workspace.test.tsx::ambiguous_turn_renders_clarification_without_confirmation_button`

### Task 12: E2E、Agent 评测、文档与退出门禁

**Depends on:** Task 1–Task 11  
**Estimate:** 1.5d

**Files:**
- Create: `e2e/agent-workspace.spec.ts`
- Create: `backend/tests/evals/agent_commands.json`
- Create: `backend/tests/evals/test_agent_command_eval.py`
- Create: `docs/AGENT_EVAL_REPORT.md`
- Modify: `backend/app/api/v1/runs.py`
- Modify: `backend/tests/integration/api/test_fake_scenario.py`
- Modify: `docs/API_CONTRACT.md`
- Modify: `docs/TEST_PLAN.md`
- Modify: `docs/DEV_PLAN.md`
- Modify: `docs/DEV_LOG.md`

**Spec:**

Playwright 覆盖：首次创作计划→确认→完成；模糊修改→澄清且无 Run；指定第三集修改→必要评估→新稿→Diff；修改大纲→新版本→受影响剧集警告；重复确认只产生一个 Run；刷新页面恢复消息和进行中的 Run。

`agent_commands.json` 至少 50 条，覆盖五类 intent、中文指代、明确/模糊剧集、active context、冲突约束和越界输入。CI 用 FakeLLM 验证契约和路由；真实模型评测通过显式 marker 执行，报告必须记录 provider/model/prompt version、意图准确率、目标准确率、澄清召回率、平均 tokens、P50/P95 延迟和失败分类，不能写模拟数字。

执行并记录：`make lint`、`make typecheck`、`make test`、`make e2e REPEAT=5`。API、Prompt、Schema 或 migration 变化同步文档；按仓库规则更新 DEV_PLAN 进度和 DEV_LOG 的“做了什么/为什么/学到什么”。

**TDD anchor:** `e2e/agent-workspace.spec.ts::script_revision_from_chat_produces_version_diff`

## Task Summary

| # | Task | Depends On | Estimate | Status |
|---:|---|---|---:|---|
| 1 | AgentAction 与消息持久化 | none | 1.5d | Pending |
| 2 | 项目上下文与预算 | 1 | 1d | Pending |
| 3 | Planner Skill | 2 | 1d | Pending |
| 4 | Turn/Action Service/API | 1,2,3 | 1.5d | Pending |
| 5 | WorkflowDispatcher 抽离 | none | 1d | Pending |
| 6 | 对话式剧本修订 | 4,5 | 1.5d | Pending |
| 7 | 大纲修订与影响 Tool | 3 | 1.5d | Pending |
| 8 | 大纲修订工作流 | 5,7 | 1d | Pending |
| 9 | Action 生命周期回写 | 4,5,6,8 | 1d | Pending |
| 10 | 前端 API 与 Hooks | 4 | 0.75d | Pending |
| 11 | Agent Workspace UI | 9,10 | 1.5d | Pending |
| 12 | E2E、评测与文档 | 1–11 | 1.5d | Pending |

**Total estimate:** 14.75 developer-days，建议按 3 个可演示里程碑交付：

1. **M1（Task 1–5）**：对话、澄清、解释和可确认计划 API 可独立使用。
2. **M2（Task 6–9）**：剧本/大纲修改可执行、可追踪、可恢复。
3. **M3（Task 10–12）**：完整工作台、E2E 和可用于简历的评测报告。

## Self-Review

- Spec coverage：设计中的五类意图、确认、过期、幂等、版本、影响、UI 和评测均映射到任务。
- Placeholder scan：通过，所有任务均使用可验证的具体描述。
- Type consistency：AgentIntent、AgentActionStatus、ActionPlan、ActiveArtifactContext 在 Task 1 定义，后续统一引用。
- Dependency integrity：无循环依赖；Task 1/5、Task 4/7/10、Task 6/8 可按 Wave 并行。
- Testability：每个任务都有具体 TDD anchor；真实模型指标明确隔离为手动评测。
- Rollback：Feature Flag 可隐藏 Workspace；新数据表可独立 downgrade；Artifact 不可变，旧页面和现有 API 保留。
