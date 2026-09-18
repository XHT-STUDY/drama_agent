# DramaAgent 意图识别优化计划

日期：2026-09-18。状态：方向已确认，待分阶段实施。

本文基于当前仓库的 Planner、Turn/Action、Workflow、评测数据与 2026-09-07 的真实模型评测结果制定。目标不是增加更多平铺意图，而是把现有意图识别升级为可测量、可校验、可追踪的“交互分支 + 业务意图 + 目标 + 约束 + 执行范围”联合契约。

本计划只定义后续实施路径；编写本文时不修改生产代码、数据库、Prompt 或评测数据，不调用真实模型。

## 1. 决策摘要

### 1.1 结论

保留当前六类业务意图：

- `create_script`
- `explain`
- `evaluate`
- `revise_script`
- `revise_outline`
- `continue`

六类意图与当前真正存在的业务动作基本一致。现阶段不增加 `export`、`import`、`compare_versions`、`revise_story_bible` 等聊天意图，也不把“人物解释”“进度查询”“大纲总结”等只读表达拆成更多平铺类别。

本轮优化采用现有分层语义：

```text
用户表达
   │
   v
turn_type：clarification / answer / plan
   │
   v
intent：六类受限业务能力
   │
   v
target：对象类型 / 集数 / 活动上下文
   │
   v
constraints / batch_size：用户要求与执行范围
   │
   v
服务端重解析与校验
   │
   v
AgentActionPlan / WorkflowRun / 最终 Artifact
```

核心原则是：模型负责理解自然语言，服务端负责权限、目标、版本、执行范围和最终命令。

### 1.2 为什么不直接扩类

新增 intent 只有在同时满足以下条件时才允许进入设计：

1. 存在稳定、重复的用户需求，而不是少量措辞差异。
2. 无法自然映射到现有 intent + target + constraints。
3. 后端已经存在或确定要建设独立业务动作。
4. 需要独立的确认、权限、失败与回滚策略。
5. 能建立独立评测集和端到端成功标准。

在这些条件成立前，增加类别只会扩大分类边界、降低召回、增加 Prompt 与工作流维护成本。

### 1.3 最脆弱的前提

本计划假设聊天仍是封闭的创作控制面，不要求通过自然语言操作全部产品功能。如果产品方向改为“所有按钮能力都必须可对话调用”，应重新评审 intent 枚举，并分别为导入、导出、版本比较、Story Bible 修订建立权限和工作流契约；不得只在 Prompt 中增加几个名称。

## 2. 当前状态与已确认问题

### 2.1 当前实现

- `AgentPlannerOutput` 同时表达 `turn_type`、`intent`、`target`、`constraints`、澄清问题和回答。
- Planner 只能从服务端动态白名单选 intent，不能输出工具、API、SQL 或 Artifact ID。
- 确定性 preflight 在模型调用前处理集数越界、无上下文指代、多目标修改和部分冲突表达。
- 短语短路在 Planner 前处理“确认、继续、下一集、写五集、重试”等高频操作。
- 服务端根据 Planner selector 解析真实 Artifact/Run，生成固定的 `AgentActionPlan`。
- 写操作确认时重新校验来源 Artifact 的 ID、版本和 checksum。
- `explain` 不创建 Run；正文解释另行读取确切原文并验证引用。

### 2.2 当前评测资产

`backend/tests/evals/agent_commands.json` 当前有 65 条：

| 预期结果 | 数量 |
|---|---:|
| create_script | 15 |
| explain | 7 |
| evaluate | 8 |
| revise_script | 12 |
| revise_outline | 5 |
| clarification | 18 |
| continue | 0 |
| 合计 | 65 |

其中：

- 14 条为确定性 preflight 用例；
- 5 条带 active context；
- 16 条标注了目标集数；
- 真实模型结果文件仍是 Prompt v1.3、60 条样本；
- 当前生产 Prompt 已是 v1.4；
- 当前真实评测只统计 intent P/R/F1 和 clarification recall。

### 2.3 P0 问题

