# DramaAgent 持续共创工作台：分阶段实施方案

日期：2026-09-12。状态：待实施的方案建议。依据当前仓库 `de231ef`、已确认的“个人创作者与 AI 持续共创”场景，以及[上一轮评审](/Users/kennywu/Documents/3-VibeProject/product/drama_agent/docs/AGENT_NATIVE_WORKBENCH_REVIEW.md)。本轮只新增方案文档，未改应用、未迁移数据库、未运行应用或测试、未调用真实模型。

**推荐按四个可独立交付的阶段推进，共 26 张任务卡。先让作者能顺畅读稿和判断改稿，再建立采用基线、局部修改和剧情状态，最后扩展自主执行。保留现有技术栈，不先更换 Agent 框架。**

快速阅读：[阶段一](#stage-1) · [阶段二](#stage-2) · [阶段三](#stage-3) · [阶段四](#stage-4)。每阶段包含文件、接口、迁移、失败路径、测试与回退。

## 总体交付与排期

| 阶段 | 作者得到的能力 | 核心实施路径 | 阶段退出门 | 估算 |
|---|---|---|---|---|
| 一：同屏共创 | 同页读稿、讨论、看证据、导入原稿和固定版本导出 | 复用正文/评估/Diff 组件，打通确切阅读上下文，补继续请求和结果回放 | 连续四批续写与请求重放正确；解释有原文；历史下载字节一致 | 10–15 人日 |
| 二：版本与局改 | 候选与采用分离，手改/Agent 局改，比较、采用和切回 | 两张表保存采用头和操作收据，稳定 scene_id，共用编辑写入口 | 未采用稿不污染默认创作；范围外正文不变；并发采用和依赖校验正确 | 8–12 人日 |
| 三：剧情与设定 | 多集续写记得关系、伏笔和人物知识；可以修改并采用设定 | 复用 Summarizer/reducer，保存版本化摘要与剧情状态，增加设定门 | 分批/重启上下文一致；正文不因摘要失败丢失；未来计划不冒充人物已知 | 6–9 人日 |
| 四：持续任务 | 同一目标跨轮执行，能中途提意见、暂停、恢复和管理预算 | Action 同表演进为 Task；一个 Task 多个 Run；受限操作与持久账本 | 跨 3 个 Run 保持目标/版本/预算；重启不重复采用；停止和反馈真实生效 | 10–14 人日 |

估算按熟悉仓库的单人全栈工程工作日计算，含必要测试、迁移演练和界面接线；不含环境配置、模型试用等待和公开发布。串行合计约 **34–50 人日**。上一轮 5–8 / 5–8 / 4–7 / 6–10 日是方向粗估，本轮补入完整恢复、并发、旧数据兼容后以上表为准；这不是交付日期承诺。

最小可行选项是先完整交付阶段一，随后用一次真实创作流程判断是否继续。阶段二能独立改善版本控制；阶段三仍运行在现有 Action/Run 上；只有第四阶段更换任务语义。不能为了缩工期把采用隔离、幂等或恢复校验移到下一阶段。

任务顺序：`W1-01…07 → W2-01…06 → W3-01…07 → W4-01…06`。阶段内按各卡依赖执行；每卡完成后保持已开放入口可用，跨层切换只在该阶段的读写合同和退出门满足后开放。本文涉及超过 8 个文件，跨前后端、数据库和工作流；这是明确的分阶段重构，不是一次页面改版。

## 固定架构与跨阶段约定

```text
作品导航 / 正文画布 / 常驻 Agent
        │ 确切 Artifact + 场景 + 作者意见
        v
现有 API 与应用服务 ──────────────> 固定版本导出文件
        │
阶段1–3：Turn / Action      阶段4：Turn / Task（沿用 Action 表）
        │                                  │
        └──────── WorkflowRun <─────────────┘
                    │ 一个执行段，一个 run_id checkpoint
                    v
           现有 LangGraph 专业工作流
             │                    │
        不可变作品候选       评估 / 摘要 / 剧情状态证据
             │                    │
             └──── 精确来源引用 ────┘
                    │ 作者采用 / 已有明确采用授权
                    v
          PostgreSQL 采用头 + 操作收据
          DB Event ──> Redis 通知 ──> 页面刷新
```

1. **正文不可变。** `valid` 是结构/领域校验结果；“采用”是独立指针关系；质量与目标达成必须依附确切版本的证据。旧稿、旧引用与旧导出不回写。
2. **阅读选择稳定。** URL 中的 Artifact ID 优先于新生成结果；消息、任务和采用变化不会自动切走作者正在阅读的稿件。场景编号在阶段一仅作版本内锚点，阶段二升级为稳定 ID。
3. **输入与成果分开。** 阶段二统一 `ArtifactWorkset`：Action.plan.workset 和 Run.config_snapshot.workset 为冻结输入，Run.state_summary.workset 为当前候选集合。阶段四 Task.plan.workset 是持续任务当前集合；必须以 revision 校验后更新，不能形成第二份 Run 输入真相。
4. **采用事务统一。** 阶段二仅新增 `artifact_heads` 与 `artifact_operations` 两张表；后者保存采用/切回/候选隐藏恢复的幂等收据。阶段三影响记录、阶段四 Task 决策都复用同一采用事务，不再建立平行采用接口。
5. **核验诚实。** 逐项检查固定为 `satisfied / unsatisfied / unverified`。执行完成或评分上涨不自动表示创作要求已实现；缺证据不触发无限改稿。全文语义与主观剧作质量仍须作者判断。
6. **技术失败不伪装成功。** 可选参考检索失败允许降级；确切正文、锁定事实或必要剧情状态缺失则停止依赖生成。已经保存的候选保留，后续恢复从明确剩余工作开始。

最脆弱的前提是：现有专业 Skill 的单集产出值得作者继续打磨。如果实际试写仍需要整篇重写，同屏与自主循环只能改善操作，无法证明内容价值。因此阶段一结束即进行作者试用；阶段四只在前三阶段的版本和证据正确后启动。模型不可用时，阅读、手动编辑、采用和历史导出仍应可用。

### 与现有规则的关系

`CLAUDE.md:25` 要求未经授权不跨模块重构；用户本轮已授权整体架构方案，因此本文可列跨层任务，当前仍不执行应用改动。[DESIGN.md](/Users/kennywu/Documents/3-VibeProject/product/drama_agent/DESIGN.md:14) 的旧范围要求每次写入先确认、后续计划不能自动执行，并排除聊天修改 Story Bible：阶段二的直接人工保存、阶段三的设定修订、阶段四的目标级授权将分别替代这些旧条款，实施对应阶段时同步权威 DEV_PLAN 与设计文档。阶段一保留现行生成确认规则；明确“识别并导入/基于材料创作”按钮本身是展示具体范围后的确认。数据库事实源、不可变作品、服务端白名单和 FakeLLM 自动化测试继续有效。

### 官方能力的复用依据

继续使用每个 Run 的 PostgreSQL checkpoint 和现有专业子图，采用官方的持久化语义；作者审阅门结束当前执行段，不把任务 ID 当成所有 Run 共用的 checkpoint thread_id。[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)。并发采用采用短事务与行锁，不在模型调用期间持锁。[PostgreSQL 17 行锁](https://www.postgresql.org/docs/17/explicit-locking.html)、[SQLAlchemy 事务](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)。导航复用 Next URL 状态，后台回收复用 FastAPI lifespan。[Next useSearchParams](https://nextjs.org/docs/app/api-reference/functions/use-search-params)、[FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)。

### 迁移与新增界面总账

| 交付阶段 | 计划迁移文件（均位于 backend/migrations/versions） | 具体变更 |
|---|---|---|
| 一 | `0011_run_stage_continuation.py` | stage_generation、continue Action 部分唯一约束 |
| 二 | `0012_artifact_selection.py` | heads/operations、候选来源与幂等字段、project selection_version |
| 三 | `0013_story_state_dedup.py` | v2 剧情证据 input_hash 的局部唯一索引 |
| 三 | `0014_story_bible_revision_intent.py` | 扩展现有 Action intent 约束 |
| 四 | `0015_agent_tasks.py` | 双执行版本、Task revision/停止请求、Task–Run/Turn 关系、lease_generation |
| 四 | `0016_task_llm_budget.py` | llm_calls 的 Task 关联、调用预留和幂等结算 |

当前 head 是 0010。以上为连续交付的具体文件名；若实施前别人新增迁移，只做必要的父 revision/编号对齐，数据合同不变。schema 扩展均先部署兼容读法，再切换入口；旧项目采用头通过受控 activation 建立，不在升级时调用模型。

新增运行服务 **0**、第三方 SDK **0**、常驻 Agent 角色 **0**、产品环境变量 **0**、必需第三方账户 **0**。阶段二新增一个维护命令 `app.cli.artifact_heads`，默认 dry-run，仅在明确指定项目并加 `--apply` 时激活该项目。旧项目激活前要求所有 v1 Run 达到完成/取消/失败终态；尚在审阅门的 Run 先用兼容入口完成或保留项目 v1，不强制转换 checkpoint。

已有配置继续使用：`LLM_API_BASE / LLM_API_KEY / LLM_*_MODEL` 用于真实创作；`EMBEDDING_*` 用于已启用的参考检索；数据库与 Redis 沿用仓库配置。自动化验收使用 FakeLLM/FakeEmbedder，不需要这些付费凭证，不读取或输出密钥。本轮已确认 uv/pnpm 可用，Docker 不可用、现有 E2E 的 setsid 在本机缺失；阶段一包含脚本可移植性修复，完整集成验收须在 Docker 可用的开发/CI 环境执行。没有新增 MCP 或外部 API 依赖需要用户配置。

## 共用验证、发布和回退规则

- **先做小范围可失败的检查。** 每卡以列出的目标测试为主，使用现有 pytest/Vitest/Playwright，不新建测试框架。故障、并发或恢复变化必须用真实 PostgreSQL 集成测试，不能用 mock 锁证明事务正确。
- **阶段收口命令。** 依赖就绪后运行 `make ci`（已包含 lint、typecheck、后端覆盖率）与 `cd frontend && pnpm test`；有迁移运行 `make migrate`、`make migrate-check` 及混合旧数据测试；正式 E2E 运行 `make e2e REPEAT=5`，其中一轮冒烟可先用 `REPEAT=1`。不重复执行已经被 make ci 覆盖的相同检查。E2E 必须只创建/销毁本轮隔离数据库与进程，不能清理日常开发数据。
- **人工和渲染验收。** 使用固定 Fake 剧情，在 1440×900、1024×768、390×844 检查读稿、差异、输入、错误恢复与键盘焦点；记录真实截图。组件测试不代替渲染，Fake 内容不代替真实创作质量。
- **真人创作试用。** 实施负责人准备同一项目的三条任务：从想法写一集样稿、单场改稿并保留一句台词、续写两集并保持角色知识。作者完成审阅和采用，记录需要澄清/重新指定对象/整段重写的位置与未满足要求。只有已有预算授权时才运行真实 provider；不把这项混入 CI。
- **文档和发布。** 实施各卡同步 API/类型、Prompt版本与固定样例；收口写 DEV_PLAN 验收证据和 DEV_LOG，修复项写 TROUBLESHOOTING。提交、push、PR、合并和公开发布按用户实际授权执行；本次方案编写不触发这些动作。
- **回退原则。** 可以关闭新入口、退回兼容 UI；一旦新语义数据已产生，必须保留能够读取 heads、v2 内容和 v2 Task 的兼容后端。优先通过新的 restore 操作恢复采用版，不删 Artifact 或流水。阶段二以后不能承诺“随时部署任何旧二进制且不处理数据”；各阶段卡片注明具体限制。

数据量扩大十倍时，采用和候选列表使用分页、按项目/类型/集数索引，剧情状态只派生需要的后缀；每项目一个活动 Run 继续作为明确的并发上限。先测量查询与生成延迟，再考虑细化锁或增加 worker。无需提前增加图数据库、跨项目调度平台或语义自动合并。

---

<a id="stage-1"></a>

## 阶段一：同屏共创与现有链路可信化

### 目标、边界和已锁定选择

交付目标：作者在同一页面读稿、提意见、确认本轮改稿并查看生成的新版本；Agent 解释具体剧情时读取确切原文；无法核实的要求明确显示待判断；下载过的历史交付保持字节一致；聊天与按钮续写至少四个批次且刷新、重试不会多写一批。

本阶段仍以 latest valid 作为默认当前稿，界面称“最新有效稿”，不称“采用稿”。候选/采用指针、scene_id、局部写入与人工编辑属于阶段二；Story Bible 修改与剧情状态属于阶段三；持久 Task、有界自主循环、跨重启累计预算与新授权模型属于阶段四。本阶段保留现行写操作确认规则，不在 UI 中提前承诺“只改这场，其他内容绝不改变”。场景定位仅用于阅读、解释和表达修改目标，现有整集修订继续如实展示影响范围。

最小路径是复用正文组件、URLSearchParams、Next navigation、原生文件输入、现有 Artifact/Run/Event/导出服务。新增运行服务、依赖、账号、环境变量均为 0；确切文件总数超过 8 个，跨 API/领域/前端/测试，不能伪装成一次页面调整。仅新增必要的作品画布、URL 状态 hook、解释 Skill/Prompt、证据视图和 E2E 进程辅助脚本。

第一阶段最脆弱的假设是“解释与结果可信后，同屏阅读能减少作者返工”。因此本阶段优先把确切稿件、原文证据和失败状态做实，不以新增对话样式或提高评分代替验收。

当前硬约束继续执行：PostgreSQL 为事实源；Artifact 不可变；Pydantic 校验；生产模型不进入自动化 CI；不在 LLM 调用期间持有数据库事务。`CLAUDE.md:25` 的未经授权不跨模块重构与本方案跨层范围存在表面冲突：用户已明确允许整体重构并要求分阶段方案；实施时把本阶段任务卡写入权威开发契约，执行仍一次一个任务。

### 顺序、复用关系与估算

推荐顺序：W1-01 → W1-02 → W1-03 → W1-04 → W1-05 → W1-06 → W1-07。W1-01 独立改善旧工作台；后续每张卡完成后现有已交付路径保持可用。以下估算针对熟悉仓库的一名全栈开发者，含指定测试、不含环境安装和真实用户试用等待；合计约 10—15 人日。原评审 5—8 人日只能覆盖 UI 与窄修，纳入续跑幂等、原文解释、固定交付及上传后应调整估算。

| 任务 | 交付 | 依赖 | 估算 |
|---|---|---|---|
| W1-01 | 修复同一 Run 分批续跑、阶段幂等与结果归属 | 无 | 2—3d |
| W1-02 | 作品画布与 Agent 同屏、URL 固定阅读/会话状态 | W1-01 | 2—3d |
| W1-03 | 明确目标直达、解释读取正文并引用确切版本 | W1-02 | 2—3d |
| W1-04 | 运行完成与目标验证分开、证据可展开定位 | W1-03 | 1—1.5d |
| W1-05 | 后端固定版本导出、服务端交付历史 | W1-02 | 1—1.5d |
| W1-06 | TXT/DOCX 原稿入口接已有导入与分阶段创作 | W1-01、02、05 | 1—1.5d |
| W1-07 | Mac/Linux E2E 进程兼容、整阶段验收和文档 | W1-01—06 | 1—1.5d |

```text
URL 阅读状态 ───> 作品画布 ─────> ActiveArtifactContext
     │                │                   │
     │                └── 证据/Diff定位    v
     └── 会话与Run ───────────────> Agent Turn
                                  │
                         目标解析 + 确切正文解释
                                  │
                           既有 Action/Run
                                  │
                         Event/SSE + Artifact
                                  │
                  Outcome证据 / 固定文件导出 / 导入结果
```

### W1-01：修复分批继续与恢复收据

**目标**：旧工作台也能可靠完成“聊天创建 → 大纲门 → 聊天/按钮连续写四批”；成功阶段推进不耗尽故障重试，旧请求重放不能触发下一批，历史 Action 结果不被新批次覆写。

**当前证据**：`agent_actions.run_id` 全局唯一在 `backend/app/db/models/agent_action.py:24`；continue 分支将新 Action 关联原 Run 在 `backend/app/application/agent_command_service.py:536-568`；每次领取都会增加 attempt 在 `workflow_dispatcher.py:103-115`；`last_synced_phase` 仅靠状态/门类型不足以区分重复 scripts 门，见 `agent_action_lifecycle.py:63-70,123-131,278-282`。当前 `RunService.continue_gated_run` 没有阶段世代与请求收据，见 `run_service.py:240-316`。

**实施内容**：

- 保留现有同一 Run 与 run_id checkpoint 继续机制，暂不引入 Task 或下一段新 Run。新增 `WorkflowRun.stage_generation` 非负整数列，存量和首次执行为 0；只有合法确认门继续才加 1，故障 retry/租约重领不增加。checkpoint 输入、终态 state、SSE、结果消息都携带该值，不能被旧 checkpoint 值覆盖。
- `agent_actions.run_id` 改为非 continue Action 的部分唯一索引：一个 Run 最多一个创建它的非 continue Action；continue 审计 Action 可以关联该 Run。当前源码搜索未发现 `WHERE AgentAction.run_id` 后 `scalar_one` 的查询；实施时仍对所有 Run→Action 查询逐个核对，当前主要入口是 Run.config_snapshot.agent_action_id 与 getAction→reconcile。禁止仅删约束就结束。
- 本阶段执行所有者固定为 `Run.config_snapshot.agent_action_id`。continue 计划确认后的新 Action 成为当前所有者；按钮或确定性聊天快捷继续沿用原所有者，不制造仅为归属而存在的新 Action。`finalize/reconcile` 发现传入 action_id 不是当前所有者时，保留该 Action 已存结果，不读取当前 Run 终态覆盖它。
- 按 Run 行锁串行确认，记录已接受继续请求的持久收据；复用 WorkflowEvent，新增 `run.continue_accepted` 事件，payload 保存 idempotency_key、规范化请求哈希、expected_generation、accepted_generation、batch_size 和当时接受响应。查询收据和阶段校验在同一短事务，事件与 Run/Action 变更一起提交，提交后才 SSE 通知/唤醒。无 LLM 调用。
- 正常 gated continue 在原子入队时将本批 `attempt_count` 置 0；失败 retry 不清零。此前 claim 事件与每批 generation 保留审计。写到目标集数时到 completed，不产生一个可继续但无剩余集数的门。
- 幂等终态键改为 `stage_generation + run.status + stage_gate`；Action.last_synced_phase 与结果消息查找使用同一键。修复同一 Action 连续两个 scripts 门的消息吞并。
- 同一继续入口正确处理“下一集/五集/全部”：`batch_size=null` 必须把 script_count 恢复为项目目标，去掉 stop_after=scripts，不能沿用上一批终点；仅清除与下一批有关的 completed_nodes/检查点字段，已完成剧本引用保留，不重生已写集。新大纲选择仍按当前有效稿规则，阶段二 才切 adopted。

**API 增量**：`POST /runs/{run_id}/continue` 请求保留 batch_size，并要求新客户端传 `expected_stage_generation`、`idempotency_key`；`RunResponse`/AgentRunSnapshot/SSE 新增 `stage_generation`。ContinueCommand 持久化 expected_stage_generation，计划确认幂等键使用 action ID；快捷聊天使用当前 Turn 的幂等键。相同键同请求返回原接受收据，不新增阶段；相同键不同参数 409 IDEMPOTENCY_KEY_REUSED；不同键但旧阶段四09 RUN_STAGE_STALE。旧计划缺 generation 时从该计划创建时的门快照恢复，无法可靠恢复则返回 stale，不能绑定到当前新门。旧裸 continue 请求缺阶段信息返回 422 并提示刷新工作台；API_CONTRACT 明示这是为防额外生成而进行的版本化客户端升级。

**文件**：修改 `backend/app/db/models/workflow_run.py`、`backend/app/db/models/agent_action.py`、`backend/app/db/repositories/agent_actions.py`、`backend/app/application/run_service.py`、`backend/app/application/agent_command_service.py`、`backend/app/application/agent_action_lifecycle.py`、`backend/app/application/workflow_dispatcher.py`、`backend/app/domain/agent_command.py`、`backend/app/workflows/creation.py`、`backend/app/api/v1/runs.py`、`backend/app/api/v1/agent.py`、`frontend/src/types/api.ts`、`frontend/src/lib/api-client.ts`、`frontend/src/features/agent/ActionPlanCard.tsx`。新增 `backend/migrations/versions/0011_run_stage_continuation.py`（当前 head=0010，按交付顺序固定新 revision）。继续收据查询放入既有 RunService，不新增仓储文件。

**测试**：扩展 `backend/tests/integration/api/test_run_continue.py`、`test_agent_actions.py`、`test_agent_shortcut.py`、`test_e2e_agent_path.py`；扩展 `backend/tests/integration/workflow/test_agent_action_phases.py`、`test_dispatcher_recovery.py` 和 `backend/tests/integration/db/test_agent_actions.py`。必须从真实 API 建 create_script Action 开始，不只播种孤立 gated Run。新增断言：四批完成；第 N 批仍能有限重试；过期 continue 在 N+1 门重放不多写；并发 button/chat 只有一个胜者；当前 continue Action 回写而旧 Action 冻结；同 action 两个 scripts 门各有一条结果且重放无重复；最后批次全部继续写完剩余；取消/failed/非门 needs_review 不可继续。

**验证命令**：在 backend 目录 `uv run pytest tests/integration/api/test_run_continue.py tests/integration/api/test_agent_actions.py tests/integration/api/test_agent_shortcut.py tests/integration/api/test_e2e_agent_path.py tests/integration/workflow/test_agent_action_phases.py tests/integration/workflow/test_dispatcher_recovery.py tests/integration/db/test_agent_actions.py`；`uv run alembic upgrade head` 后执行迁移用例 `tests/integration/db/test_migration.py`。

**回滚**：不删 Run、Action 或 Event。数据库已有多个 continue 关联后不能直接恢复旧 run_id 全唯一约束；迁移 downgrade 检测此条件并拒绝破坏性回滚。可回退 UI 到既有布局，但保留 W1-01 兼容后端和请求适配。关闭新 continue 计划的生成入口后让已经接受的批次结束，再发布修复版本；不把旧后端部署到新 generation Run 上。

### W1-02：同屏作品画布与 URL 状态

**目标**：同屏读稿、看评估/Diff、聊天；刷新与浏览器后退恢复确切作品版本、场景、会话，打开作品即设置当前上下文。

**实施内容**：

- `AgentWorkspace` 改为作品导航、作品画布、常驻会话三区。正文最大面积；窄屏使用作品/Agent 两个原生 tab 或按钮，切换不卸载会话数据；不另加“项目上下文选中”操作。
- 画布复用 `StoryBibleView`、`OutlineListView`、`ScriptView`、`EvaluationPanel`、`DiffView`。从当前详情页抽取数据组装，不复制第二套正文渲染。查看评估时匹配 `source_script_artifact_id`，只有其它版本评估则显示其来源并禁用跨版本场景跳转；不把“最新评估”静默绑到当前历史稿。
- URL 字段固定为 `artifact=<uuid>`、`scene=<positive number>`、`conversation=<uuid>`、`panel=read|evaluation|diff|exports|sources`、`compare=<base artifact uuid>`、`run=<uuid>`。未传 artifact 时只在首次加载选择最新可读作品并 replace 到确切 ID；切稿 push，展开工具 panel replace；消息到来不改用户正在阅读的稿件。新稿完成显示“打开本轮新稿”按钮，点击后才切换。scene 是确切版本中的只读锚点，阶段二 再增 scene_id。
- URL 是可恢复导航事实，TanStack Query 是服务端事实缓存；不把 Artifact 内容再保存到 localStorage。输入草稿按 project+conversation 保存在 sessionStorage，保存发生于输入，成功发送才清掉；切会话不残留另一会话的 activeActionId/失败内容/发送中状态。未发送文本不因“去看 Diff”丢失。
- 项目所有 queued/running Run 从现有项目 Runs 列表识别，状态区不依赖“最近一条计划卡”。刷新遇到导入/导出 Run 同样显示真实进度。每次 generation 与 Run 终态刷新对应作品、消息和项目计数；去掉页面与 AgentWorkspace 双重订阅同一 Run 的重复来源。
- 原 `/story-bible`、`/outline`、`/scripts/[episode]`、`/versions`、`/exports` 路由保留兼容入口，解析用户选择的确切 Artifact/对比 ID 后导向同一工作台 URL；知识库管理也可先复用为 sources panel。不能把旧深链接统一送回一个空白首页。

**API 增量**：读取仍复用 `GET /artifacts/{id}`、项目 Artifact 列表、links/diff、conversations、runs。`GET /artifacts/{id}` 支持可选 project_id 参数，工作台固定传当前项目并在服务端校验归属；请求跨项目返回 404/归属错误且不渲染正文。当前 Turn.active_context 从实际已加载 Artifact 的 id/type/episode/version/checksum 派生；W1-03 增 scene_number 前，scene 暂不送给模型。

**文件**：修改 `frontend/src/features/agent/AgentWorkspace.tsx`、`ArtifactContextPanel.tsx`、`ConversationPanel.tsx`、`AgentComposer.tsx`、`frontend/src/hooks/use-agent-conversation.ts`、`frontend/src/app/projects/[id]/page.tsx`、上述五个详情 page、`frontend/src/features/scripts/ScriptView.tsx`、`frontend/src/lib/api-client.ts`、`backend/app/api/v1/artifacts.py`。新增 `frontend/src/features/agent/ArtifactCanvas.tsx`、`frontend/src/hooks/use-workspace-location.ts`。复用 `frontend/src/features/episodes/EpisodeNav.tsx` 的导航表现，增加场景子项不建设完整树库。

**失败/边缘**：非法 UUID/scene 清除对应选择并提示，不能尝试猜最新稿；旧版本仍可阅读/解释。该稿不是 latest valid 时“修改”提示切到最新稿后继续，保留旧版本查阅，不静默改另一稿。空项目仍可直接输入 Idea；读取失败有局部重试且输入不消失。移动端焦点返回原触发按钮，键盘能遍历导航、画布工具与 Composer。抽屉支持 Escape、焦点边界和可见关闭按钮。

**测试**：扩展 `frontend/tests/agent-workspace.test.tsx`、`script-detail-nav.test.tsx`、`script-evaluation.test.tsx`、`diff-view.test.tsx`；新增 `frontend/tests/workspace-location.test.tsx`（URL/back/refresh/未知参数/跨项目拒绝/草稿隔离）。`e2e/agent-workspace.spec.ts` 增同一页看稿→发送修改→确认→打开实际新稿→比较，断言 project pathname 不变；历史稿+旧评估不可错误定位。选择的 Artifact ID、发送的 active_context 和打开的证据链接三者一致。

**验证命令**：frontend 目录 `pnpm exec vitest run tests/agent-workspace.test.tsx tests/workspace-location.test.tsx tests/script-detail-nav.test.tsx tests/script-evaluation.test.tsx tests/diff-view.test.tsx`。W1-07 使用 FakeLLM 完成 E2E 与 1440×900、1024×768、390×844 渲染检查。

**回滚**：恢复页面布局即可，URL 字段为兼容增量、无作品数据迁移。现有 `NEXT_PUBLIC_AGENT_WORKSPACE_ENABLED=false` 仍可显示 legacy UI，但 W1-01 继续请求适配不能一起回退。固定版本链接仍应被兼容路由解析。

### W1-03：明确目标解析与有原文的解释

**目标**：“修改第 3 集剧本”无需先选上下文；“这场为什么突然翻脸”读取当前确切稿件/场景回答，并能打开原文依据。

**实施内容**：

- `_preflight_clarification` 只对真正缺目标/多义/冲突/越界指令追问。明确业务对象+集数可由服务端解析，支持阿拉伯数字与常见中文集数；对象明确为大纲不需要先选大纲。不要把自然语言目标识别压成一串只有固定样例能通过的正则，复杂但可表达的请求允许 Planner 产出 selector，再在服务端校验。
- 优先级固定：明确文本指定的对象/集数优先于不匹配的页面上下文；文本仅指“这里/当前稿”则用已加载的 active context；同一目标的历史版本使用 active ID，只读解释允许，写作仍按现行 latest 检查返回 stale。多目标修改不偷偷选第一集，说明本阶段一次改一个目标并追问选择。Artifact ID/项目归属与版本只能服务端解析，模型仍不提供可执行 ID。
- Planner 的职责收敛为目标/意图路由。`intent=explain` 或内容性 answer 必须经过 `AgentContextService.build_explanation_context`，读取解析目标的实际正文；项目级状态答复可用确定性项目索引，但不能顺带解释未读剧情。
- 新增一个 `ArtifactExplainerSkill`，继续用现有 BaseAgent/LLMClient/StructuredOutputParser。模型输入为用户问题、目标版本快照、目标正文（可选单场）、相关设定与来源编号；输出 answer 与引用的 source_index/scene_number/quote。服务端用来源编号回填 UUID/version/checksum，不让模型输出任意 Artifact ID。复用现有 Evaluator 的原文规范化与引文定位能力；将现有私有规范化/场景查找函数提取为共用纯函数，不创建新评审 Agent。
- 解释读取在短只读事务内完成，关闭事务后再调用模型。当前已读正文与用户请求是 ContextBuilder 受保护分区；超预算时返回“请选择一场/一个更窄目标”，不截断关键正文后声称读完。Planner 仍只读摘要，不把全项目剧本塞进去；修复 `AgentCommandService` 对已构建 context 再做 `[:12000]` 字符截断与 token 预算冲突，避免受保护目标在第二次裁切被删除。
- 引文必须在指定版本/场景出现才成为链接。内容性解释没有有效引文或只有缺失正文时返回“原文不足以确认”的有限答复，不附带未经核实的完整解释。解释仍无 Run/Action/Artifact 写入，只保存本 Turn 消息与引用 metadata；重复 Turn 返回该结果不再调用模型。
- Planner 和解释合计使用现有 `agent_turn_max_tokens=16000` 的单 Turn 限制，读正文沿用 `agent_context_budget_tokens=12000`；复用现有 budget context 按 turn ID 包住两次调用，异常 finally 清理，不能只给解释另开一份预算。此限额仅约束本次 Turn 尝试，跨进程精确累计仍属于阶段四，不在本阶段宣传该能力。新解释最多一次生成，不增加自定义重试层。

**API/Schema 增量**：Turn.active_context 可选 `scene_number: int>=1`，仅对 script_draft 合法且须存在于确切版本，纳入 request_hash。增加 `ArtifactCitation`（artifact_id/version/scene_number/quote/checksum）作为 Message.metadata.explanation_citations 的服务端结构；原消息字段不变。新增解释 input/output Pydantic Schema 放在 `backend/app/domain/agent_planner.py`；模型输出仅 source_index。Prompt manifest 新增 artifact_explainer v1.0.0，Planner prompt 版本递增并明确内容解释路由规则。

**文件**：修改 `backend/app/skills/agent_command_planner.py`、`backend/app/application/agent_command_service.py`、`backend/app/application/agent_context_service.py`、`backend/app/domain/agent_command.py`、`backend/app/domain/agent_planner.py`、`backend/app/api/dependencies.py`、`backend/app/llm/openai_compatible.py`、`backend/app/prompts/loader.py`、`backend/app/prompts/manifest.yaml`、`backend/app/prompts/templates/agent_command_planner.md`、`frontend/src/types/api.ts`、`frontend/src/features/agent/MessageList.tsx`、W1-02 画布/hook。新增 `backend/app/skills/artifact_explainer.py`、`backend/app/prompts/templates/artifact_explainer.md`、`backend/tests/golden/artifact_explanation_valid.json`；将现有 Evaluator 的原文规范化与场景查找纯函数提取到 `backend/app/tools/text_evidence.py`，同时修改 `backend/app/skills/evaluator.py` 调用该函数，不能复制第二份校验算法。

**测试**：扩展 `backend/tests/unit/skills/test_agent_command_planner.py`、`backend/tests/unit/application/test_agent_context_service.py`、`backend/tests/integration/api/test_agent_turns.py`、`backend/tests/contract/test_prompts.py`、`backend/tests/evals/agent_commands.json` 和 `test_agent_command_eval.py`；新增 `backend/tests/unit/skills/test_artifact_explainer.py`。最小正反样例：明确第3集无 active；当前第2集但明确改第3集；仅“改这里”无 active；中文第三集；空/无效/跨项目目标；历史稿解释；场景不存在；只有标题不能解释剧情；正文超预算；伪造/跨场引文；读取后新稿生成仍引用原 ID；同 Turn 重放不二次模型调用；Planner+解释没有活动 DB 事务。更新 FakeLLM 及 E2E 桩，不使用保存的真实模型评测 JSON 冒充本轮结果。

**验证命令**：backend 目录 `uv run pytest tests/unit/skills/test_agent_command_planner.py tests/unit/skills/test_artifact_explainer.py tests/unit/application/test_agent_context_service.py tests/integration/api/test_agent_turns.py tests/contract/test_prompts.py tests/evals/test_agent_command_eval.py`。

**回滚**：引用 metadata 为增量，旧消息可读。可回退解释 Prompt/Skill，但回退后内容性解释只返回“当前解释暂不可用/仅显示项目状态”，不得重新上线无正文而解释剧情的旧行为。不改变已有 Artifact。

### W1-04：Outcome 未验证语义与证据可见

**目标**：界面分别表达执行是否结束和创作要求是否得到验证；缺正文证据时绝不判语义达成，用户能从结果直接打开本轮实际版本、检查和 Diff。

**实施内容**：

- 本阶段停止使用“Run终态/产物数量/分差”构成的摘要来调用语义 Outcome Evaluator。已有确定性证据继续报告；自然语言创作要求如“女主更主动、仍保留悬念”没有对应可复核文本检查时标为 unverified。评分上涨、Schema valid、工作流完成都不能自动将其置为 satisfied。
- 新增逐项 `constraint_checks`，元素含原约束文本、status=satisfied|unsatisfied|unverified、reason、evidence_refs。语义要求缺输入、检查异常、旧数据无检查记录都落 unverified；只有明确失败的实际检查落 unsatisfied。W1-03 的 ArtifactCitation 作为展示引用契约。阶段一 不再建设完整语义判定系统；保留既有 Skill 文件供历史契约/后续阶段，退出当前无正文调用路径。
- 保持 `goal_status` 旧三态以兼容旧客户端；语义未验证映射 partially_achieved，并新增 `verification_status=verified|unverified`。新 UI 使用 Run 状态显示“本轮生成已完成”，另列“2 项要求待判断”，不把未验证伪装为已失败。只有已知未达成/下游影响能提出既有受限后续计划；单纯 unverified 不自动再开一轮生成。
- ActionPlanCard 与 MessageList 复用一份 OutcomeEvidenceView；默认显示具体结果入口、待判断要求，details 展开原文引用与检查。不要仅显示数量，不能继续要求 E2E evidence-links=0。保留完整消息内容，去掉只渲染第一行而丢失失败指引的处理。
- 证据目标使用本轮 source/new script/outline/evaluation/continuity Artifact ID，不用通用版本页的 latest 默认值。Action 的结果 metadata 保存这些固定引用与版本；跨项目、缺失证据只显示不可用，不用同集最新稿替代。

**API/Schema 增量**：AgentOutcome 新增 constraint_checks（默认空）、verification_status（旧结果默认 unverified）、evidence_refs（默认空）；原 evidence_artifact_ids、score_delta、remaining_constraints 保留。历史行不回填“已验证”，UI 标记“历史结果未记录逐项核验”。Message.metadata 同步这些字段用于回放，不要求新表。Prompt/Schema 变更与 contract fixtures 同步；本卡不增加新模型调用。

**文件**：修改 `backend/app/domain/agent_command.py`、`backend/app/application/agent_outcome_service.py`、`backend/app/application/agent_action_lifecycle.py`、`frontend/src/types/api.ts`、`frontend/src/features/agent/ActionPlanCard.tsx`、`MessageList.tsx`。新增 `frontend/src/features/agent/OutcomeEvidenceView.tsx`。更新 `backend/tests/golden/agent_outcome_partial.json` 与 `backend/tests/evals/agent_outcomes.json` 的语义，以未知和失败分离为准。

**测试**：扩展 `backend/tests/unit/application/test_agent_outcome_service.py`、`backend/tests/integration/events/test_agent_action_events.py`、`backend/tests/contract/test_agent_command_schemas.py`、`backend/tests/evals/test_agent_outcome_eval.py`、`frontend/tests/agent-workspace.test.tsx`、`e2e/agent-workspace.spec.ts`。断言 completed+无原文→unverified；检查不可用→unverified；连续性明确失败→unsatisfied；仅未验证不生成子计划；旧结果不自称已验证；证据链接打开固定版本；失败/取消指引没有被第一行截断；结果卡与消息只展一份详情。

**验证命令**：backend 目录 `uv run pytest tests/unit/application/test_agent_outcome_service.py tests/integration/events/test_agent_action_events.py tests/contract/test_agent_command_schemas.py tests/evals/test_agent_outcome_eval.py`；frontend 目录 `pnpm exec vitest run tests/agent-workspace.test.tsx`。

**回滚**：字段是 JSON 增量，没有作品迁移。保留新字段只读，回退 UI 也不得把 verification_status=unverified 的结果翻译为目标已达成；必要时用保守的“本轮执行已结束”兼容文案。

### W1-05：固定版本后端导出与服务端历史

**目标**：作者导出哪一稿就固定哪一稿；改稿后从历史再次下载得到相同文件字节和来源版本。

**实施内容**：

- 前端导出复用 `POST /projects/{id}/exports`、ExportService、LocalFileStore、export_file Artifact 和下载端点。UI 展示用户将导出的确切版本清单；点击生成时发送全部选中 kind 的显式 Artifact ID，不在浏览器重新序列化 DOCX/Markdown。
- 后端 create_export 在创建 Run 前解析/校验选择，规范化成明确的 artifact_ids 保存到 config_snapshot；旧客户端省略 IDs 时也在请求接受时解析 latest，不能等 Worker 运行时重新选稿。显式 selection 某 kind 是空数组/缺项时拒绝 422，不得通过 `_assemble` 中 truthy 判断回落 latest；不属于当前项目、invalid、类型不匹配、同一集选多个正文版本均在排队前拒绝。
- Worker 仍使用已实现 ExportService，输出文件与 source_artifact_ids/sha256 固定。将 export_file Artifact ID 同时写入 Run.state_summary 供刷新读取；RunResponse 增可选 `result_artifact_ids`（空列表兼容其它旧 Run），导出结束后用该 ID 下载，不只依赖易错过的即时 SSE。
- 历史从项目 type=export_file 的已有 Artifact 列表分页查询，重新下载固定 `/exports/{artifact_id}/download?project_id=...`，不再次调用 serializeExport。已有 localStorage 历史保留为“旧版浏览器记录，未保存固定文件”，不能伪造为可恢复的服务端交付；不自动迁移/重生成它们。移除服务端历史的“清空”操作，本阶段不新增删除导出物接口。
- 导出使用当前实际有的 Artifact 集合，不能依赖 project.current_episode_count 假设正文从1连续存在；评估附带自身 source_script ID，选择与正文版本不匹配的报告时显式提醒并默认不包含。某选中 kind 读取失败必须报错，不能 catch 成空内容后悄悄少导一部分。

**API 增量**：沿用 CreateExportRequest 的 kinds/format/artifact_ids/idempotency_key；加强验证与接受时快照。RunResponse.result_artifact_ids 可选列表；ExportFileContent 已有 sha256、source_artifact_ids、warnings，不建新导出表。新创建请求沿用相同 idempotency_key，参数改变必须新键；相同键不同选择返回409而非复用错误文件。

**文件**：修改 `backend/app/api/v1/exports.py`、`backend/app/application/export_service.py`、`backend/app/domain/export.py`、`backend/app/application/workflow_dispatcher.py`、`backend/app/api/v1/runs.py`、`frontend/src/lib/api-client.ts`、`frontend/src/types/api.ts`、`frontend/src/app/projects/[id]/exports/page.tsx`、`frontend/src/features/exports/ExportSection.tsx`、`ExportHistory.tsx` 和 W1-02 的 exports panel。已有 `frontend/src/lib/export.ts` 的序列化仅从产品路径移除；最后确认无调用再删除未用代码，不为本卡引入另一下载库。

**测试**：扩展 `backend/tests/integration/api/test_exports.py`、`backend/tests/integration/export/test_export_service.py`、`backend/tests/integration/api/test_upload_to_export.py`、`frontend/tests/exports.test.tsx`。固定v1生成文件→v2落库→刷新页面→从历史下载，SHA256一致；queued等待期间新稿生成不改选择；ID跨项目/invalid/空列表/重复同集/键复用错参拒绝；文件遗失404不能自动以新稿替补；选中内容加载失败禁止提交；DOCX可打开，Markdown来源清单可核对。

**验证命令**：backend 目录 `uv run pytest tests/integration/api/test_exports.py tests/integration/export/test_export_service.py tests/integration/export/test_docx.py tests/integration/api/test_upload_to_export.py`；frontend 目录 `pnpm exec vitest run tests/exports.test.tsx`。

**回滚**：已有 ExportFile/文件保留。UI 可以回到旧导出中心外观，下载必须继续走已存 Artifact，不可恢复“历史记录重新生成当前稿”的逻辑。无需数据库迁移；文件和结果引用缺一时显示可诊断失败。

### W1-06：原稿附件入口接已有导入路径

**目标**：作者可在工作台附加 TXT/DOCX 单集原稿、大纲或参考资料，看到解析/分类结果并进入现有阅读、评估或分阶段创作路径。无需复制长文本到4000字输入框。

**实施内容**：

- Composer 添加原生文件输入及附件卡，只接受当前后端支持的 TXT/DOCX，客户端提示<=10MB，最终由现有 FileParserTool 联合大小/扩展名/内容签名验证。选文件先 `POST /projects/{id}/uploads`（multipart），只解析存档，不调用模型；卡片展示原名、字数、警告。用户点击“识别并导入”才创建 `action=import` Run，明确该步骤会做模型分类并可能入库。
- 完全复用 `build_import_workflow`、ImportClassifierSkill、route_import、full_script_to_script_draft 与 KnowledgeService.ingest_upload，不新增长篇拆分、小说改编或通用附件系统。当前 full_script 转换固定 episode_number=1，UI 只承诺“导入单集原稿”；遇到已有第1集先展示“导入会生成第1集新版本”的具体说明，确认后生成不可变新版本，不静默覆盖；暂不支持从同一文件解析任意多集。
- `full_script` 成功生成 ScriptDraft 后，打开该 Artifact 的画布并提供“评估这份原稿”，调用现有单集评估流程且冻结该版本；转换结构不足时保留上传文件，说明未形成可评估剧本，不展示成功稿件。`reference` 展示是否已入资料库，embedding失败保留文件并显示真实告警，不把“分类完成”写成“检索可用”。
- `outline/idea_or_notes` 的分类结果显示“基于此材料创作”按钮。按钮调用现有 create_script Run 路径，config.upload_id 指向上传记录，config.options 使用项目目标集数并设置 stop_after=outline；Dispatcher 的 `_resolve_upload_text` 已支持此入口。此按钮本身是具体生成授权，执行前展示来源、集数和大纲阶段终点；不强迫用户把全文再输入聊天。运行在W1-02统一Run状态区可见，结果进入同屏画布；阶段4才统一为Task入口。
- `unknown` 仅说明无法判断并保留附件，首版仅提供“基于它创作”的现有 upload_id 路径与取消，不提供前端覆盖分类或伪造成功分类。新增未知分类也不自动触发生成。
- 上传记录通过已有 GET uploads 恢复；导入Run通过config.upload_id对应文件，重试复用同一Run/key，不能再次上传同一文件才能继续。导入终态将 classification/script Artifact ID写入W1-05新增 result_artifact_ids；前端读取分类Artifact获取content_type/reason，必要route由现有终态SSE/服务端已存state读取，不靠UI猜模型结论。

**API 增量**：上传multipart与import/create_run现有请求不变。前端新增 uploadsApi.create/list 与类型UploadResponse；新增 import 结果返回的明确引用仅复用 RunResponse.result_artifact_ids。创建 import/create_script 携带config.upload_id时，Run入口在排队前用UploadRepository.get_for_project验证所属项目、parse_status=parsed；禁止前端传服务器path。若上传source为用户已解析材料，Run配置仍由服务端固定允许字段构造，不开放任意config调用给组件。

**文件**：修改 `frontend/src/features/agent/AgentComposer.tsx`、`AgentWorkspace.tsx`、`frontend/src/lib/api-client.ts`、`frontend/src/types/api.ts`、`backend/app/api/v1/runs.py`、`backend/app/application/workflow_dispatcher.py`；新增 `frontend/src/features/agent/UploadInput.tsx`。复用 `backend/app/api/v1/uploads.py`、`backend/app/workflows/import_file.py`、`backend/app/tools/file_parser.py`、`backend/app/workflows/router.py`、`backend/app/application/knowledge_service.py`；只有必须回传明确失败时才修改这些既有文件，不另建ImportService。

**测试**：扩展 `backend/tests/integration/api/test_uploads.py`、`test_upload_to_export.py`、`backend/tests/integration/workflow/test_import_workflow.py`、`backend/tests/integration/rag/test_upload_ingest.py`，新增 `frontend/tests/upload-input.test.tsx` 与 `e2e/import-workspace.spec.ts`。覆盖TXT/DOCX→分类→原稿阅读/大纲创作；>10MB/伪装/空文档；跨项目upload拒绝；已有第1集提示与原稿checksum保留；full_script转换失败不伪造成功；reference入库失败明确显示；unknown不自动生成；刷新后导入状态与文件仍在；同逻辑重发不重复Run。FakeLLM/FakeEmbedder沿用现有注入。

**验证命令**：backend 目录 `uv run pytest tests/integration/api/test_uploads.py tests/integration/api/test_upload_to_export.py tests/integration/workflow/test_import_workflow.py tests/integration/rag/test_upload_ingest.py`；frontend 目录 `pnpm exec vitest run tests/upload-input.test.tsx`。

**回滚**：移除附件入口不删除uploads、知识库文档或导入Artifact；已生成Run继续在通用状态区可见。没有新增导入格式/数据迁移；保留原文件和分类结果供重试。

### W1-07：验收环境兼容、全链路验证和文档收尾

**目标**：全部已交付卡片在FakeLLM环境中可重复验证；Mac/Linux能够启动和清理本轮测试进程，前端构建/真实浏览器检查与契约测试分别有证据。

**实施内容**：

- 现有 `scripts/e2e.sh:108-115` 依赖setsid。用已有Python标准库subprocess启动backend/frontend独立会话/进程组，可靠转发SIGINT/SIGTERM并wait，先TERM后限时KILL；只保存和清理本轮启动的PID/PGID，不使用pkill、按端口杀进程或误杀用户开发服务。保留现有脚本CLI与Makefile入口，新辅助脚本仅解决spawn/cleanup，不重写整个测试平台。
- 使用本轮唯一Compose project名隔离容器与数据；只down本轮创建的项目。启动前检查docker compose与端口，缺环境或端口被占用就给出明确错误且不清理别人的服务。`--no-build`必须验证前端构建的API_BASE/工作台开关与本次一致，不能拿错构建通过测试。
- E2E不再以“刷新一次结果才出现”作为成功路径。正常完成应自动回写消息和实际版本入口；刷新是额外恢复验收。为W1-01四批继续、W1-03正文解释、W1-04未知语义、W1-05历史文件、W1-06附件分别保留能失败的用户路径。
- UI人工验收必须记录实际viewport截图与键盘流程：1440×900/1024×768/390×844；正文可读、无横向溢出、Composer不遮挡内容、对话与正文切换不丢稿、Escape与焦点返回可用。截图证据只能来自实际渲染，不把组件测试叫视觉验证。
- 文档更新由执行每卡的人同步 `docs/API_CONTRACT.md`、`docs/TEST_PLAN.md`、Prompt manifest、前端类型；阶段完成后更新 `docs/DEV_PLAN.md` 对应W1任务状态与证据、追加 `docs/DEV_LOG.md`，Bug修复追加 `docs/TROUBLESHOOTING.md`，修正 `docs/AGENT_EVAL_REPORT.md` 与保存评测的矛盾但不捏造本轮真实成绩。

**文件**：修改 `scripts/e2e.sh`、`e2e/agent-workspace.spec.ts`、`e2e/fixtures/helpers.ts`、`e2e/fixtures/data.ts`、`e2e/playwright.config.ts`、上述验收文档。新增 `scripts/e2e_process.py`、`backend/tests/unit/test_e2e_process.py`（仅进程所有权/信号清理自校验，无Docker）；W1-06新增 `e2e/import-workspace.spec.ts`在此完成整套编排接线。

**验收命令**：逐卡目标测试通过后，在repo根执行 `make lint`、`make typecheck`、`make test`、`make ci`、`make e2e REPEAT=5`；`make ci`已包含lint/typecheck/cov，实际执行可省去重复完整门禁，以最后一次变更后的 `make ci` + frontend `pnpm test` + `make e2e REPEAT=5`作为最终记录。新迁移需 `make migrate`、`make migrate-check`及旧数据迁移用例。当前主机无Docker时可完成静态/单元检查，完整集成/E2E必须在具备Docker环境运行，不能用此限制把W1标DONE。

**最终验收条件**：

1. 一个项目页面完成选稿→读原文→明确修改→确认→查看新版本/Diff，URL固定真实版本，刷新/后退保持阅读与会话。
2. 明确第3集无active无需多余澄清；代词缺目标仍澄清。内容性解释必须读取正文并返回有效原文引用；无法核实则说明不足。
3. completed、评分上涨、无语义证据三个条件同时出现时仍显示unverified；没有“缺证据却已达成”；用户能查看具体待判断要求与本轮证据。
4. 聊天create后四批继续成功；旧继续请求重放/并发只承认一次；阶段结果不被吞并；老Action结果不被新Action覆写；失败重试上限仍有效。
5. 固定v1导出后生成v2，历史下载字节SHA256仍相同；异步等待不改变导出选择；缺文件不会偷偷重新生成。
6. 单集TXT/DOCX导入或大纲文件创作到同屏画布可用；非法/跨项目/未知分类不会触发未授权生成；不承诺长篇多集解析。
7. Mac/Linux脚本启动/中断后无本轮孤儿服务，其他开发进程不受影响；FakeLLM E2E连续5次通过，三个viewport与键盘验收有实际证据。

**回滚与交接**：按W1-01保留兼容后端，不进行破坏性删表或文件清理；UI可独立回退。阶段2只需接入采用指针/scene_id与局改契约，不需要重做本阶段的固定Artifact阅读、引用、历史导出；阶段4再把同Run stage_generation兼容路径迁移为Task多Run，当前结果仍可读。真实模型与创作质量试用是后续独立证据，使用现有模型凭证与明确预算；本阶段工程门禁不要求新增账号，也不暗中调用模型。

---

<a id="stage-2"></a>

## 阶段二：候选、采用与局部编辑

### 交付目标与验收边界

用户能够明确区分“正在使用的版本”和“Agent/人工提出的候选版本”；可以在单场景或选中文本范围内修改剧本，查看差异后逐集或成组采用，也可以切回旧版本。生成成功、结构校验通过、质量评分通过和用户采用是四件独立的事。未采用内容不得悄悄进入下一项任务或普通导出。

采用最小结构：保留不可变 Artifact、ArtifactLink、现有 Action/Run 和精确版本 Diff；增加作品采用指针及操作流水，给场景增加稳定 ID，复用一个确定性编辑写入口。不建立 Git 分支系统，不把台词拆成数据库记录，不引入新的工作流引擎。

阶段验收必须同时覆盖：空白新项目完成候选创作；旧项目无损迁移；手动与 Agent 局改仅改变授权范围；并发采用只成功一方；候选批次内部依赖正确；运行中换采用版本不污染已确认输入；丢弃不破坏历史/导出快照；普通导出使用采用版及其对应质量证据。

### 六张任务卡共同遵守的数据契约

#### 采用指针、候选与输入工作集

| 对象 | 字段与含义 | 不变量 |
|---|---|---|
| `artifact_heads`（新增表） | 主键 `(project_id, artifact_type, episode_number)`；`artifact_id` 外键；`revision BIGINT`；更新时间 | 仅管理 `story_bible`、`episode_outline_set`、`script_draft`。指针指向同项目、同类型、同集的结构有效 Artifact；revision 每次实际切换递增。创作类型的采用版从这里读取，不从最大 version 推断。 |
| `artifact_operations`（新增表） | `id`、`project_id`、`operation_key`、`request_hash`、`kind: initialize/adopt/restore/discard/restore_candidate`、`changes JSONB`、`result JSONB`、`created_at` | 状态操作命名空间内 `(project_id, operation_key)` 唯一；request_hash 包含 kind 和完整请求。采用 changes 保存各槽旧/新 ID、revision 和迁移来源；disposition 操作保存 artifact_id 与前后可见性。result 保存原响应，重放不按当前状态重算。流水与全部状态变化同事务提交。 |
| Artifact 可变旁路元数据 | `candidate_disposition: available/discarded/null`；`editorial_metadata JSONB`；`operation_key`、`operation_request_hash` | adopted 是 head 计算出的关系，不能写成 Artifact 状态。disposition 不修改 content、checksum、version、结构有效性。旧记录/证据记录可为空。创建候选的操作键在 Artifact 表内项目唯一，与状态操作收据属于不同端点命名空间，不能声称跨表全局唯一或相互覆盖。 |
| `editorial_metadata`（有 Pydantic schema） | `origin_kind: manual/agent/import/legacy`、`base_artifact_id`、`basis_heads`、`edit_scope`、`applied_patch`、`created_by_run_id`、`discarded_at` | 只保存编辑来源、输入和操作意图；不能在这里复制另一份正文或隐含采用指针。 |
| `projects.artifact_selection_version` | 兼容值 1，新语义值 2 | 已有项目先保持 1，完成受控初始化后原子切为 2；阶段退出门满足后新项目默认 2、初始没有 heads，schema 扩展期间继续默认 1。版本 2 不能隐式回退到 latest-valid。 |

`episode_number` 延续现有 Story Bible/整季大纲约定，不另造空值槽位。表约束、应用校验与所用常量保持一致。`artifact_id` 不接受跨项目或错类型指向。

共享值对象放在领域模型中，字段名固定如下，供阶段三/四继续使用：

```text
ArtifactWorkset {
  schema_version: 1,
  revision: integer,
  baseline_heads: [
    {artifact_type, episode_number, artifact_id: UUID|null, revision: integer}
  ],
  artifacts: [
    {artifact_id, artifact_type, episode_number, version, checksum}
  ]
}
```

`artifacts` 复用现有 `ArtifactSnapshot`，每个 `(artifact_type, episode_number)` 只能出现一次；候选和采用版都用精确 ID 表达。`baseline_heads` 记录确认时相关槽位的采用指针及 revision，缺失槽位为 `null/0`，用于比较和采用 CAS，不能用它重新解析运行输入。

确认时把 workset 写入 `Action.plan.workset` 和 `Run.config_snapshot.workset`，之后保持不变。执行中每个成功节点把新候选加入 `Run.state_summary.workset`，revision 递增，并在同一个持久化边界提交 Artifact 和当前工作集。暂停后的显式替换只更新 summary 中的工作集并留下事件；不得覆盖最初的 config。恢复时复用持久化工作集，不能重新查询“最新”。没有新 workset 表或 workset 服务；校验/构造放进既有 ArtifactService 与编排服务。

新项目可以在一个候选批次中顺次产生 Story Bible → 大纲 → 多集剧本：下游读取本次工作集中的上游候选，候选之间建立真实 ArtifactLink。没有 heads 时，界面显示“尚未采用”；只有用户明确的采用操作会建立 heads。阶段三的连续性账本和分集摘要可以作为证据进入 workset，但不设为作品采用指针。

#### 采用与历史的统一语义

1. 批量采用以最终 resulting heads 校验本次新采用 Artifact 的创作依赖。检查 `derived_from`、`references`、`continues` 的真实目标类型；`revises` 是编辑来源，证据引用也不作为作品 head 依赖。实际引用了上游候选，就必须同时采用该候选或已采用同一 ID。不能只看版本号或依赖数组位置。
2. 只采用上游版本是允许的，已经采用的下游版本保留，返回具体 `stale_dependencies` 和影响集数。系统不得默默改写/取消下游采用。后续任务确认时显示这些影响，由用户选择沿用精确旧输入或更新工作集。
3. “切回旧版”是新的 restore 指针操作，指向原来的 Artifact ID，不复制正文、不生成伪新版本，CAS 与依赖校验跟采用一致。依赖不匹配时只能明确选择配套旧版本一起恢复，或创建 rebase 候选，不提供跳过校验的 `force=true`。
4. 丢弃仅从默认候选列表隐藏，历史、精确读取、Diff、已有运行/导出快照仍能读取。采用版不能丢弃。被 queued/running 工作集引用时拒绝丢弃；paused 工作集必须先显式替换或移除。依赖这个候选的其他候选保留，但再次采用时标为依赖不可用。可以显式恢复候选可见性。
5. 并发写采用指针先短时锁项目行，再校验每个 expected `{artifact_id, revision}`，处理初次建立 head 的空槽竞争和 ABA（A→B→A）情况。校验、幂等请求、流水、多个指针同事务；任何冲突全部回滚。锁内不调用模型，不运行长耗时质量检查。

### W2-01｜增加采用模型并提供可审计的项目迁移

**目标与依赖：** 建立其余任务共享的存储契约。可在阶段一完成后的现有模型上直接开始；迁移固定为 `backend/migrations/versions/0012_artifact_selection.py`，位于阶段一 0011 之后。

**代码范围：** `backend/app/db/models/artifact.py`、`project.py`、models 导出；新增 `artifact_head.py`、`artifact_operation.py`；扩展 `backend/app/db/repositories/artifacts.py`、`backend/app/artifacts/store.py`、`backend/app/application/artifact_service.py`；新增共享领域模型 `backend/app/domain/artifact_workset.py` 及采用请求/响应 schema；新增上述 additive migration 和 `backend/app/cli/artifact_heads.py`。增加表和方法即可，不建独立 ArtifactHeadService。

**实施内容：**

- 实现上表字段、唯一键、索引与同项目/类型/集数校验；增加 `get_adopted`、`list_heads`、`validate_workset`、候选 disposition 查询。保持 `get_latest_valid` 的物理含义，避免旧调用被静默改义；工作台业务读入口在 W2-02 逐个迁移。
- `Artifact.status` 表示正文结构/领域有效性，质量结果继续通过绑定脚本版本的证据表达。现有修订节点会先写 draft、连续性检查后改 valid，不能一次性改写历史：保留 legacy Run 行为，新的 阶段二 写入口结构校验完成即标 valid，质量失败不等于撤销候选正文。无效结构不能采用。
- 修正新写入口的去重边界：已有 `find_by_input_hash` 缺少项目过滤，不可直接承担编辑幂等；使用项目内 `operation_key/request_hash`。同键同请求返回原结果，同键异请求 409。artifact version 分配和新指针提交共享事务时，冲突处理使用受限保存点/重试，不能触发 `ArtifactStore` 当前的整会话 rollback 丢掉已经准备好的流水。
- 旧项目初始化选择迁移时各槽位 latest valid 作为“继承当前版本”，写 `kind=initialize` 流水与来源，再原子切 selection_version=2。这只是兼容初始化，UI 不声称用户曾手动采用。没有 valid 的槽位留空。内容和 checksum 原样保留。
- 初始化在项目没有 queued/running/needs_review Run，也没有 planning Turn 的安全边界执行，不能边跑边采集不一致 latest。处于审阅门的 legacy Run 完整保持旧语义，项目暂不 activation；先用兼容入口结束或取消该 Run，再激活，不转换或丢弃 checkpoint。activation 和新写入入口用同一项目锁消除竞态。

**API/读回：** `GET /projects/{project_id}/artifact-heads` 返回 selection_version、heads、各槽位 revision 与迁移来源；`GET /projects/{project_id}/artifacts/adopted?type=&episode=` 返回 head 和精确 Artifact，空槽位返回明确 `ARTIFACT_NOT_ADOPTED`。原 `latest` 端点保留版本历史用途。维护命令为 `cd backend && uv run python -m app.cli.artifact_heads --project-id PROJECT_ID`，PROJECT_ID 使用本次选定项目的真实 ID；默认只给出拟采用清单、旧 Run 阻塞项和校验结果，加 `--apply` 才在项目锁内重新核验并激活。幂等键由项目 ID 与 selection_version=2 固定生成。不能在启动时全库隐式迁移。

**验证与完成标准：** PostgreSQL 集成测试证明同槽唯一、跨项目拒绝、空槽并发、重复迁移幂等和事务失败无半迁移；用旧项目 fixture 验证初始化前后的正文/checksum/版本数不变；draft 无 head，新项目零 heads；artifact status 与质量证据分离；不同项目的同内容编辑不串用 Artifact。

**回退：** additive schema 可先闲置。项目激活前可关入口；激活并产生候选后，只能回到仍理解 heads 的兼容版本，不能退回“最大 valid 即当前”的旧二进制。保留表、流水、候选、checkpoint，不执行破坏性 down migration。

### W2-02｜统一默认读入口，冻结 Action/Run 的作品工作集

**目标与依赖：** 依赖 W2-01，使候选不再抢占工作台、后续任务和导出的当前版本；空白项目候选创作仍能连续运行。此卡必须完整扫描业务调用点，不以新增一个 get_adopted 方法作为完成。

**后端修改清单：**

| 文件/入口 | 需要的行为 |
|---|---|
| `application/agent_context_service.py` | 上下文列表以采用 heads 为默认；评价通过确切 script ID 绑定；候选工作集作为显式任务输入，不能用 highest-version reducer 拼装。 |
| `application/agent_command_service.py` | 计划/确认保存精确 workset；修订源、Story Bible、大纲、评价输入统一从它读取。确认比较引用版本/checksum/head revision，候选选择显式进入 workset。evaluate Run 必须继承快照，不能在转换配置时只留下 scope/episode。 |
| `application/agent_action_lifecycle.py` | follow-up 由前次候选工作集或当前采用版显式构造，不重新取 latest。 |
| `application/workflow_dispatcher.py` | evaluate_project、revise_script 等分支统一接收服务端核验的 workset。所有可创建 Run 的入口都适用，不能只保护聊天确认；不信任用户直接塞进 config 的权威快照。 |
| `application/run_service.py` | continue 不再刷新 latest outline。暂停续跑接收 `expected_workset_revision` 和显式 `use_artifact_ids`；服务器验证引用完整性、可用性和依赖，冲突 409。运行中只能在既有安全暂停边界改变输入。 |
| `workflows/nodes/prepare_conversational_revision.py`、`application/revision_service.py`、`workflows/nodes/revise_outline.py` | 修订上下文和 locked facts 用确认时 workset；不能剧本来源固定而 Bible/outline 又读 latest。 |
| `application/evaluation_service.py` | 通过真实 ArtifactLink 类型解析脚本依据，禁止假设 derived_from 最后一条就是大纲。评价只对应该 script ID/checksum。 |
| `application/export_service.py`、`api/v1/exports.py` | 普通导出解析 adopted heads，并绑定所选脚本的评价/修订证据；在创建导出请求时冻结 selection 快照。阶段一已有快照实现时复用。候选预览导出必须显式选择精确 IDs，文件/界面明确“候选预览”。 |
| `nodes/story_bible.py`、`outline.py`、`write_episode.py`、`revise.py`、`revise_outline.py`、`workflows/import_file.py` | 所有新创作写入候选和当前 workset，生成/导入/自动修订都不能自动改 heads。写下一集时保存真实上一集脚本的 `continues` 关系。 |
| `workflows/nodes/finalize.py`、项目进度读模型 | 生成数与采用数分开。新工作台以 adopted script heads 计已采用集数；不再把 current_episode_count 的生成增长当作用户已接受。保留 legacy 字段兼容老 Run。 |

**前端修改清单：** `frontend/src/features/agent/ArtifactContextPanel.tsx`及其深链传精确 ID；`ActionPlanCard.tsx` 的门禁预览展示本次 workset 引用；Story Bible/outline/script 页面默认调用 adopted；script 页评价按正在查看的脚本请求；“评价这版”传脚本 ID，而不是触发无约束项目 latest 扫描；exports 页面默认 heads；版本历史保留 exact-ID Diff，默认比较基准改成 adopted。统一 `frontend/src/lib/api-client.ts`、类型与 hooks，清除页面内“最大 version/第一项就是当前”的 reducer。

**API：** existing Action plan/confirm 和 Run 返回增补 workset schema，不新增并行编排对象；continue 请求增加期望工作集 revision 与显式替换项。版本列表支持 `selection=adopted|candidate|all`、`include_discarded`，响应每条包含 `is_adopted`、candidate_disposition、basis 及 stale_dependencies。外部传入的 ID 先校验项目归属、type/episode/checksum、结构有效性及候选可用性；最终 manifest 由服务器组装。

**验证与完成标准：** 建立路由/服务回归矩阵：新生成更高版本未采用，所有默认页面、Agent 计划、普通导出仍引用旧 head；显式候选运行则全链路引用候选工作集；确认后别人改 head，正在执行的输入不变；暂停续跑无 `use_artifact_ids` 不偷偷换上游；空白项目一次运行可以 Bible→大纲→剧本，heads 仍为空；导出提交后 head 变化，导出内容与冻结快照一致；对每个脚本显示的评分来自其精确版本。静态搜索 `get_latest_valid/get_latest` 和前端最大版本 reducer 只作为漏网检查，不能代替以上行为测试。

**回退：** read 路由按项目 selection_version 兼容；新 workset Run 即使 UI 入口关闭，也必须能按精确 ID 读回和恢复。恢复旧 UI 时默认路由仍由兼容服务返回采用版，不能通过关旗标使候选再次成为当前版。

### W2-03｜引入稳定场景 ID 和确定性的局部编辑引擎

**目标与依赖：** 依赖 W2-01，可与 W2-02 的调用点迁移并行。为手改和模型局改提供同一个可验证写约束；移动/插入场景不再让“第三场”锚点指到另一场。

**代码范围：** `backend/app/domain/script.py`、`domain/diff.py`；新增纯函数模块 `backend/app/tools/script_edit.py`；扩展 `tools/diff.py`、`artifacts/diff_service.py`、`tools/script_render.py` 与现有文本统计工具；`tools/script_text.py` 导入适配；前端脚本/差异类型和文本选择工具。数据库仍每版一份 JSON Artifact，不拆场景/台词表。

**实施内容与 schema：**

- 保留 ScriptDraft schema v1 解码，新增 `content_schema_version=2.0`：每场加入服务器管理的 `scene_id: UUID`；`scene_number` 仅为顺序显示，保存后由服务器重排。编辑/移动保留 ID，新插入场景服务器生成 ID。正文仍用原有 dialogue 数组及字段结构。
- 旧 Artifact content 和 checksum 不变。读取 envelope 另给 `scene_anchors`：以 `UUID5(artifact.id, 'scene:'+scene_number)` 生成该旧版本的确定性锚点；第一次编辑把这个映射写进新 v2，后续继承。两个无已知编辑关系的 v1 版本不能因为编号相同就声称是同一场；Diff 可保留相似度匹配作为展示回退，但不能用它自动重放修改或移动证据。
- 选区引用固定为 `{base_artifact_id, base_checksum, scene_id, field, dialogue_index?, start?, end?, selected_text?}`，field 为白名单而不是任意 JSON path。文本范围用 Unicode codepoint，前端显式从 UTF-16 转换；服务端验证 selected_text、边界和 base checksum，禁止 offset 静默错位。单场整字段替换无需 offset。
- patch 操作最小支持 `replace_field`、`replace_text_range`；只有显式 scene/structural scope 才允许插入/删除/移动场景。每次 patch 经过一套范围校验、schema 校验和身份校验。scope 之外字段及场景逐项保持一致，只有服务器派生的编号、plaintext、字数统计可重算。
- 输出调用 `script_plain_text` 与现有统计函数统一重建，不把模型给的 plaintext 或 word_count 当真。范围检查在标准化之前识别实际编辑，避免模型通过改 scene_id/number 掩盖越界。
- v2 导入遇到无真实大纲时，`referenced_outline_artifact_id` 允许 null，并记录为未提供；现有导入用随机 UUID 占位，不能把它当真实依赖。保留旧原文，适配层标注未核实，不批量伪造 ArtifactLink。

**验证与完成标准：** 场景插入/移动后旧 ID 不变；legacy 读不变 checksum，首次编辑映射可复现；emoji/中文选区准确；过期 checksum、错 scene、伪 ID、选区文本不符、越权字段修改均拒绝；模型即使返回整稿也不能篡改非授权场景；结构改动必须显式 scope；Diff 优先显示稳定 ID，并保留 v1 展示能力。测试使用固定文本/patch fixture，不依赖真实模型。

**回退：** v1/v2 双读长期保留；关闭局部编辑入口不影响 v2 版本读取、渲染、Diff、导出。不能把 v2 已写内容降级覆盖 v1。

### W2-04｜接通人工和 Agent 局改，共用候选写入口

**目标与依赖：** 依赖 W2-02、W2-03。手动输入直接保存候选；自然语言局改复用现有 revise_script Action/Run 和 ReviserSkill，两条路径最终执行同一个 `ArtifactService.apply_script_edit`。

**代码范围：** `application/artifact_service.py`、`api/v1/artifacts.py`、`skills/reviser.py`、`prompts/templates/reviser.md`、`workflows/nodes/revise.py`、`prepare_conversational_revision.py`、Action plan/confirm schema；`frontend/src/features/scripts/ScriptView.tsx`、剧本页面及局改面板。现有全文修订仍支持，但必须明确标注 full_script scope。

**共用写协议：**

```text
POST /artifacts/{base_artifact_id}/edits
{
  operation_key,
  base_checksum,
  expected_head: {artifact_id: UUID|null, revision},
  scope,
  patch,
  save_and_adopt: false
}
→ {candidate, base, current_head, diff, stale_dependencies, operation_id}
```

人工保存不创建 Run、不调用 LLM，结果经过 W2-03 的确定性校验后创建不可变候选；写 `revises → base` 并继承正确的创作依据。保存基于旧冻结 base 可以成功，返回 stale 提示；不能假装是在当前采用版上修改。用户显式选择 save_and_adopt 时，候选写入与 W2-05 的采用 CAS 在同一事务完成，冲突则整体不落地，避免出现“已采用成功”假反馈。

Agent 计划记录 base、scope、选择文本和 workset；确认后 ReviserSkill 在 scoped 模式返回 bounded patch，节点将它交给相同写入口。对旧全文输出提供适配，但仍对比 base、执行同样范围守卫；不能把“prompt 要求只改一场”当成隔离措施。生成失败、校验失败、scope violation 和写入成功分别返回现有 Run 错误/事件；重复节点重试以稳定 operation_key 返回同一候选。

**前端行为：** 从正在查看的版本进入“编辑本场”“编辑选区”“让 Agent 修改”；显示采用版/候选标签和准确基线。手改有未保存提示、保存候选和差异预览；Agent 路径显示修改范围、要求、预计采用影响，再复用现有确认流程。服务端拒绝过期选择时保留用户输入，刷新基线后重新选择，不丢弃输入或覆盖当前稿。局改无需为了编辑正文打开新 Task 页面。

**验证与完成标准：** 相同 patch 从人工和 mock Reviser 路径写入得到相同正文与派生字段；未授权字段被拒；重复请求/节点重试只产生一个候选；不同操作可保留不同候选；旧基线保存得到 stale 提示但不覆盖 head；save_and_adopt 竞争失败不落半笔；模型报错不写正文。至少一条浏览器流程证明“选一段→手改→保存候选→Diff→返回”完整可用，一条 mock Agent 流程证明范围约束生效。

**回退：** 关闭 Agent scoped 模式或人工编辑 UI 均不影响已经创建的候选/历史；保留共享写入口协议以支持重试读回。全文修订不得绕过新采用机制。

### W2-05｜实现批量采用、切回、丢弃和保守 rebase

**目标与依赖：** 依赖 W2-02 的工作集/依赖记录和 W2-03/04 的编辑溯源。提供实际可用的作品版本管理，不引入通用分支合并系统。

**代码范围：** 扩展 `application/artifact_service.py`、`db/repositories/artifacts.py`、`api/v1/artifacts.py` 和 W2-01 schema；rebase 复用 `tools/script_edit.py`，影响分析复用 ArtifactLink 反向查询。所有操作项目归属统一校验。

**API 与事务行为：**

| API | 输入/结果 | 关键约束 |
|---|---|---|
| `POST /projects/{project_id}/artifact-adoptions/preview` | selections `[{artifact_id, expected_head_id, expected_head_revision}]`；返回 diff 摘要、缺失依赖和下游影响 | 只读预览不占用 CAS；实际提交必须重新校验。 |
| `POST /projects/{project_id}/artifact-adoptions` | 同 selections、`operation_key`、`kind: adopt/restore`；返回更新 heads、流水、stale_dependencies | 短项目行锁→幂等→逐槽 CAS→结构/依赖检查→全部指针及流水提交。原 head 已是目标时视为无变化，不增加 revision；不同 expected 仍按并发契约处理。 |
| `POST /artifacts/{artifact_id}/discard` | operation_key，期望 disposition；返回隐藏后的候选 | 仅候选；采用版或 active 工作集引用返回 `CANDIDATE_IN_USE`/`ARTIFACT_IS_ADOPTED`；不改变内容/状态/链接。 |
| `POST /artifacts/{artifact_id}/restore-candidate` | operation_key，期望 disposition | 恢复 available，不等于采用。 |
| `POST /artifacts/{candidate_id}/rebase` | operation_key、`target_head_id`、`expected_head_revision` | 仅在已记录 patch 的安全重放条件成立时创建新候选，返回新旧 base 和 Diff；从不修改原候选或 head。 |

采用校验全量按最终 heads 计算：一次采用 Bible、outline、script 可以跨越原 heads；只选 script 而其依赖的 outline 仍是候选，要精确返回缺少的父版本；可让 UI 勾选配套依赖，但服务端不能悄悄扩大用户选择。连续剧新稿的 `continues` 是实际前集版本，部分采用按该依赖处理。祖先/上游已经存在的 legacy 缺链仅标 `unverified_legacy`，不能伪造“依赖已证明”。新生成和编辑必须具备可核验链。

rebase 仅做保守 scene-level patch 重放：候选必须保留原 base 与 applied_patch；新目标采用稿与旧 base 的稳定场景身份可证明；候选触及场景在目标中没有内容变化，创作依赖也一致。另一场被改而本场原样，可以重放生成新候选；同一场双方变化、结构增删/移动、没有可靠 ID 的 legacy、上游依据改变都返回 `REBASE_CONFLICT`，指出场景和原因。界面展示 base/current/candidate，由用户选择重新局改/手动处理。阶段二不进行语义自动合并。

错误码至少包括 `HEAD_CONFLICT`（携带 current head+revision 和仍可保存的用户输入引用）、`DEPENDENCY_NOT_ADOPTED`（缺失 parents）、`CANDIDATE_DISCARDED`、`CANDIDATE_IN_USE`、`REBASE_CONFLICT`、`OPERATION_KEY_REUSED`。所有 conflict 保留原版本和原输入，不静默覆盖。

**验证与完成标准：** 两事务同时采用同槽只有一个成功；空槽并发、A→B→A、同 key 同 body、同 key 异 body；两集批量操作其中一集失败整批回滚；完整候选依赖包成功、部分缺父拒绝；上游单独采用保留下游并返回 stale；精确旧版 restore 以及不兼容依赖 restore；丢弃后历史/Diff/已冻结导出仍可读，新采用拒绝丢弃候选；active/paused 工作集保护；不相交场景 rebase 成功，同场和 legacy 不安全映射均拒绝。采用、discard 与 Run 启动引用候选的竞态测试必须使用相同项目锁顺序，证明不会生成已丢弃的活动输入。

**回退：** 可关闭新操作入口，保持 heads/流水可读；错误采用通过新的 restore 操作恢复，不能直接删除历史流水或回滚数据库至丢数据的旧快照。已记录请求的幂等读回应继续可用。

### W2-06｜接通完整作品审阅体验与阶段验收

**目标与依赖：** 依赖 W2-01 至 W2-05。让用户可以完成真实的版本决策，而非只在 API 上拥有 heads。用一套固定样本及 mock provider 验证业务闭环，再在具备 PostgreSQL/浏览器依赖的环境运行正式门禁。

**代码范围：** 现有 ArtifactContextPanel、ActionPlanCard、ScriptView、DiffView、Story Bible/outline/script/versions/exports 页面、api-client/types/hooks；后端新增方法相关单测和 PostgreSQL 集成测试；现有前端单元与 Playwright 流程。沿用阶段一已经交付的作品画布，不依赖阶段四 Task 接入。

**必须交付的界面状态：**

- 默认页明确显示采用版，旁边列候选来源、基于哪版、范围及结构/质量证据。无 adopted 的新项目提示“尚未采用”，可查看本次候选包；不能自动拿候选充当当前。
- 采用前展示具体范围、Diff、所需配套上游及受影响的下游；可逐集选或明确批量选。冲突后刷新当前 head，保留用户选择和输入，展示重新比较/安全 rebase/重做局改操作。
- 历史页可切回精确旧版，清楚区分“恢复候选可见性”和“切回采用版本”；已丢弃项默认折叠但仍能历史查看；运行正在使用的候选说明不可丢弃的具体 Run。
- 剧本版本与评价证据始终成对展示；普通导出显示冻结的采用版本，候选预览明确标记。生成进度和已采用集数分开显示。
- 计划确认卡显示将使用的 Bible、大纲、脚本精确版本及输入已过期状态；若使用候选必须明确。局改和后续任务都能从候选工作集继续，不强迫先全项目采用。

**端到端验收场景：**

1. 新项目从无 heads 生成 Bible→大纲→第 1/2 集候选；运行和候选预览正确；选择完整依赖包采用；普通页面与导出切到这些精确版本。
2. 第 2 集选区手改保存候选，另一场内容及 scene_id 不变；默认稿不变；审核 Diff 后采用；该版质量证据未生成时显示未评价。
3. 对同一基线发起 mock Agent 单场修改，模型恶意改其他场景时守卫拒绝；合法结果写入候选，运行事件和 provenance 可追溯。
4. 两窗口编辑/采用同一集：两份候选都可保留，只有一个 CAS 采用成功；第二份安全跨场 rebase 可以创建新候选，同场冲突保留双方并要求重新处理。
5. 大纲候选只在显式 workset 内影响新剧本；部分采用缺父拒绝；单独采用新大纲保留旧剧本并显示 stale；旧 Run 恢复仍用自己的冻结输入。
6. 旧项目迁移、旧 v1 剧本阅读→首次局改→旧版 Diff→切回；已有导出快照和历史评价仍指向原 ID/checksum。
7. 丢弃未使用候选后默认候选列表隐藏，history/exact read/Diff/已冻结导出仍有效；采用版和活动工作集成员不可丢弃；恢复候选后可再次参与显式选择。

**门禁与交付证据：** 按仓库现有 Makefile/包配置运行定向后端测试、前端测试和 typecheck/lint，再运行既有全量检查；正式浏览器回归覆盖上述关键路径，并在支持 `make e2e REPEAT=1`/既有重复模式的合格环境执行。以实施时脚本为准，不新建虚假的平行测试命令。本计划未执行这些检查；若本机缺 Docker、PostgreSQL 或仓库启动脚本依赖，分别报告源码检查、mock 浏览器验证和正式集成门禁状态，不能把 mock 通过写成正式通过。不以真实 LLM 调用作为确定性验收前提。

交付物包括可执行迁移与 dry-run 报告样例、上述测试结果、采用/冲突/丢弃/restore 界面证据、所有默认读入口迁移表、legacy/新语义运行恢复说明。全部通过后才能开启新项目默认 阶段二；旧项目逐项目 activation。

**回退：** UI 可回退到只读采用版与历史页；保留兼容后端和 heads 解析。阶段四开始前无需重新迁移 Artifact/scene/workset，只让演进后的 Task 继承它们。

### 阶段二验证命令与估算

下列新测试文件和维护命令是明确的实施交付物，并非当前已存在或已通过。

| 卡片 | 新增/扩展的主要测试 | 估算 |
|---|---|---|
| W2-01 | 新增 `backend/tests/integration/db/test_artifact_selection.py`：约束、旧项目 activation、事务回滚、跨项目幂等 | 1–1.5d |
| W2-02 | 新增 `backend/tests/integration/api/test_adopted_context.py`：上述默认读入口与候选 workset；扩展现有 continue/export 测试 | 1.5–2d |
| W2-03 | 新增 `backend/tests/unit/tools/test_script_edit.py`，扩展 diff/domain contracts：scene 身份、Unicode、范围守卫 | 1–1.5d |
| W2-04 | 新增 `backend/tests/integration/api/test_artifact_edits.py` 与 `frontend/tests/script-edit.test.tsx`：手改/Agent 共用保存与冲突保稿 | 1.5–2d |
| W2-05 | 新增 `backend/tests/integration/api/test_artifact_adoptions.py`：CAS、依赖、操作收据、discard/restore/rebase 竞态 | 1.5–2.5d |
| W2-06 | 新增 `e2e/artifact-review.spec.ts`；扩展工作台、Diff、导出前端用例与旧稿回归 | 1.5–2.5d |

后端定向命令：`cd backend && uv run pytest tests/integration/db/test_artifact_selection.py tests/integration/api/test_adopted_context.py tests/unit/tools/test_script_edit.py tests/integration/api/test_artifact_edits.py tests/integration/api/test_artifact_adoptions.py tests/integration/api/test_run_continue.py tests/integration/api/test_exports.py`。

前端定向命令：`cd frontend && pnpm exec vitest run tests/script-edit.test.tsx tests/agent-workspace.test.tsx tests/diff-view.test.tsx tests/exports.test.tsx`。阶段收口执行本文“共用验证”的正式门禁与根目录 e2e 流程，合计约 8–12 人日。

### 依赖顺序与主要源码证据

顺序为 `W2-01 → W2-02`，`W2-03` 在数据契约定稿后可并行；`W2-02 + W2-03 → W2-04 → W2-05 → W2-06`。采用事务实现骨架可随 W2-01 开发，但开放操作必须等待工作集、依赖和编辑元数据完成，避免“有采用按钮，后续任务仍读 latest”的半切换。

以下行号为编写计划时快照，实施时以当前文件定位为准：

| 现有证据 | 设计含义 |
|---|---|
| `backend/app/db/repositories/artifacts.py:35` highest valid；`:78` input-hash 查询；`:86` 精确 script 评价查询 | 新增 adopted 查询，不篡改物理 latest；编辑幂等项目隔离；复用精确版本评价能力。 |
| `backend/app/artifacts/store.py:89-134` 去重、版本分配和整事务 rollback；`application/artifact_service.py:99-157` 结构校验/创建/最新读取 | 扩展现有写入口；显式处理复合事务重试与回滚边界。 |
| `backend/app/workflows/nodes/revise.py:153-190` draft 写入；`continuity_check.py:202-209` 升 valid | 质量与结构状态分离需兼容旧 Run，不能只新增候选枚举。 |
| `backend/app/domain/script.py` 场景只有 scene_number；`tools/diff.py:215-285` 编号/相似度对齐 | 新增稳定 scene_id；相似度结果不能承担自动编辑锚点。 |
| `backend/app/tools/script_text.py:211-212` 无大纲时生成随机引用 UUID | 迁移不能把随机引用当已验证依赖；v2 允许明确空依据。 |
| `backend/app/application/agent_context_service.py:62-65,249`；`agent_command_service.py:521,1127-1226` | 默认最新、确认快照、evaluate 配置丢失快照等处必须共同改，才有冻结输入。 |
| `backend/app/application/agent_action_lifecycle.py:552-557`；`workflow_dispatcher.py:559-580,682-700`；`run_service.py:279-284` | follow-up、评估/修订分派、继续运行同属工作集切换边界。 |
| `backend/app/workflows/nodes/prepare_conversational_revision.py:79-93`；`application/revision_service.py:129-136`；`nodes/revise_outline.py:104` | 精确源剧本不能搭配执行时最新上游。 |
| `backend/app/application/evaluation_service.py:189-212` | 增加 continues 后必须按关系目标类型解析，不能靠链接顺序识别大纲。 |
| `backend/app/application/export_service.py:149-163,198-223`；`api/v1/exports.py:114-125` | latest 聚合与 worker 延迟解析都会越过采用边界；复用阶段一导出快照修复。 |
| `backend/app/workflows/nodes/write_episode.py:182-220`；`workflows/import_file.py:196-207`；`nodes/finalize.py:52-60` | 生成/导入均写候选工作集；“已生成”不能充当“已采用”。 |
| `frontend` ArtifactContextPanel、ActionPlanCard、story-bible/outline/scripts/exports 页面 | 多个组件直接请求 latest 或自行挑最大版本，后端新表本身不会改变用户看到的当前作品。 |

---

<a id="stage-3"></a>

## 阶段三：剧情状态与设定共创

### 目标与边界

阶段结束后，作者能够修改并采用设定，按小批次续写，退出后恢复；Agent 使用与当前工作稿完全匹配的剧情状态，能区分作者的未来安排和角色此刻已经知道的事情。前文采用新版本后，系统显示哪些后续内容需要复核，按需重建派生状态，保留已有正文。

本阶段接在阶段二 后，沿用它的三个契约：

- `artifact_heads(project_id, artifact_type, episode_number, artifact_id, revision)`：仅对 `story_bible`、`episode_outline_set`、`script_draft` 建采用头；`artifact_operations` 记录原子多头切换及幂等收据。
- `ArtifactWorkset={schema_version:1, revision, baseline_heads:[{artifact_type,episode_number,artifact_id|null,revision}], artifacts:[ArtifactSnapshot]}`。`ArtifactSnapshot` 严格使用 `{artifact_id,artifact_type,episode_number,version,checksum}`。`Action.plan.workset`、`Run.config_snapshot.workset` 冻结输入，`Run.state_summary.workset` 保存节点提交/暂停边界后的当前工作稿集合。
- 场景已有稳定 `scene_id`；候选来源通过 `Artifact.source_artifact_ids` / `ArtifactLink` 记录，前后集通过 `continues` 边绑定具体稿件。

**所有操作继续使用现有 AgentAction / WorkflowRun / Event / checkpoint。阶段四的 Task、任务预算和工具循环不是本阶段依赖。** 不新增工作集表、图数据库、向量数据库、Agent 角色或常驻摘要进程。剧情证据进入现有 Artifact，不增加采用头。

现状证据：

| 现有实现 | 本阶段需要补齐的运行语义 |
|---|---|
| `backend/app/domain/summary.py:29` 的 `SummaryOutput` 已有角色变化、伏笔、时间线，但多为 `list[dict]`；`skills/summarizer.py:122` 只做基本字段检查。 | 变化类型、引用、原文证据和模型不能填写的权威字段需明确。 |
| `memory/continuity.py:105` 已有纯函数状态更新；`domain/continuity.py:30` 已有角色、伏笔、事件 ID。 | 不重造状态机；接通 typed delta、实际版本来源和确定性回放。 |
| `workflows/nodes/write_episode.py:70` 每次重建初态，`:102` 跳过已有集，`:201` 用标题/动作截断生成摘要。 | 分批恢复需装载确切前文状态，不能因脚本存在就视为记忆已完成。 |
| `workflows/nodes/continuity_check.py:49` 单独重建标题/动作摘要。 | 写作与修订统一使用同一个版本化剧情状态服务。 |
| `application/artifact_service.py:40` 已注册 `continuity_state`；State 仍只有 `continuity_state_text`。 | 新状态存 Artifact，checkpoint 只保存引用和进度。 |
| `StoryBible.locked_facts`、`forbidden_changes` 都是文本列表；`memory/continuity.py:65` 给初始伏笔按下标编号。 | 事实/初始伏笔稳定 ID、确认来源与时序信息需有兼容读法。 |
| `workflows/nodes/retrieve.py:158` 用一个需求查询检索三阶段；`:187` 检索失败降级。 | 保留可选资料降级；必要正文/已确认设定/剧情状态不可使用空串兜底。 |
| `prompts/manifest.yaml:102` 的 summarize_episode output_schema 是 EpisodeSummary，模板 frontmatter 与 Skill 实际输出却是 SummaryOutput。 | 在新版本注册时一起修正契约，不沿用不一致声明。 |

### 固定设计决策

#### 1. 一集的正文与剧情记忆是两个提交点

先保存候选正文及工作集引用，再生成并保存 `episode_summary`（包含该集摘要和状态 delta）与 `continuity_state`（该集结束快照）。派生成功后才允许下一集依赖它。

如果摘要失败，正文继续存在，界面显示“第 N 集已生成；剧情状态待恢复”。Run 保存 `derivation_pending_episode=N`、目标脚本 ID、错误原因及最后一个完整状态 ID，后续生成停止。重试先补派生，不重新生成已经落库的正文。不得把失败改写为标题摘要成功，也不得回滚用户可见的候选正文。

#### 2. 版本来源、哈希与有效区间

新增 `EpisodeSummaryArtifactV2`，继续使用已有 `continuity_state` 类型；两种证据的 content_schema_version 固定为 2.0，envelope 如下：

| 对象 | 必需内容 |
|---|---|
| `EpisodeSummaryArtifactV2` | `episode_number`、`script_ref`、`story_bible_ref`、`prior_state_ref`（第 1 集可指向 through=0 初态）、`input_digest`、`derivation_version`、`prompt_version`、`schema_version`、`provider/model`、`summary`、`delta`、`inference_proposals`。来源字段全部由服务端填写。 |
| `ContinuityStateArtifactV2` | `through_episode`、`story_bible_ref`、`previous_state_ref`、`episode_summary_ref`、`basis_digest`、`state`、`state_content_digest`、`reducer_version`。初态 through=0 的 previous/summary 为空；ORM 的 episode_number 保持合法值 1，content.through_episode=0 纳入去重键，避免与集末状态混淆。 |
| `delta` | 有限类型的角色状态变化、人物知识变化、关系变化、新伏笔/伏笔回收、时间线事件；每项有来源脚本/scene_id/字段路径/原文引用，删除和反向变化必须显式表达。 |

`input_digest` 哈希**实际送入 Summarizer 的规范化输入**：当前脚本内容、设定投影、前态投影，以及 Prompt/Schema/提取策略版本、provider/model 标识。前态投影可以省略来源 UUID 和过去摘要的叙述性全文，但不能省略提取器实际依赖的人物/伏笔/知识状态。该投影是明确的纯函数并受契约测试覆盖，不能先按“似乎不相关的人物”猜测裁剪。

`basis_digest` 哈希有序基线：设定引用 + 按集号排序的 1..N 脚本引用及 checksum + reducer 版本。复用现有 `compute_checksum` / `compute_input_hash`；有序列表通过规范化 `dedup_extra` 纳入，不改写历史 artifact 的旧哈希算法。全局的采用头 revision 不放入内容缓存哈希，避免无关头变化使所有缓存失效；它保留在 Run 输入快照与并发校验中。

缓存分两层语义：相同来源基线直接复用原 Artifact；来源 ID 改变但实际输入内容完全一致，可以复用已校验的提取内容并生成**新来源 envelope**，不让新稿引用旧稿的证据身份。原输入不同则不能仅凭输出看起来相似跳过提取。

有效区间按剧中时间表达：`episode_summary` 只描述该脚本版本的第 N 集；through=N 的状态表示第 N 集末尾、可供第 N+1 集使用，在相同基线下成立。它没有跨版本自动有效期。事实的 `effective_from_episode` / `effective_to_episode` 是半开区间 `[from,to)`，未知结束为 null；场内知识变化还带 `scene_id` 与发生顺序。为第 N 集做前态检查只读 through=N-1，不能装入该集之后的“未来状态”。

#### 3. 候选工作集与项目采用稿严格区分

对一个 Run，剧情来源优先使用其冻结的 `ArtifactWorkset.artifacts`；同一批内第 N+1 集引用第 N 集新候选及其新状态，不隐式移动项目采用头。服务端加载所有引用并核对项目、type、episode、version、checksum，不能仅信任客户端或 checkpoint 里传来的文本。

新的独立创作操作默认从当前采用头构造工作集；作者明确继续审阅候选时，使用该候选所在工作集。旧 Run 恢复保持它原有输入，不能自动混入期间产生的新采用稿。要跟随新的采用基线，先在安全边界结束/暂停旧执行段，再按现有 Action/Run 入口生成新的执行配置；无需等待 Task。

候选状态可以出现在 `workset.artifacts`，但不进入 `baseline_heads` 和采用闭包。每个证据槽仅保留当前引用；through=0 初态与第1集末状态占同一 type/episode 槽时替换当前引用，旧初态通过 previous_state_ref 仍可访问，不违反工作集槽位唯一性。采用正文的依赖校验仍由阶段二 负责；本阶段在采用成功之后检查当前 adopted chain 是否已有匹配剧情证据，缺少则标为待派生。

#### 4. 设定、推断、角色知识和未来计划是四种不同内容

- **已确认事实**：服务端记录事实 ID、确认来源（用户消息或编辑/确认请求）、所在设定版本及有效区间。可选保护规则，例如角色 A 在第 8 集前不得得知事实 F。
- **模型推断**：有引用的提议，不能由模型自行设为“用户已确认”，不自动写入硬约束或人物已知信息。正文显式陈述且经提取校验的剧情变化可以更新派生状态，但其 authority 仍是正文证据，不是用户设定确认。
- **人物知识**：用 `character_id + fact_id/claim_id + knows/believes/suspects + acquired_episode/scene + evidence` 记录。角色误信和怀疑不升级为世界真实事实；“忘记/获知真相”是明确变化。
- **作者未来安排**：如第 8 集揭晓、未来反转、预期回收。只进入作者规划区和生成约束，不能初始化为“已经发生的事件”或“角色已经知道”。模型不得从未来大纲向当前角色状态搬运事实。

旧 `locked_facts` / `forbidden_changes` 通过兼容读取保留原有保护，标记 `origin=legacy_protected`，不伪称用户确认；旧 Artifact 不回写。首次新版本生成时由服务端分配/保留 fact/loop ID，之后编辑文本或排序不重新编号。整个候选设定被采用，与其中某条事实被显式锁定是两类作者决定；确认事实创建新的设定版本或明确确认记录，不能原地改候选 content 的 authority。

#### 5. 采用变更后仅做必要重算

采用事务只更新头、记录 `artifact_operations` 与轻量影响记录，不调用模型、不批量重写正文。找出新旧工作版本集合的最早变化集 K：1..K-1 完整相同的状态复用；K 之后按用户下一次需要的范围惰性派生。旧状态保留用于旧稿/旧 Run，不把 Artifact.status 改 invalid。旧导入稿中不存在的 referenced_outline_artifact_id 标为 unverified_legacy；新稿明确没有大纲时保持 null，不能为了依赖链伪造大纲。

精度采用保守策略：有完整来源链时沿 `continues/derived_from/references` 找受影响后集与连续性报告；旧数据或未覆盖语义依赖的情况，标记 K 及后续已有集“需要复核”。不能因为第 4 集没有直接提到变更人物就宣称它安全。先避免不必要的全文生成；状态提取后缀是否可跳过，以实际输入哈希相同为唯一依据。

#### 6. 必要上下文失败即停止依赖生成，可选资料失败可降级

必需内容由当前操作声明：连续续写需要精确目标/前文稿、采用/工作集设定、适用受保护事实、连续前文状态和当前大纲片段；已导入独立原稿的局部编辑只要求该操作实际依赖的来源，不凭空强制补齐大纲或前集。缺上下文的局改不能声称通过完整跨集连续性。缺失、跨项目、checksum 不符、前文有空洞、状态基线过期、保护内容超过预算时，返回可恢复错误；保留已生成正文，禁止下一次依赖它的模型生成。

可选：创作技巧和参考资料的向量检索。RAG 不可用时记录告警并继续；若用户明确把某份资料设为本次必读输入，则将它纳入必需来源，不能仍按“RAG 可选”处理。事实查询走结构化 Artifact 引用，不以向量召回来决定是否遵守锁定事实。

### 任务卡

### W3-01　类型化剧情变化、设定事实与来源契约

**依赖 / 估算**：阶段二的 ArtifactSnapshot、scene_id、兼容读取；0.75–1 人日。

**交付行为**：所有派生状态能回答“从哪一稿的哪一场得到、在哪一集有效”；模型不能伪造事实确认或把未来安排写入已发生状态。

**修改面**：

- `backend/app/domain/summary.py`：新增 v2 typed delta、摘要 artifact envelope；保持旧 SummaryOutput 可读。
- `backend/app/domain/continuity.py`：状态 envelope、来源引用、知识/关系变化与区间验证；领域 reducer 仍保留纯函数。
- `backend/app/domain/story_bible.py`：稳定事实/初始伏笔 ID、事实来源与确认、未来安排、角色信息保护规则；旧文本列表由兼容 adapter 投影，禁止双份可编辑事实源。
- `backend/app/domain/enums.py`、`application/artifact_service.py`：新增 `episode_summary` schema 注册，continuity_state 按 content_schema_version 选择 v1/v2，不能用 v2 模型强读全部历史。
- `backend/app/prompts/manifest.yaml`、`prompts/loader.py`、`llm/openai_compatible.py`：同步新的摘要 schema 到 summarizer 模型路由；新增模板版本，旧版本继续可加载。

**API / Schema / 迁移**：本卡不新增写 API。StoryBible v2 与剧情证据 v2 是内容 schema 升级；Artifact type 为现有 String 列，新增类型不需要新数据表，但需同步后端/前端类型和领域类型注册；当前 Artifact.type 为无类型 CHECK 的 String 列，阶段二也不增加类型枚举 DB 约束。旧条目不 UPDATE；不在部署迁移中调用模型补摘要。fact/loop ID 的兼容映射结果必须可重复，并在下一次内容新版本中固化。

**测试**：在现有 contract/skills 测试组织中新增 `backend/tests/contract/test_story_state_v2.py`；覆盖未知角色/伏笔引用、重复 ID、区间倒置、未来事件进过去状态、伪造 confirmation、正文 source 不符、旧 schema 可读。执行：`cd backend && uv run pytest tests/contract/test_story_state_v2.py tests/unit/skills/test_summarizer.py tests/unit/memory/test_continuity.py`。

**人工验收**：查看一个旧项目与一个新项目，同一事实重排、改措辞后 ID 不变；模型提议与用户锁定清晰区分；没有“迁移后自动用户确认”的显示。

**回滚**：关闭 v2 写入入口，继续兼容读取 v1/v2；新字段与新 Artifact 保留。不要用旧 StoryBible writer 覆盖含 v2 事实语义的稿件。

### W3-02　接通 Summarizer，持久化摘要与状态 delta

**依赖 / 估算**：W3-01；1–1.5 人日。

**交付行为**：每一集候选正文生成后，产出绑定该稿件的摘要和状态；失败可恢复且不丢正文。

**修改面**：

- 复用 `backend/app/skills/summarizer.py`，将人物/伏笔/时间线提取改为 v2，补关系变化；模型输出服务端校验后再交 reducer。
- 新增一个应用服务 `backend/app/application/story_state_service.py`，负责初态、按来源查缓存、调用 Summarizer、引用验证、纯 reducer、两个证据 Artifact 的原子保存；不让 Skill 访问 DB。
- `backend/app/memory/continuity.py`：只允许白名单字段更新，禁止任意 `dict` 传入 `model_copy(update=...)` 绕过验证；reducer 输出重新经 Pydantic 完整校验。
- `backend/app/artifacts/versions.py` 复用现有 hash；`db/repositories/artifacts.py` 增按项目/type/input_hash/source 查派生记录的最小查询，不新增通用缓存类。
- `backend/app/prompts/templates/episode_summary_v2.md` 与 manifest：原文依据、未来信息隔离、变化类型、已有 ID 引用；初态/新事件 ID 由服务端配置的稳定规则分配，不让各集都生成 `loop_001`。

**API / Schema / 迁移**：无新公开 API；由当前 Run 调用 `ensure_state_through(db, workset, through_episode, runtime)`。现有 Artifact 幂等为查询去重，给新派生类型加项目/type/episode/input_hash 的 partial unique index；迁移文件固定为 `backend/migrations/versions/0013_story_state_dedup.py`，索引仅覆盖 episode_summary 和 continuity_state 的 `content_schema_version=2.0` 且 input_hash 非空记录。同输入竞争通过唯一键重新读取，不能写重复有效证据。只给新类型/新 schema 范围加索引，避免旧重复数据阻断升级。

**事务和失败要求**：正文提交与派生提交分开；摘要+状态+引用+DB 事件在一个短事务里保存。模型调用前后不持有长事务。提交后通知 SSE；SSE 失败不重算摘要。中途崩溃时先按 input_hash 发现已完成记录；不能保证远程模型请求在提交前崩溃时绝不重发，但必须保证数据库结果不重复、已提交调用不重复。

**测试**：新增 `backend/tests/integration/memory/test_story_state_service.py`，使用内容感知 FakeLLM；覆盖源稿变更/Prompt版本变更不误复用、正文保留后派生失败、提交后重试零模型调用、关系/知识/伏笔进入状态、跨项目引用拒绝、并发唯一。执行：`cd backend && uv run pytest tests/integration/memory/test_story_state_service.py tests/unit/skills/test_summarizer.py tests/unit/memory/test_continuity.py`。

**人工验收**：第 1 集揭示一条秘密、人物只在第 2 集得知；状态分别显示作者事实和角色知识。模拟摘要失败，看得到候选稿和“恢复剧情状态”入口，恢复后正文 checksum 不变。

**回滚**：停止新的剧情派生 Run/写入，已有正文和派生证据均保留；没有有效状态时禁用依赖续写，不能退回标题摘要冒充成功。

### W3-03　统一写作、改稿和分批恢复的状态读取

**依赖 / 估算**：W3-02；1–1.5 人日。

**交付行为**：整批写作、分批续写、失败重试和单集改稿，使用相同基版本的剧情状态。

**修改面**：

- `backend/app/workflows/nodes/write_episode.py`：进入写作循环前，按工作集装载 through=start-1 状态；已有脚本只跳过正文生成，仍检查其派生证据是否齐全。每集写后调用 W3-02 并推进当前 workset 的证据引用。
- `backend/app/workflows/nodes/continuity_check.py`：移除标题/动作截断回放，调用同一状态服务；检查第 N 集使用 through=N-1，避免先把被检查的新稿写入前态。
- `prepare_conversational_revision.py`、`select_revision.py` / `revise.py`：沿用阶段二固定工作集，禁止临时查询 latest valid；旧稿修订按作者明确选择的基线读上下文。
- `backend/app/workflows/state.py`：新增 `continuity_state_artifact_id`、`episode_summary_artifact_ids`、`derivation_pending_episode`；旧 `continuity_state_text` 仅用于旧执行语义兼容，新版不依赖其内容恢复。
- `workflow_dispatcher.py`、`run_service.py`：配置写入 `story_state_semantics_version=2`；恢复补丁及新批次只拷贝正确工作集/引用，不把已完成写作节点标记误用于新批次。

**API / Schema / 迁移**：复用 `/runs/{id}/retry` 与 `/runs/{id}/continue`；Run response/state_summary 可增加 `story_state_status=ready|pending|failed`、待派生集及可恢复错误。无需新表。原 Run 的故障恢复使用原 run_id checkpoint；作者另选新采用基线时由既有动作创建新 Run，不能把它当原 Run 故障重试，也不需要 Task。

**测试**：扩展 `test_creation_workflow.py`、`test_staged_creation.py`、`test_run_continue.py`，新增 `backend/tests/integration/workflow/test_story_state_recovery.py`。对比一次写 5 集与 1+1+3 分批并模拟进程重启，逐集捕获 Writer 和 Summarizer 实际输入，断言其正文基线/角色知识/伏笔一致；多次派生失败后恢复不重复生成前集。执行：`cd backend && uv run pytest tests/integration/workflow/test_story_state_recovery.py tests/integration/workflow/test_staged_creation.py tests/integration/api/test_run_continue.py tests/unit/revision/test_continuity_check.py`。

**人工验收**：同一批内未采用第 1 集可供第 2 集使用；另开独立创作仍基于采用稿。关闭页面并重启服务后继续，页面显示相同候选包，前集关系变化不消失。

**回滚**：旧语义 Run 继续由旧路径完成；新语义 Run 在安全边界暂停，保留 v2 只读和恢复能力。不得将 v2 Run 路由给只认识 continuity_state_text 的旧 worker。

### W3-04　采用变更后的影响记录与惰性重建

**依赖 / 估算**：W3-03、阶段二的采用事务和 artifact_operations；0.75–1.25 人日。

**交付行为**：采用第 2 集新版本后，第 1 集状态复用，第 2 集及依赖后集标记待复核；历史正文、历史报告均保留。

**修改面**：

- 扩展阶段二的头切换应用服务：提交时比较新旧 ArtifactWorkset 的创作基线，记录最早变化集、变更事实/角色 ID（有则提供）、受影响正文/连续性检查引用。
- `backend/app/tools/outline_impact.py`：复用现有字段比较与来源图；增加 StoryBible 事实/角色/初始伏笔变化的输入分支或同文件纯函数，不引入第二套通用依赖框架。只改变 arc/未知全局规则时采用保守范围。
- `story_state_service.py`：提供 `resolve_status`（只读）和按需 `ensure_state_through`；只有前缀来源完全相同才复用；实际输入哈希相同才复用提取结果。过期是“相对某个当前基线”的状态，不能全局把旧 Artifact 标 invalid。
- `db/repositories/artifacts.py`：补齐向后来源查询；状态链 source 中包含确切前态和脚本，让重算具备证据。

**API / Schema / 迁移**：新增只读 `GET /projects/{id}/story-state?through_episode=N&run_id=<optional>`：默认采用集合，有 run_id 则解析该 Run 工作集；返回 basis_digest、ready/missing/stale、through_episode、source refs、warnings。GET 不调用模型、不修改状态。影响信息作为一个 `story_state_impact` 证据 Artifact，source 指向新旧创作版本，content 包含 adoption_operation_id，服务端校验其 kind 为 adopt/restore/initialize，借现有 artifact_operations 事务保存；不新增 freshness 表。人工“更新剧情状态”复用 `POST /projects/{id}/runs` 的受限 `action=refresh_story_state`（source run/workset 引用与 through_episode），经服务端解析、现有单项目执行槽与预算运行；禁止客户端任意传来源正文。

**测试**：新增 `backend/tests/integration/artifacts/test_story_state_invalidation.py`；第 2 集采用变化、只改标题的可复用分支、事实变化、无完整旧依赖时保守标记、撤销采用、旧 Run 固定快照、GET 零模型调用、刷新只重算所需后缀。执行：`cd backend && uv run pytest tests/integration/artifacts/test_story_state_invalidation.py tests/unit/tools/test_outline_impact.py tests/integration/memory/test_story_state_service.py`。

**人工验收**：采用新第 2 集后立即看见第 3–5 集待复核原因；不触发任何正文改写。只请求写第 3 集时仅重建需要的前态；切回旧采用版本可复用旧状态且旧证据仍能定位原稿。

**回滚**：关闭刷新按钮和新增自动派生，保留影响标记、采用头语义及只读证据；不删除 artifact_operations / impact Artifact，不清除“待复核”误导作者。

### W3-05　设定修订、事实确认与首次创作设定门

**依赖 / 估算**：W3-01、W3-04；1–1.5 人日。

**交付行为**：首次创作可停在 StoryBible，用户改人物/锁定事实、对比并采用，再生成大纲；已有项目可修订设定并先查看影响。

**修改面**：

- `backend/app/domain/story_bible.py` 增 `StoryBibleRevisionInput/Result`；新 `skills/story_bible_reviser.py` 复用 BaseAgent/StructuredOutputParser，生成完整 StoryBible 候选，保留未授权 fact/character/loop ID 和确认元数据。人工改设定扩展阶段二 `POST /artifacts/{id}/edits`：按 Artifact 类型增加 StoryBibleEditRequest，仅开放明确的角色、事实、保护规则字段，由 ArtifactService 保存新候选，不经模型；复用 base checksum、operation_key、采用 CAS 和不可变保存，不能把剧本 patch 强套到设定。
- 新 `backend/app/workflows/story_bible_revision.py` 使用现有 revise_outline 的小图模式：装载固定来源→生成候选→字段/保护校验→影响摘要→保存；不另外增加 Agent 类。
- `domain/agent_command.py`、`agent_planner.py`、`agent_command_service.py`、`workflow_dispatcher.py`、`workflows/router`、`llm/fake.py`：接通 `revise_story_bible` 意图及 Run action。按钮直接构造同一受限 command；自然语言明确修改设定才交 Planner。available_intents、语义白名单、schema/prompt/golden 一并更新。
- `creation.py` / `nodes/story_bible.py`：新增设定后条件边，`stop_after=story_bible` 时记录 `stage_gate=story_bible` 并 END；`run_service.continue_gated_run`、Dispatcher、Action lifecycle、Outcome 的设计内 gate 分支、前端枚举均扩展为 story_bible/outline/scripts，不能只改图不改状态消费端。
- 设定门继续时使用明确采用/候选工作集版本；如果请求“采用后生成大纲”，调用阶段二采用服务的原子确认后再调既有继续入口。不得以 latest valid 选择任意未采用新设定。续跑只执行 outline 之后，不重跑 normalize/SB；旧 outline/scripts gate 行为保持。

**API / Schema / 迁移**：新增 `POST /projects/{id}/story-bible/revisions`（base_artifact_id、base_checksum、instruction、明确允许变化的 fact/character 范围、workset 依据、幂等键）返回 202 Run/Action；可按现有 `revisions.py` 风格落地。事实确认/解除保护是现有人工编辑服务的受限 SB 操作，需明确 author request，输出新版本，不允许模型填写 user_confirmed。采用仍走阶段二接口，不另建 SB adopt API。`backend/migrations/versions/0014_story_bible_revision_intent.py` 扩展 `agent_actions.intent` 的 CHECK；同步 domain/API/frontend 的 action 和 gate 枚举。旧 Run 未提供新 stop_after 时不能自动插入设定门。

**测试**：新增 `backend/tests/integration/api/test_story_bible_revision.py`，扩展 `test_staged_creation.py` 与 `test_run_continue.py`；覆盖固定事实未授权变化拒绝、用户明确解锁后候选+影响、事实 ID 保持、过期 base 409、首次 SB gate 未调 Outline、继续后仅调一次 Outline、所有三类 gate 的重复确认/失败恢复、旧 Run。执行：`cd backend && uv run pytest tests/integration/api/test_story_bible_revision.py tests/integration/workflow/test_staged_creation.py tests/integration/api/test_run_continue.py tests/integration/db/test_migration.py`。

**人工验收**：输入想法→看设定→“把姐姐改成竞争对手，但保留血缘”→查看改动与锁定事实→采用→生成大纲。已有项目改变“第 8 集才揭晓”时先展示影响，后续剧本没有自动变成新版本。

**回滚**：关闭新 SB 修改和设定门入口；已有 story_bible gate 的 Run 由兼容服务继续处理或保留暂停，不能回滚为“不认识的状态”。新增 intent 的历史行仍存在时不直接 downgrade CHECK 使历史不可读。

### W3-06　必要上下文保护与针对当前问题的资料检索

**依赖 / 估算**：W3-03、W3-05；0.5–0.75 人日。

**交付行为**：角色状态通过确切版本查询进入上下文；参考检索按本集问题进行；关键内容读取失败时明确暂停。

**修改面**：

- `backend/app/memory/context_builder.py`、`domain/context.py`：把当前用户约束、目标片段、适用锁定事实、必要前态投影列为 protected/required；保留 ContextBuilder 已有的 protected 超预算异常；另补 required 缺失/版本无效的结构化错误，不能仅记录 warning。超出预算时给“缩小范围/补充状态”恢复方式，不静默截断。
- `application/agent_context_service.py`、Writer/Reviser/Continuity 输入组装：使用工作集+state_ref，明确标记作者未来安排与角色当前知识；后者只来自 through=N-1 的状态与当前场内已验证事件。
- `workflows/nodes/retrieve.py` / `rag/retriever.py`：在每个实际写作/设定修订操作有具体目标后构造 query（本集冲突/场景目标/修改问题），继续复用 existing Retriever，保持项目过滤、类别过滤、trace 和 NullRetriever。查询/资料引用保存为该次执行依据，不把三阶段早期检索视作恢复后的新检索结果。
- 检索失败区分可选资料告警与必读资料失败；不把全部读取异常用一个 catch 后清空上下文。ContextManifest 增实际使用的 artifact_refs、state_ref、mandatory_source_ids、输入预算和明确降级原因。

**API / Schema / 迁移**：无新表/新动作。GET 状态与 Run error payload 加稳定错误码：`REQUIRED_CONTEXT_MISSING`、`STORY_STATE_STALE`、`STORY_STATE_GAP`、`PROTECTED_CONTEXT_TOO_LARGE`；继续沿用现有通用 ErrorResponse，不新增异常协议。RAG warning 与阻断错误不能同色同状态展示。

**测试**：扩展 `backend/tests/unit/memory/test_context_budget.py`、`tests/integration/memory/test_summary_reaches_writer.py`、`tests/integration/workflow/test_creation_with_rag.py`；负例必须断言“调用 Writer 即失败”的模型桩，证明必需来源缺失不继续。RAG 断开可生成，必读资料失效不可生成；项目 A 的事实不进入项目 B；作者未来真相不进入当前角色 known 信息。执行：`cd backend && uv run pytest tests/unit/memory/test_context_budget.py tests/integration/memory/test_summary_reaches_writer.py tests/integration/workflow/test_creation_with_rag.py tests/unit/application/test_agent_context_service.py`。

**人工验收**：删掉可选检索服务连接仍可续写并有提示；模拟确切脚本无法读取时，界面保留此前结果并指明缺少哪一稿，不出现“已完成续写”。资料页可以解释本次为什么用了该来源。

**回滚**：可关闭按目标的 RAG 检索，沿用无资料生成；必要来源 fail-closed、保护上下文和版本解析不得随功能开关撤掉。

### W3-07　共创视图、兼容迁移与阶段退出验收

**依赖 / 估算**：W3-01–06；0.75–1 人日。

**交付行为**：作者在同一工作台查看事实和人物知识、编辑设定、理解后续影响、恢复派生，避免看到“记忆任务/嵌入状态”等内部步骤作为主流程。

**修改面**：复用 `frontend/src/features/story-bible/StoryBibleView.tsx`、`CharacterCard.tsx`、`features/agent/AgentWorkspace.tsx`、`ActionPlanCard.tsx` 和已有作品画布；增加一个轻量剧情状态面板，显示“截至第 N 集”“采用稿/此候选包”“事实来源/角色已知/作者计划”“待复核/恢复”。评估/影响跳转使用阶段二的 ArtifactSnapshot+scene_id 定位。更新 `types/api.ts`、查询 hooks、提示文案及键盘焦点/状态朗读。

**API / Schema / 迁移**：只消费前卡 API。迁移脚本不得重写旧 Artifact 的 content/version/checksum；首次使用旧项目时按已冻结的采用稿懒派生。旧式标题摘要不升级为已验证 v2。migration upgrade/down-read 在含旧 Run、旧 SB/脚本、v2 候选及 adopted heads 的混合库上验证；为 DB CHECK/索引变更准备安全 downgrade 条件说明。

**最小固定验收场景**：

1. 同一项目一次 5 集和 1+1+3 分批，服务重启后捕获实际后续输入一致；只比较确定性 Fake 场景，不要求随机模型正文逐字一致。
2. 第 2 集候选未采用，批内第 3 集引用它；独立新操作和导出仍读取采用集；部分采用依赖不满足时拒绝。
3. 已生成第 3 集时摘要失败，候选仍可查看；retry 只补摘要/状态，完成后继续第 4 集。
4. 采用第 2 集新版本，第 1 集状态不重算，第 3–5 集标记需复核；只请求第 3 集时不重写第 4–5 集；旧 Run 的依据不被替换。
5. 作者知道秘密、女主怀疑、男主第 8 集才获知；第 3 集人物面板和实际 Prompt 没有把作者计划当已发生事件。模型引用存在但语义仍不确定时显示提议，不能声称规则已证明其正确。
6. 首创停在设定门→改设定→采用→大纲门→一集样稿；旧项目仍可从 outline/scripts 门恢复。
7. 可选 RAG 失败可继续，必需来源/状态失效或保护段超预算时停止，错误能回到具体稿件和恢复入口。

**测试命令**（均在实施后运行）：

- 后端先按前卡子集验证；阶段收口执行 `make lint`、`make typecheck`、`make test`，新增迁移执行 `make migrate-check` 与混合数据升级/兼容读取测试。
- 前端扩展 `frontend/tests/story-bible-outline.test.tsx`、`agent-workspace.test.tsx`，新增 `frontend/src/features/story-bible/StoryStatePanel.tsx` 与 `frontend/tests/story-state-panel.test.tsx`：`cd frontend && pnpm exec vitest run tests/story-bible-outline.test.tsx tests/agent-workspace.test.tsx tests/story-state-panel.test.tsx`。
- E2E 新增 `e2e/story-continuity-workspace.spec.ts` 完整场景，使用 FakeLLM/FakeEmbedder 与真实测试 DB；先以 `make e2e REPEAT=1` 冒烟，正式退出门执行仓库要求的 `make e2e REPEAT=5`。
- 人工验收使用既有固定 Fake 剧情走上述 7 条，再做键盘与窄屏查看/恢复。真实模型创作质量试用另列，经已有授权与预算执行；不把 Fake 通过写成主观质量提升。

**退出门槛**：旧版本改写为 0；未采用候选污染独立采用上下文为 0；缺少必需状态仍继续生成为 0；模型自授用户确认事实为 0；恢复后已保存正文被无故重生成为 0。每条状态引用能反查对应稿件与源场景。代码、测试、迁移、DEV_PLAN/DEV_LOG/API_CONTRACT/PROMPT_GUIDE/KNOWN_LIMITATIONS 同步后才可 DONE。

**回滚**：优先关闭本阶段新写入口，等待在途模型请求结束并在安全边界保存结果；保留采用头、v2 只读、已产生的事实确认、候选正文和影响提示。只回退 UI 不能让后端重新用 latest valid/标题摘要/空必需上下文。保留兼容服务使 v2 Run 可恢复，或明确保持暂停，不做删表/删 Artifact 的回滚。

### 排期与可裁剪范围

按熟悉仓库的单人全栈估算，7 张卡合计约 **6–9 人日**，不含真实创作试用等待。原评审的 4–7 人日是粗估；本计划补齐了状态来源、候选隔离、首创设定门和兼容恢复，宜以任务卡估算为准。

暂不做逐人物精准依赖分析、自动整季重写、事实图数据库、角色扮演 Agent 或新的调度框架；只有具体性能或准确性证据证明现有 JSONB 与有序依赖链不足时再追加。

---

<a id="stage-4"></a>

## 阶段四：让一个创作任务跨多轮持续执行

**交付物：**作者提出目标后，Agent 在授权范围内读取、改稿、检查，保留候选和证据；补充意见、暂停、预算不足或进程重启后，仍能接回同一个任务。阶段一至三已经可独立使用，本阶段不承担它们的基础能力。

**取舍：**复用现有 Action 存储、Run 调度、LangGraph 和 LLM 协议。将 `agent_actions` 的新记录解释为 Task，不新增 Task → Action → Run 三层，也不引入 Agent SDK、消息队列、动态工具市场或多 Agent 协商。数据库表名暂时保留，避免纯重命名迁移。

### W4-01｜从 Action 平滑演进为 Task

- **改动位置：**`backend/app/db/models/agent_action.py`、`agent_turn.py`、`workflow_run.py`、`db/repositories/agent_actions.py`、`domain/agent_command.py`、`api/v1/agent.py`；新增 `backend/app/domain/agent_task.py`、`backend/app/application/agent_task_service.py` 和 `backend/migrations/versions/0015_agent_tasks.py`。任务查询继续复用现有仓储，不再增加一个同表仓储。
- **数据约定：**`agent_actions` 增加 `execution_version`（旧记录为 1，新 Task 为 2）、`revision`（bigint，从 1 开始）、`pause_requested_at`、`cancel_requested_at`。v2 的 `intent=task`、`parent_action_id=NULL`、`replan_depth=0`、`run_id=NULL`；status 为 `open / needs_input / needs_review / completed / failed / cancelled`。是否正在执行由关联 Run 派生，避免两个运行状态相互覆盖。旧记录保留原意图、状态和结果；数据库约束按版本分支。
- **TaskSpec：**继续存入 `plan` JSONB，以 Pydantic 固定 `schema_version / goal / constraints / scope / allowed_operations / authorization / workset / progress / pending_feedback / required_decision / budget`。`workset` 直接采用阶段二的 `ArtifactWorkset`。约束含稳定 ID、原文、硬约束或偏好、检查方式；范围列出集数、场景 ID 和允许修改的设定字段。授权明确生成、检查、是否采用、可执行批次与纠错次数。默认不自动采用，明确要求“采用后继续”才进入授权记录。
- **关系：**`workflow_runs` 增加 `task_id`、`segment_index`、`lease_generation`（每次领取递增的 fencing token），唯一 `(task_id, segment_index)`；v2 Run.action 固定为 `task_segment` 并同步 API/路由枚举；每个 Run 固定 `task_revision / operation / authorization`，输入工作集唯一存放在 `Run.config_snapshot.workset`；下文 input_workset 仅指这份值，不新增同义字段。`agent_turns.task_id` 可空，用于后续反馈；Turn response 增加可空 task_id，status/type 扩展 task_updated/task 分支；初始 Turn 仍通过原 `agent_turn_id` 关联。Task 有多个 Run，Run 仍以自己的 `run_id` 作为 checkpoint thread_id。失败恢复使用原 Run；新的创作阶段或新反馈产生新 Run。
- **接口：**新增 `POST /projects/{id}/agent/tasks`，接收目标文本、活动上下文和幂等键，同时创建初始 Turn 与 Task；初始不够明确时返回 `needs_input`，明确的修改请求直接构成对应范围的生成授权。新增 `GET /projects/{id}/agent/tasks`（游标分页）与 `GET /agent/tasks/{id}`，返回任务、revision、当前 Run、按 segment 排列的 Run 摘要、候选 workset、预算和待决策项。旧 Action 读写接口遇到 v2 都返回 `TASK_API_REQUIRED` 和 Task 入口，避免旧 Response schema 误解 open 状态；旧 v1 API 和历史继续可读可用。
- **兼容控制入口：**v2 Run 的旧 `/continue`、`/retry` 和 Action confirm 不得绕过 Task revision/预算/停止状态，返回 `TASK_API_REQUIRED` 并指明 Task 控制入口。可恢复执行错误让 Task 进入 needs_input，保留失败 Run 与剩余操作；现有租约恢复在同一 Run 内最多尝试 3 次。次数耗尽后作者明确“重试剩余步骤”才允许新 segment，Task 预算与候选不重置，作为 continue 的带原因决策，不自动循环。只有不可恢复的数据完整性错误进入终态 failed。
- **验收：**相同请求重放只建一个 Task/Turn；相同幂等键不同正文返回 409；跨项目 Artifact/Task 被拒绝；旧 Action 确认、历史结果和分批继续回归通过；Task 不因等待审稿长期占据项目活动 Run 唯一槽。

### W4-02｜实现有界的读取、决策、执行、验收循环

- **改动位置：**新增 `backend/app/skills/agent_task_planner.py`、`backend/app/prompts/templates/agent_task_planner.md`、`backend/app/workflows/task_segment.py`；修改 `application/agent_task_service.py`、`workflows/router.py`、`creation.py`、`conversational_revision.py`、`outline_revision.py`、阶段三的设定修订工作流及 `llm/fake.py`。沿用现有注册机制，不再增加通用工具协议。
- **操作集：**`read_artifact / retrieve_reference / explain / create_story_bible / revise_story_bible / create_outline / revise_outline / write_episodes / revise_script / evaluate / refresh_story_state / request_input / finish`。局部修订沿用阶段二的范围合同。导入、导出和采用仍调用确定性应用接口，Agent 不能通过任意工具名、文件路径或模型文本绕过这些入口。
- **执行边界：**一个 segment 最多做 2 次补充读取、1 次专业写操作、1 次结果验收，最多 3 次 Planner 决策。用现有 `generate_structured()` 返回受限 Pydantic 联合类型；服务端逐项校验 operation、scope、输入版本和剩余预算。达到读取上限仍不明确则询问作者。失败重试仍走现有 LLM retry，不再外包一层无界 Agent retry。
- **专业工作流：**把既有写作/修订图作为固定子图调用；阶段型创建分别执行设定、大纲、正文批次，每个新 Run 从冻结的 workset 初始化，只携带明确输入，不复制上一 Run 的 `completed_nodes`。子图使用稳定节点名和 checkpoint namespace；同一 Run 恢复不重复写已完成 Artifact。v2 子图关闭既有 creation 的内部自动修订分支，由 Task 统一掌握纠错权，防止子图改一次、Task 又改一次；v1 保持原行为。模型选择下一操作，不能改写整个工作流拓扑。
- **纠错规则：**首次完成用户请求后，只在阶段一的约束检查得到明确 `unsatisfied`、且修复在授权范围内时，允许新 Run 自动修正一次；沿用 `max_revision_rounds=1` 和 `agent_max_replan_depth=1` 的上限意义。`unverified` 直接呈现证据缺口，不循环“改到通过”。每段授权的纠错消耗写入 Task progress，新 Run、故障恢复和 Planner 再建议都不能清零。后续作者的新反馈是新的授权修订段，获得自己的一次纠错上限；总预算始终累计。
- **结束语义：**候选生成和检查完成后进入 `needs_review`，即使全部检查通过也不直接 completed；作者仍可在同一 Task 提意见或采用。采用后没有已授权的后续工作才 completed；有后续工作则保持 open。纯解释或仅检查任务交付证据后可直接 completed。完成某个 segment 不等于完成 Task。
- **推进规则：**设定、大纲及样稿分别等待作者判断。明确授权连续写 N 集时，可按已授权小批次推进同一 Task，使用同一候选 workset；默认每批 1 集，可选 3 或 5 集，绝不越过目标集数。每个 segment 结束后先保存成果和证据，再决定下一个 Run；阶段三的 `state_ref / derivation_pending_episode / basis_digest` 和阶段进度一并保留，正文已存而派生未完成时先补派生，不重写正文或直接写下一集；出现设定变化、候选依赖冲突、预算不足或新增反馈时停止自动推进。
- **验收：**FakeLLM 固定轨迹覆盖“读取指定场景→局改→保留最后一句→检查”“检查失败→一次修正”“检查不确定→待审”“请求越权改下一集→询问”；断言实际调用数、写入范围和 Run 数上限；连续五次要求自我修正也只能消耗一次已授权自动纠错。已有专业图的创建、评估和修订断言继续有效。

### W4-03｜持久化反馈、作者决策和采用权限

- **改动位置：**`application/agent_task_service.py`、`api/v1/agent.py`、`domain/agent_task.py`、`application/agent_command_service.py`、阶段二的采用服务、`db/repositories/agent_turns.py`。
- **反馈接口：**现有 `POST /projects/{id}/agent/turns` 增加 `task_id`、`expected_task_revision`；两者必须同时提供。修改意见以 Turn 收据去重，在短事务中更新 Task revision 与 `pending_feedback`，反馈立即可见，但不改正在执行的输入。纯解释问题记录 Turn 并返回答案，不增加创作 revision。终态任务的新修改创建新 Task，并以旧 workset 作为显式输入，不复活历史终态。
- **决策接口：**新增 `POST /agent/tasks/{id}/decisions`，接收 `decision_id / expected_task_revision / idempotency_key / decision / payload`。decision 固定为 `answer / approve_scope / adopt / discard / continue / increase_budget / pause / cancel / resume`；`answer / approve_scope / adopt / discard / continue / increase_budget` 校验当前待决策 ID；pause/cancel 是任何非终态任务随时可用的控制操作，不依赖 required_decision，resume 只用于已暂停状态。控制操作可省 decision_id，仍需 revision 和幂等键。discard 只移除指定候选工作集入口，保留 Artifact，回到 needs_input 供新意见或结束，不隐式采用旧候选。采用 payload 仍使用阶段二的候选 ID、预期 head 和 revision，不能只传“采用最新稿”。确认不能回传并替换整个 TaskSpec。决策请求复用 AgentTurn 收据和 `(project_id,idempotency_key)` 约束，使用 decision 命名空间保存请求哈希与已接受响应，结果放入带版本的 planner_output/response metadata；按钮和文字反馈都能重放原响应，不用 Task 当前 revision 冒充旧回执。
- **运行中反馈：**完成当前已发出的模型调用后检查新 revision；可保存基于旧约定生成的候选，标记“尚未应用新意见”，不继续运行或自动采用。随后只从已明确关联本 Task/Run 的候选 ArtifactSnapshot 和有序反馈新建 segment，不查询 latest。硬约束、范围或预算的扩大须呈现明确差额；原授权内的措辞调整直接执行。连续多条反馈按 Turn 顺序一起纳入，不丢中间一条。
- **结果并发提交：**Run 的当前 workset、剩余步骤、正文/派生进度在每个安全边界持久化。Run 结束时，在短事务中校验 lease_generation、Task revision 和 expected_workset_revision 后，将 `Run.state_summary.workset` 回写 Task；并发反馈或采用已推进 revision 时，旧结果只保留为该 Run 的候选，不覆盖新工作集。Task 服务下一段显式选择这些候选并重新校验。
- **采用与继续：**手动采用通过阶段二的原子 CAS 更新 Task workset 的 baseline_heads；已经明确授权“采用后继续”时也执行相同的 CAS 和依赖校验，冲突必须返回作者。head CAS、artifact_operations 收据、Task revision、workset revision/baseline_heads、决策消费同一事务提交；阶段二 ArtifactService 不能自行 commit，提交后才排后续 Run。操作只改变明确候选槽，不批量采用项目中其他候选。采用授权与续写授权分开记录；“改第三场”的任务不能因采用就自行写下一集。界面显示“续写第 4 集”的具体范围和预算，作者点击即构成这段范围授权，不再追加泛化确认。暂停保留 Task 与候选，恢复仍属于同一 Task；取消为终态。
- **验收：**两个浏览器用相同 revision 提交矛盾决策，仅一个成功；重复采用或恢复不重复建 Run；运行中补充“结尾别动”后，旧稿不会冒充已遵守；取消后到达的延迟确认不能恢复任务；丢包重试不消耗额外授权；反馈/采用与 Run 完成同时发生不丢任何一方的工作集或意见。

### W4-04｜把预算移到已有 LLM 调用账本

- **改动位置：**`backend/app/llm/budget.py`、`openai_compatible.py`、`fake.py`、`structured_output.py`、`db/models/llm_call.py`、`application/workflow_dispatcher.py`、`application/agent_task_service.py`；新增 `backend/migrations/versions/0016_task_llm_budget.py`。保留协议和 provider 配置；现有 `llm_calls` 虽已建模，不能把它当作已经打通的持久计费实现。
- **数据约定：**`llm_calls` 增加可空 `task_id`、唯一 `invocation_id`、`reserved_tokens`、`reservation_state`（reserved/settled/unknown）、`settled_at`。每一次真实 HTTP 尝试单独记一行；`attempt` 和 prompt/input Artifact 信息沿用。Task budget 保存批准的上限和授权记录，用量从账本聚合，避免再维护另一套易漂移计数表。
- **扣量时点：**在 HTTP 请求开始前锁 Task 行，校验暂停/取消、revision、Run lease_generation 与预算；初始 Planner 尚无 Run 时校验对应 Turn 规划租约，预留一次调用及估计 token，提交事务后再访问模型。成功或明确失败后幂等结算一次；超时、worker 中断或请求已发出但结果不明保留占用，标为 unknown。网络 retry、Schema retry、Planner、总结和验收都经过同一入口；不能只在外层 Skill 计一次。
- **默认值：**初始 Task 沿用当前配置的软调用数 18、硬调用数 24、token 阈值 200,000；每个 Run 的现有限制仍有效。Task 跨 Run、恢复、人工反馈不重置用量。预算界面列明剩余次数、实际 token、未结算预留；增加预算必须给出明确增加的调用数和 token 数，写入一次性授权记录，不自动续杯。预算不足映射为 `needs_input(reason=budget_exhausted)`，当前 Run 在安全边界结束并保留候选、剩余步骤及派生进度；不得把 Task 设为终态 failed。increase_budget 后继续同一 Task，unknown 占用不清除。
- **精度边界：**调用次数是硬上限；token 采用现有估算器加本次输出上限预留，取得 provider usage 后结算。任意兼容 provider 的 token 估算可能不准，因此界面称“token 估算阈值”，不承诺精确货币封顶。结果不明时宁可保留预留，也不能把可能收费的尝试当零次。外部 provider 不支持幂等时，重启可能重复付费调用，账本记录每次尝试，Artifact 落库仍必须幂等。
- **验收：**两个 worker 争抢最后一次调用只放行一个；第 25 次不得发 HTTP；重启、超时、Schema 重试和跨 Run 继续不清零；同一结算回调重放不多计；数据库不可用时不开始新的付费调用。FakeLLM 与真实适配器走相同账本挂钩。

### W4-05｜补齐持续调度、租约隔离和可靠事件

- **改动位置：**`backend/app/application/workflow_dispatcher.py`、`run_service.py`、`backend/app/main.py`、`events/publisher.py`、`stream.py`、`schemas.py`、`workflows/persistence.py`、各公共 Artifact 提交路径。
- **调度：**保留 FastAPI lifespan 里的 dispatcher，增加每 5 秒、每次最多 20 条的过期租约/可运行 Task 扫描；不是新增后台服务。沿用 Run 30 秒租约、10 秒心跳、最多 3 次领取。领取短事务使用数据库行锁；一次仅启动满足项目活动 Run 唯一约束的 segment。每次正式新 segment 的重试计数从零开始，原 Run 故障恢复累计，不以“继续”清掉故障次数。
- **租约隔离：**Artifact、workset、Outcome 和结束事件提交前必须重新核对租约持有者、有效期和领取时的 lease_generation；失去租约的旧 worker 返回结果不能覆盖新 worker。实际模型请求不能放在行锁或业务事务中。锁顺序固定为 project → task → run → 产物槽 → conversation；只获取本次需要的锁，避免嵌套逆序。
- **Checkpoint 的同一租约保护：**`workflows/persistence.py` 在已有 AsyncPostgresSaver 外包一个面向当前 Run 的写入守卫，覆盖 `aput` 和 `aput_writes`。使用传给 saver 的同一 psycopg connection，在同一短事务内锁 Run、核对 owner/status/lease_generation 后调用原 saver 写入；事务之外再检查一次不够。守卫串行化这段同连接操作，不改官方 checkpoint 表/序列化格式，不给每次 lease 新建 namespace。已核对锁定的 checkpoint-postgres 3.1.2 支持传入 AsyncConnection；框架存储逻辑继续复用，不复制其 SQL。[官方 AsyncPostgresSaver 实现](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/aio.py)。新增“新 worker 已推进后，旧 worker 延迟提交 checkpoint/pending writes”的故障注入，旧写入必须被拒。
- **暂停和取消：**Task 的持久请求先落库，再唤醒 dispatcher；模型调用前、专业节点边界和 Artifact 提交前检查。已提交候选保留；用户暂停或取消后才返回的模型结果不进入后续写作、不改变 head。正在发生的外部模型请求无法保证撤回，预算仍记录。暂停请求持久写入 pause_requested_at；暂停后当前 Run 记为 cancelled，并在 state_summary 标明 stop_reason=paused、已保存 Artifact 和 remaining_operations，释放项目活动槽，Task 停在 `needs_input(reason=paused)`；取消终止 Task。恢复清除暂停请求，新建 segment 并继承剩余预算，只做 remaining_operations：写稿完成则从派生/检查继续，派生完成则从验收继续；输入改变先重新校验来源，不复制 completed_nodes，也不重新生成已保存正文。
- **事件：**沿用 `workflow_events`，payload 增加 `task_id / task_revision / segment_index`。业务状态、消息和 DB 事件在同一短事务提交，Redis 仅在提交后通知；禁止 event publisher 私自提交调用方未完成的事务。所有 SSE replay、Redis live 和 DB poll 路径都返回稳定 `id`，按数据库 sequence 去重；Last-Event-ID 必须属于当前 Run，否则 400。修复现有 stream 的阶段循环和局部 import 问题，覆盖 Redis 断线后的 DB 追赶。
- **验收：**在“任务已存但未排队”“Artifact 已存但 checkpoint 未确认”“Run 已完成但事件未推送”三个位置强杀 worker，恢复后不重复成果；旧 lease worker 无法提交；Redis 停机期间任务可完成且页面可追上；跨 Run 伪造游标被拒；等待作者的任务不产生空转 LLM 调用。

### W4-06｜把持续任务接入现有工作台并完成切换

- **改动位置：**`frontend/src/features/agent/AgentWorkspace.tsx`、`ConversationPanel.tsx`、`ActionPlanCard.tsx`、`frontend/src/hooks/use-agent-conversation.ts`、`use-run-events.ts`、`frontend/src/lib/api-client.ts`、`frontend/src/types/api.ts`；新增 `frontend/src/hooks/use-agent-task.ts`。旧 ActionPlanCard 继续渲染历史，新任务区域直接复用已有状态/决策组件。
- **用户界面：**常驻区显示目标、当前对象、已完成工作、下一项作者决策、剩余预算。运行节点和调用账本收进详情。Task workset 提供作品索引和新候选入口，GET Task 恢复任务状态；画布始终优先使用阶段一的 URL artifact/compare/scene，只有没有阅读选择时才设默认稿。任务完成、反馈或采用都不自动切走作者正在比较的版本。活跃任务每 2 秒查询当前 Run，订阅该 Run 的现有 SSE；切换 segment 后按服务端 Run 列表更新，不建立第二套 task_events 或跨 Run SSE 聚合服务。待审或暂停后停止频繁轮询，在用户操作、窗口回焦时刷新。
- **发布顺序：**完成 schema 扩展与双版本兼容后，先验证旧数据、再运行 v2 全链路，最后前端创建入口切到 `/agent/tasks`。不自动把旧 Action 链合并成新 Task。新提交须同步更新 `docs/DEV_PLAN.md` 的责任边界和验收记录；旧架构文档标注被替代条款。
- **验收文件：**新增 `backend/tests/integration/workflow/test_agent_task_runtime.py`、`backend/tests/integration/workflow/test_task_budget_recovery.py`、`e2e/agent-task.spec.ts`；扩展现有 `test_dispatcher_recovery.py`、`test_agent_action_events.py`。定向后端命令：`cd backend && uv run pytest tests/integration/workflow/test_agent_task_runtime.py tests/integration/workflow/test_task_budget_recovery.py tests/integration/workflow/test_dispatcher_recovery.py tests/integration/events/test_agent_action_events.py`；前端新增 `frontend/tests/agent-task.test.tsx`，执行 `cd frontend && pnpm exec vitest run tests/agent-task.test.tsx tests/agent-workspace.test.tsx tests/run-events.test.ts`。按本文“共用验证”执行工程门禁及 FakeLLM E2E。人工完成“改第三场→中途加意见→断网刷新→比较并采用→明确选择续写下一集”，不得依赖数据库手工修状态。
- **退出门：**一个 Task 跨至少 3 个 Run 保留目标、版本、预算和反馈；进程重启/重复请求不重复采用；作者能在运行中暂停、恢复、取消；“正文保存后摘要失败→暂停/加意见→恢复”只补必要派生和检查；每项目标展示来源或明确未验证。工程结果与真人剧作评价分开记录，不用 FakeLLM 成功冒充创作质量。
- **回退：**停止创建 v2 Task；已有 v2 由兼容版本只读展示或排空，旧 v1 入口继续可用。保留新增列和账本，不做会丢关系的生产 downgrade。不能直接回到完全不识别 v2 的旧 worker，必须先暂停并排空 v2；这一步是阶段四的主要回退成本。

**预估：**10–14 个工程工作日，包含兼容迁移、故障注入和 UI 接入。主要风险是模型质量、外部调用结果不明以及新旧运行语义共存；不以增加抽象层处理这些风险，而以受限操作、持久账本、租约校验和双版本退出门处理。