1. **真实评测 harness 与 v1.4 契约不一致。** 除 clarification 外，harness 默认要求 `turn_type=plan`；正确的 `answer/explain` 会被记为失败。
2. **目标集数没有进入指标。** 数据已经标注部分 `episode_number`，但 harness 没有比较；违反设计文档中“剧集目标识别准确率不低于 95%”的目标。
3. **`continue` 完全没有真实模型离线评测。** 较长的继续表达仍可能经过 Planner，不能只依赖短语短路测试。
4. **真实评测没有质量门槛。** 当前只限制调用/解析失败不能超过 20%，低 F1 不会阻断发布。
5. **报告与数据漂移。** 报告写 60 条，当前文件有 65 条；保存结果是 v1.3，当前 Prompt 是 v1.4。
6. **单集评估下游范围存在缺口。** Plan 和 Run config 保留 episode，但 Dispatcher 当前会把项目全部最新有效剧本写入评估状态，可能把单集请求扩大为全项目评估。

### 2.4 P1 问题

1. 明确文本中的集数虽然能由规则提取，但服务端没有强制与模型输出对齐。
2. 写操作依赖模型提取的 `constraints`，模型遗漏后下游无法恢复用户原始要求。
3. `explain` 混合项目状态、正文解释、设定读取和查看已有评估，内部读取策略边界不够清晰。
4. 冲突检测依赖有限正则，可能把“前慢后快”这类合理要求误判为冲突，也会漏掉隐式冲突。
5. Planner 同时负责路由、目标、约束和部分回答，导致 Prompt 与评测语义容易漂移。
6. “好、行、确认”等短路依赖最新 proposed Action/gated Run，缺少与最近对话语境的距离校验。
7. 当前数据以规范中文和同义模板为主，对口语、错别字、复合请求、多轮补充和状态变化覆盖不足。

## 3. 目标、非目标与成功标准

### 3.1 建设目标

1. 让离线评测准确反映当前 Planner 合同，而不是只测一个 intent 标签。
2. 对 `turn_type`、intent、target、集数、批次和最终执行范围分别给出指标。
3. 对明确文本目标实行服务端确定性优先，并检测模型与服务端的分歧。
4. 保留用户原始要求，避免结构化约束遗漏后永久丢失。
5. 让单集评估、修订、continue 等高风险路径具备端到端语义门禁。
6. 建立可重复的真实模型发版评测和线上样本回灌流程。

### 3.2 明确不建设

- 不增加新的产品 intent。
- 不开放模型自由工具调用或 function-calling 动作空间。
- 不引入新的 Agent 框架、向量数据库、外部评测平台或常驻服务。
- 不把真实付费模型调用放进每次 CI。
- 不用 LLM-as-a-judge 判断本可由字段精确比较的 intent、集数或批次。
- 不将聊天消息变成 Artifact 事实源。
- 不为提高离线分数硬编码具体评测句子。

### 3.3 发布指标

完成四阶段后，真实模型候选版本必须同时达到：

| 指标 | 门槛 |
|---|---:|
| 结构化输出可解析率 | ≥ 99% |
| turn_type accuracy | ≥ 95% |
| intent macro-F1 | ≥ 90% |
| 写操作 intent precision | ≥ 98% |
| 每个写操作 intent recall | ≥ 90% |
| clarification precision | ≥ 95% |
| clarification recall | ≥ 95% |
| 明确文本目标类型准确率 | ≥ 99% |
| 明确文本集数准确率 | ≥ 99% |
| active context 目标准确率 | ≥ 95% |
| continue precision | ≥ 98% |
| continue batch_size 准确率 | ≥ 95% |
| 确定性 preflight 零模型调用 | 100% |
| 写操作错误目标被服务端拦截 | 100% |
| 端到端执行范围契约 | 100% |

“写操作”指 `create_script`、`evaluate`、`revise_script`、`revise_outline`、`continue`。写操作 precision 高于 recall：宁可澄清，也不能自信执行错误动作或错误目标。

## 4. 数据与指标合同

### 4.1 Case 标注合同

每条命令评测样本至少包含：

- 稳定 case ID；
- 数据集版本和 split；
- 用户原文；
- 项目目标集数；
- 可用 intent 白名单；
- 可选 active context；
- 预期 turn_type；
- 可空 intent；
- 可空 target_type；
- 可空 episode_number；
- 可空 batch_size；
- 是否应由 preflight 确定性完成；
- 风险标签和覆盖标签；
- 一句人工标注意图说明。

不对自由文本 `answer` 做逐字比较。回答质量由解释引用、项目状态事实和消息契约分别验证。`constraints` 不使用另一个 LLM 自动打分；高风险样本标注必须保留的约束原子，由确定性规范化和人工复核共同判断。

### 4.2 指标定义

- **turn_type accuracy**：clarification、answer、plan 三分类准确率。
- **intent P/R/F1**：仅在标注意图的样本上计算，同时报告 micro 和 macro。
- **target accuracy**：分别统计 target_type、明确集数、上下文集数。
- **batch accuracy**：仅对 continue case 统计 `batch_size`，`null` 表示写完剩余全部。
- **clarification precision/recall**：既防漏澄清，也防过度澄清。
- **invalid rate**：模型调用失败、输出截断、Schema 失败和安全校验失败分别归因。
- **semantic execution accuracy**：从用户请求到 Run config、Workflow state、最终 Artifact 范围的一致率。
- **cost/latency**：记录每轮模型、Prompt 版本、调用次数、输入/输出 token、p50/p95 延迟和估算费用。

### 4.3 失败分类

真实评测结果必须将失败归入以下互斥主类：

1. provider/timeout/rate-limit；
2. output truncated；
3. Schema/安全校验失败；
4. turn_type 错误；
5. intent 错误；
6. target_type 错误；
7. episode_number 错误；
8. batch_size 错误；
9. 应澄清但执行；
10. 可直接执行但过度澄清；
11. 关键约束遗漏；
12. 下游执行范围漂移。

一条 case 可以有次级标签，但主失败类只能有一个，便于趋势对比。

## 5. 分阶段实施总览

| 阶段 | 交付 | 估算 | 是否独立可合并 |
|---|---|---:|---:|
| IR-1 修正评测尺子 | v1.4 兼容 harness、联合指标、门槛、报告一致性 | 2–3 人日 | 是 |
| IR-2 扩充代表性数据 | 360 条分层数据、120 条盲测、多轮与 continue 覆盖 | 4–6 人日 | 是 |
| IR-3 强化生产路由 | 服务端目标对齐、原始约束保真、解释分流、短路保护 | 5–8 人日 | 是 |
| IR-4 端到端与运行闭环 | 执行范围门禁、评估范围修复、指标与回灌流程 | 4–6 人日 | 是 |

串行估算约 15–23 人日，按熟悉仓库的一名后端为主、兼顾前端与测试的工程师计算，不包含真实模型供应商等待时间。计划涉及超过 8 个文件，跨领域 Schema、Skill、Application Service、Workflow、测试与文档，应按任务卡逐个合并，不做一次性大改。

## 6. IR-1：修正评测尺子

### 6.1 目标

让评测 harness 正确理解 Prompt v1.4 的 `clarification/answer/plan` 分支，并真正评估目标集数、target_type 和 continue，而不是只统计 intent 标签。

### 6.2 实施任务

#### IR-1.1 统一期望输出语义

- 更新 `agent_commands.json` 的 dataset version。
- 将项目状态问题标成 `answer`，正文解释标成 `answer + explain`。
- 只把会生成 Action 的请求标成 `plan`。
- 明确 `explain` 不需要 `AgentActionPlan`。
- 修正旧的 `plan/explain` case，避免继续训练错误契约。

#### IR-1.2 重写评分器

- 先比较 turn_type，再比较 intent。
- 仅在对应字段有标注时比较 target_type、episode_number、batch_size。
- 同时输出 micro/macro 指标、混淆矩阵和失败明细。
- 结果文件写入数据集版本、git commit、Prompt 版本、模型、provider、运行时间和重复次数。
- 禁止用保存的旧结果冒充当前 Prompt 结果。

#### IR-1.3 增加 continue 基础集

加入至少 20 条 continue case，覆盖：

- 大纲确认后开始写；
- 写一集、五集、全部；
- “下一集、下一批、把剩下的写完”；
- 白名单没有 continue 时必须澄清；
- 无 gated Run 时不得生成可确认的 continue Plan；
- 长复合表达不能被短语短路错误吞掉。

这些 case 在 IR-2 扩充时增长到40条。

#### IR-1.4 建立真实模型门槛

- harness 结束时根据第 3.3 节门槛生成通过/失败摘要。
- 开发期允许显式 `--report-only` 只产报告；发版模式必须执行门槛。
- 单 provider 发版至少跑一次全量；Prompt 或 planner model 变化必须重新跑。
- 真实 Key 只从现有 `.env`/Settings 注入，不写入报告和日志。

### 6.3 主要文件

- `backend/tests/evals/agent_commands.json`
- `backend/tests/evals/test_agent_command_eval.py`
- `backend/tests/evals/results/agent_commands_results.json`
- `backend/tests/unit/skills/test_agent_command_planner.py`
- `backend/tests/contract/test_agent_command_schemas.py`
- `docs/AGENT_EVAL_REPORT.md`
- `docs/AGENT_EVAL_METHOD.md`
- `docs/TEST_PLAN.md`

### 6.4 验收

- `answer/explain` 的正确输出被记为 TP，不再被当作 FN。
- 伪造“intent 正确但 episode 错误”的结果时，target 指标失败。
- 伪造“continue 正确但 batch_size 错误”的结果时，batch 指标失败。
- dataset、报告和结果文件的 case 数及版本一致。
- 默认 CI 不调用真实模型。
- 发版模式任一关键门槛不达标时进程返回非零状态。

### 6.5 验证命令

在 `backend` 目录运行：

```powershell
python -m pytest tests/evals/test_agent_command_eval.py tests/unit/skills/test_agent_command_planner.py tests/contract/test_agent_command_schemas.py -q
```

有付费 Key 且明确授权时运行真实评测：

```powershell
$env:EVAL_LLM_ENABLED='1'
python -m pytest tests/evals/test_agent_command_eval.py -m eval_real -q
```

### 6.6 回退

本阶段只改测试与文档，无生产数据迁移。若新评分器存在错误，可回退 harness，但保留已经修正的 v1.4 case 标注；不得恢复把正文解释标成 `plan` 的旧语义。

## 7. IR-2：扩充代表性评测数据

### 7.1 目标

把当前同义模板为主的65条集合扩展为360条，形成开发回归集与独立盲测集，使每个关键边界都有足够样本。

### 7.2 数据规模与分布

总计360条：

| 主标签 | 数量 |
|---|---:|
| create_script | 45 |
| explain | 50 |
| evaluate | 45 |
| revise_script | 55 |
| revise_outline | 45 |
| continue | 40 |
| clarification / out-of-scope | 80 |
| 合计 | 360 |

其中240条作为开发/固定回归集，120条作为盲测集。盲测集不进入 Prompt 示例，不根据单条失败反复调词；只在候选版本冻结后运行并产出总结果。

交叉覆盖最低数量：

- active context：60条；
- 文本明确目标与 active context 冲突：30条；
- 多轮澄清与补充：60条；
- 错别字、口语、省略和中英混合：50条；
- 多请求/复合请求：40条；
- 白名单随项目状态变化：40条；
- 明确集数目标：80条；
- 范围外请求和攻击性指令：40条。

这些是交叉标签，同一 case 可以计入多个覆盖维度。

### 7.3 数据建设纪律

- 先写分类边界矩阵，再补样本，不按“每类凑数量”生成重复句子。
- 每条 case 由一人标注、一人复核；争议 case 必须写清决策理由。
- Prompt 示例不得与盲测集逐字或近似重复。
- 保留困难样本，不因模型经常失败就修改为容易标签。
- 修改标签必须记录 dataset changelog。
- 不把真实用户隐私、完整剧本正文、密钥或本地路径放入数据集。

### 7.4 必须覆盖的边界对

- 解释第3集 / 评估第3集；
- 查看已有评估 / 重新执行评估；
- 先出大纲 / 修改已有大纲；
- 继续当前 Run / 新建一个创作；
- 改这里（有上下文）/ 改这里（无上下文）；
- 明确第3集但页面在第2集；
- 修改单集 / 同时修改多集；
- 项目状态查询 / 正文剧情解释；
- 确认现有计划 / “好的，但改成悬疑”；
- 合理的节奏变化 / 真正自相矛盾的约束；
- 集数越界 / 项目范围评估；
- 有 gated Run 的继续 / 无 gated Run 的继续。

### 7.5 主要文件

- `backend/tests/evals/agent_commands.json`：240条开发/回归集。
- `backend/tests/evals/agent_commands_holdout.json`：120条盲测集。
- `backend/tests/evals/test_agent_command_eval.py`
- `backend/tests/evals/README.md`：标注规范、split 纪律和 changelog。
- `docs/AGENT_EVAL_REPORT.md`

### 7.6 验收

- 两个数据文件合计恰好360条且 ID 唯一。
- 自动契约检查验证分布和交叉覆盖下限。
- 每个 case 的 expected 字段与 turn_type 语义合法。
- 盲测集不会被默认单元测试逐条暴露为 Prompt 示例。
- 当前候选模型连续运行三次，报告均值、最差值和每次失败明细。
- 不以三次结果的最好一次作为发布依据；门槛按最差一次判断。

### 7.7 回退

数据和报告可按文件回退，不影响生产。回退时不得删除已经暴露的失败类别；应将争议样本移入带原因的 quarantine 清单，而不是静默移除。

## 8. IR-3：强化生产路由

### 8.1 目标

将明确目标和执行安全从“模型大概率判断正确”提升为“服务端确定性优先，模型结果必须一致或被纠正/拒绝”。同时保留用户原始要求，降低约束遗漏风险。

### 8.2 目标解析与优先级

统一以下优先级，并由一个共享的纯函数合同实现：

```text
用户文本明确对象/集数
    > 合法 active context
    > 模型推断的 selector
    > 无法确定则 clarification
```

服务端在 Planner 前提取确定性候选，在 Planner 后执行一致性校验：

- 文本明确第3集、模型返回第2集：拒绝或规范化为第3集，并记录 disagreement。
- 文本明确大纲、模型返回 script：拒绝输出，不生成 Action。
- 只有“这里/当前稿”且 active context 合法：使用 active context。
- 文本与 active context 指向不同目标：文本优先。
- 多个写目标：保持一次只操作一个目标并澄清。
- 越界和不存在目标：不进入执行计划。

### 8.3 约束保真

- `AgentActionPlan` 对修订类命令保存原始 `user_request`，同时保留结构化 constraints。
- Run config 同时携带原文和结构化约束。
- 修订 Prompt 明确：原文是完整授权边界，结构化约束是索引；两者冲突时停止并报错，不自行扩权。
- Action Plan 卡片显示模型提取出的关键约束，用户确认前可发现遗漏。
- 服务端继续禁止模型输出 Artifact ID、Run ID 和工具名。

这项变更是 JSON Schema 的向后兼容增量，不需要数据库列迁移；历史计划缺少原文时沿用旧 constraints，并标记为 legacy plan。

### 8.4 explain 内部分流

不新增公开 intent，在服务端将只读请求区分为：

- 项目状态：使用确定性项目索引和摘要；
- 正文解释：读取确切剧本/场景并验证引用；
- Story Bible/大纲解释：读取对应 Artifact；
- 查看已有评估：读取现有 evaluation Artifact，不误触发新评估。

Planner 只决定 read/execute、intent 和 selector；具体答复由服务端选择的读取器生成。项目状态类回答如果继续使用模型，必须使用单独受约束的 Answer Composer，不复用可产生 Action 的 Planner 输出。

### 8.5 缩小正则职责

正则只负责高确定性事实：

- 集数提取；
- 明确对象词；
- 简短确认/继续短语；
- 明确越界和多目标。

“快与慢、保留与删除”等语义冲突不再只凭宽泛正则决定。只有确切互斥模板可以确定性澄清；其他情况交给结构化 Planner，并要求返回冲突说明。

### 8.6 短路保护

- 只有最近相关 Assistant 消息是 action plan 或 stage gate 时，“好/行/确认”才允许直接执行。
- 中间出现新的用户主题、目标变化或长复合表达时回落 Planner。
- 同时存在多个可能候选时不猜测，返回可读澄清。
- 记录 shortcut kind、目标类型、是否执行、是否回落和失败原因；禁止记录高基数文本。

### 8.7 主要文件

- `backend/app/domain/agent_planner.py`
- `backend/app/domain/agent_command.py`
- `backend/app/skills/agent_command_planner.py`
- `backend/app/skills/agent_shortcut.py`
- `backend/app/application/agent_context_service.py`
- `backend/app/application/agent_command_service.py`
- `backend/app/prompts/templates/agent_command_planner.md`
- `backend/app/prompts/manifest.yaml`
- `backend/app/application/workflow_dispatcher.py`
- `frontend/src/types/api.ts`
- `frontend/src/features/agent/ActionPlanCard.tsx`
- 对应 unit/contract/integration/frontend 测试。

### 8.8 验收

- 所有明确文本集数都由服务端解析并与 Planner 输出一致。
- 模型返回错误集数或目标类型时，不创建错误 Action。
- 用户原始修订请求可以从 Action Plan 追踪到 Run config 和修订工作流输入。
- 项目状态、正文解释、查看已有评估不再混用同一读取路径。
- “前慢后快”不被确定性正则误判为冲突。
- 单独“确认”只作用于最近相关计划；复合表达不短路。
- 所有 Prompt/Schema 变更有版本升级和 golden fixture。

### 8.9 回退

- 目标解析与一致性校验可按 feature commit 回退，无数据库迁移。
- 新增 JSON 字段保持旧代码可忽略；已经持久化的新计划不能被旧代码误读为额外执行权限。
- 如果 Answer Composer 不稳定，正文解释回退到“有限答复 + 原文入口”，不能恢复未读正文却生成剧情解释的行为。

## 9. IR-4：端到端语义门禁与运行闭环

### 9.1 目标

验证“听懂”最终真的变成正确的 Run、Workflow state 和 Artifact 范围，并通过低基数指标发现线上漂移。

### 9.2 修复单集评估范围

- `scope=episode` 时，Dispatcher 只把指定集最新有效剧本放入 `script_artifact_ids`。
- 指定集不存在时，在创建或确认阶段返回明确错误，不退化为全项目评估。
- `scope=project` 才允许收集全部最新有效剧本。
- 最终 evaluation Artifact 必须绑定正确 source script。

### 9.3 端到端语义测试矩阵

| 用户语义 | 必须断言 |
|---|---|
| 评估第3集 | Run config、Workflow state、最终报告都只有第3集 |
| 修改第3集，页面在第2集 | source ID 必须来自第3集 |
| 修改大纲 | source 必须是最新有效大纲，不得读取剧本 ID |
| 解释历史稿某场 | 无 Action/Run，引用固定历史版本和场次 |
| 继续写5集 | 恢复原 Run，batch_size=5，不新建创作 Run |
| 无 gated Run 时继续 | 不生成可确认 continue Action |
| 多目标修改 | clarification，无 Action、Run、Artifact |
| 错误模型 target | 服务端拦截，无错误写入 |
| Action 来源已更新 | confirm 返回 stale，不执行旧计划 |
| 约束包含三项 | 原文和三项要求都进入下游输入或被明确标记未提取 |

### 9.4 运行指标

复用现有 Prometheus 和结构化日志，增加低基数指标：

- `agent_planner_results_total{turn_type,intent}`
- `agent_planner_failures_total{reason}`
- `agent_target_disagreements_total{kind}`
- `agent_clarifications_total{source}`，source 为 preflight/model/server_reconcile
- `agent_shortcuts_total{kind,outcome}`
- `agent_actions_total{intent,status}`

禁止把用户文本、项目 ID、Artifact ID、conversation ID 放进 metric label。

### 9.5 线上样本回灌

- 每次发版前抽取失败、澄清和 target disagreement 样本。
- 样本进入评测集前必须脱敏和人工复核。
- 同一语义重复样本去重，不用流量比例淹没困难边界。
- 已用于 Prompt 调优的样本只能进入开发集，不能进入盲测集。
- 每次新增生产失败 case，同时补一条确定性、集成或真实模型评测，选择能最早阻断该失败的层级。

### 9.6 主要文件

- `backend/app/application/workflow_dispatcher.py`
- `backend/app/application/agent_command_service.py`
- `backend/app/core/metrics.py` 或当前指标注册模块
- `backend/tests/integration/api/test_agent_turns.py`
- `backend/tests/integration/api/test_agent_actions.py`
- `backend/tests/integration/workflow/test_conversational_revision.py`
- 独立评估 Workflow 的 integration 测试文件
- `e2e/agent-workspace.spec.ts`
- `docs/AGENT_EVAL_REPORT.md`
- `docs/OPERATIONS.md`
- `docs/TEST_PLAN.md`

### 9.7 验收

- 端到端语义矩阵全部通过。
- 单集评估不会产生其他集的 evaluation Artifact。
- 所有新增指标无高基数 label。
- 一次真实模型发版评测同时保存开发集与盲测集结果。
- 报告包含旧版本对比、失败分类、成本和延迟。
- E2E 使用 FakeLLM，不需要真实 Key；真实模型只在显式授权的离线评测中使用。

### 9.8 回退

- 指标和测试可独立回退，不影响数据。
- 单集评估范围修复不能回退为静默全项目评估；若出现兼容问题，应暂时拒绝单集入口并给出明确错误。
- 回灌流程只新增脱敏测试资产，不修改历史用户消息。

## 10. 测试分层

### 10.1 Unit

- 中文/阿拉伯集数提取；
- 显式目标与 active context 优先级；
- 多目标和越界；
- Planner 输出一致性校验；
- shortcut 上下文保护；
- 指标计算和混淆矩阵；
- constraints 原文保真。

### 10.2 Contract

- `AgentPlannerInput/Output` 禁止额外字段；
- ActionPlan intent 与 command intent 一致；
- 新增原始请求字段向后兼容；
- Prompt manifest、版本与 golden fixture 一致；
- eval dataset 分布、ID、split 和 expected 字段合法。

### 10.3 Integration

- Turn 的 clarification/answer/plan/failed 四分支；
- 错误目标不创建 Action；
- Action confirm 目标快照过期；
- 单集评估范围；
- revision 原始请求进入 Run config；
- continue 白名单和 gated Run；
- 短路只确认最近相关计划；
- 幂等重放不重复调用 Planner 或创建 Run。

### 10.4 E2E

- 创作计划 → 确认 → 大纲门 → continue；
- 无上下文修改 → 澄清；
- 明确第3集覆盖第2集页面上下文；
- 单集修订 → 新版本和 Diff；
- 单集评估 → 只有该集结果；
- 正文解释 → 原文引用且无 Run；
- stale plan → 明确提示重新规划。

### 10.5 Real eval

- 开发集与盲测集分开报告；
- 同一候选配置运行三次；
- 报告均值和最差值；
- 以最差值执行发布门槛；
- 保存模型、Prompt、dataset、commit、token、延迟与费用；
- 不把真实模型调用放进常规 CI。

## 11. 文档、发布与执行纪律

每个任务卡完成后：

1. 更新 `docs/DEV_PLAN.md` 的对应任务状态和验收证据。
2. 在 `docs/DEV_LOG.md` 记录做了什么、为什么、验证结果和经验。
3. Bug 修复同步 `docs/TROUBLESHOOTING.md`。
4. Prompt 变更升级版本并更新 manifest/golden fixture。
5. API或领域字段变化同步前端类型和 `docs/API_CONTRACT.md`。
6. 真实评测只在用户明确批准费用和已有可用 Key 时执行。
7. 不提交 `.env`、真实 Key、原始用户隐私或未脱敏日志。

阶段收口运行：

```powershell
make lint
make typecheck
make test
make e2e REPEAT=5
```

Windows 环境若 `make` 不可用，按 Makefile 中的等价后端、前端和 Playwright 命令执行，并在验收记录中写明替代命令。

## 12. 依赖、公共表面与迁移

### 12.1 外部依赖

- 新增第三方服务：0。
- 新增运行时依赖：0。
- 新增数据库迁移：0。
- 新增 MCP/插件：0。
- 自动化 CI 所需付费账户：0。
- 真实模型发版评测：沿用现有 `LLM_API_BASE`、`LLM_API_KEY`、`LLM_PLANNER_MODEL`。

### 12.2 公共表面

- intent 枚举：不变。
- Turn/Action API 路由：不变。
- 修订命令/计划 JSON：增加可选原始请求字段，向后兼容。
- 测试工具：增加 report-only/发版门槛运行模式。
- 数据资产：增加 holdout 文件和 eval 标注规范。
- Prometheus：增加低基数 Planner/target/shortcut 指标。

### 12.3 回滚成本

四阶段均不需要数据库迁移，可按阶段代码回滚。已经持久化的新 JSON 字段必须保持旧读取兼容；Artifact、Message、Action 和 Run 不删除、不重写。任何回滚都不能恢复“错误单集请求静默扩大为全项目执行”或“没有读原文却解释剧情”的已知不诚实行为。

## 13. 拒绝的方案

### 13.1 扩成十几到二十个平铺 intent

拒绝原因：多数只读表达最终共享同一读取路径，类别边界模糊；每个新类别都会增加 Schema、Prompt、计划、确认和测试成本，却不能解决目标或执行范围错误。

### 13.2 完全规则化路由

拒绝原因：规则适合集数、对象、越界和短句确认，不适合复杂中文创作诉求、隐含目标和长约束。完全规则化会演变成难维护的关键词系统。

### 13.3 让模型直接 function-call 任意工具

拒绝原因：当前动作空间封闭、写操作代价高且已有确定性工作流。开放工具选择会削弱服务端白名单、确认门和来源快照保护。

### 13.4 用另一个 LLM 判断路由是否正确

拒绝原因：turn_type、intent、集数和 batch_size 都可由人工标签和精确字段比较；引入 LLM judge 会增加偏差和费用，且使发布门槛不可重复。

## 14. 实施顺序与完成定义

固定顺序：

```text
IR-1 修正评测尺子
  → IR-2 扩充数据
  → IR-3 强化生产路由
  → IR-4 端到端与运行闭环
```

各阶段独立可合并：

- IR-1 完成后，即使后续不做，评测也不再错误计算 v1.4。
- IR-2 完成后，即使生产路由不改，模型选择和 Prompt 回归已有更可信依据。
- IR-3 完成后，即使监控尚未补齐，错误目标也会在执行前被服务端拦截。
- IR-4 完成后，识别结果到最终 Artifact 的语义闭环形成发布门禁。

全部完成的定义：

1. 360条数据和 split 合同稳定；
2. 当前 Prompt/模型真实评测达到第3.3节全部门槛；
3. 明确目标冲突不会创建错误 Action；
4. 单集评估、修订和 continue 的执行范围与用户表达一致；
5. 原始用户要求可追踪到 Plan、Run config 和工作流输入；
6. 常规 CI、集成测试和 E2E 全绿；
7. 文档、Prompt 版本、评测报告和代码一致；
8. 上线指标能区分 preflight/model/reconcile/shortcut 的失败来源。

完成上述条件前，不以“总体准确率看起来较高”宣称意图识别已经生产可靠。
