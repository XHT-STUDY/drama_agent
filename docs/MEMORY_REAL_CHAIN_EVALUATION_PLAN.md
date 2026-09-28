# Memory 真实链路效果评估方案

> 状态：设计完成，待批准实施  
> 日期：2026-09-28  
> 适用范围：对话累计摘要、最近消息、结构化剧情状态、上下文组装、Planner、Writer、Reviser 与连续性检查  
> 关联文档：[MEMORY_DESIGN.md](MEMORY_DESIGN.md)、[TEST_PLAN.md](TEST_PLAN.md)、[MEMORY_EVAL_REPORT.md](MEMORY_EVAL_REPORT.md)、[ADR-0001](adr/0001-memory-evaluation-replays-production-chain.md)

## 1. 结论

现有 M-01 评测应保留为快速、确定性的契约层，但不能继续作为 Memory 最终效果的唯一证据。新增评测采用“两条互补链路”：

1. **生产链路评测**：从真实 API 写入消息开始，经过 PostgreSQL、Redis、后台累计摘要、Agent Planner、创作 Run、StoryStateService、ContextBuilder 和真实模型，最后对实际 Artifact 评分。
2. **冻结输入消融**：从生产链路取得同一份原始消息、作品工作集和上下文分段，仅替换 Memory 视图，比较 `none`、`recent_only`、`production`、`full_history` 和 `oracle`，隔离 Memory 本身的贡献。

主结论只允许来自 `production` 真实链路和冻结输入消融。参考 harness、FakeLLM 和 LLM Judge 都不能单独证明真实效果。

## 2. 当前基线与缺口

### 2.1 已有能力

- 32 组对话 Golden Case：8 个题材 × 24/48/96/192 条消息。
- 10 组剧情 Golden Case：每组 10 集，带 typed delta 真值。
- `none`、`recent_only`、历史 `current`、`structured` 四组确定性消融。
- 写入、召回、使用、成本四层评分。
- 生产 reducer 消费剧情 Golden Case 的契约测试。
- 真实摘要模型入口 `scripts/evaluate_memory.py --provider real`。

### 2.2 不能由现有报告推出的结论

- `structured` 组主要是参考 harness，不是完整生产 API 重放。
- “使用层到达率”证明标记进入 ContextBuilder 输出，不证明 Writer 最终遵守。
- 剧情指标证明正确 delta 能被 reducer 正确处理，不证明真实模型能从剧本准确抽取 delta。
- 当前真实模式只替换摘要函数，没有运行 Redis、后台任务、Artifact 写入、Planner、Writer 和恢复链路。
- 真实模型固定集尚未执行，现有 93.8%/100% 等结果不能表述为线上模型效果。

## 3. 评估目标

评测必须分别回答以下问题，禁止用一个总分掩盖失败位置：

1. **写入正确性**：原始消息、摘要和剧情状态是否完整、幂等、可恢复？
2. **抽取正确性**：真实摘要器和 Summarizer 是否提取了正确的信息，是否虚构？
3. **召回正确性**：当前任务是否选中了正确项目、会话、版本和前态？
4. **上下文到达**：召回内容是否经过预算分配后实际进入了模型请求？
5. **记忆遵守**：Planner、Writer 和 Reviser 是否在输出中遵守已到达的 Memory？
6. **最终收益**：相对无 Memory 和最近窗口，连续性错误是否显著下降？
7. **成本收益**：相对完整历史，token、延迟和费用是否可接受？
8. **失败恢复**：Redis 清空、进程重启、摘要失败和稿件修订后是否恢复到正确状态？

## 4. 非目标

- 不用本方案评价通用创作审美或商业价值。
- 不把 RAG 质量混入 Memory 因果结论；Memory 专项消融固定或关闭 RAG。
- 不用 LLM Judge 替代确定性事实校验。
- 不在生产数据库、生产 Redis 或真实用户项目上运行。
- 不增加用户可见 API，不改变正常运行时的 Memory 语义。
- 不在本阶段引入 Mem0、图数据库或新的常驻评测服务。

## 5. 关键术语与判定边界

术语以根目录 [CONTEXT.md](../CONTEXT.md) 为准。评测中尤其要区分：

```text
写进数据库 ≠ 写进派生记忆
写进派生记忆 ≠ 当前任务召回
当前任务召回 ≠ 进入模型上下文
进入模型上下文 ≠ 模型最终遵守
```

每一层必须保存独立证据，才能定位失败发生在哪一段。

## 6. 总体架构

```text
Golden Case
    │
    ├── 自然语言消息 + Memory Atom 真值
    ├── 固定 StoryBible / 大纲 / 剧本版本
    └── 输出行为断言
    │
    ▼
真实 FastAPI 路由
    │
    ├── POST /projects/{id}/agent/turns
    ├── POST /conversations/{id}/messages
    ├── POST /agent/actions/{id}/confirm
    ├── POST /runs/{id}/continue
    └── GET  /runs/{id} / story-state / artifacts
    │
    ▼
PostgreSQL + Redis + 后台摘要 + Workflow
    │
    ├── ConversationSummary Artifact
    ├── EpisodeSummary Artifact
    ├── ContinuityState Artifact
    ├── ContextManifest / 实际模型请求
    └── Script / Evaluation / ContinuityCheck Artifact
    │
    ├──────────────► 生产链路评分
    │
    └── 冻结输入 ──► Memory 变体重放 ──► 因果消融评分
                                  │
                                  ▼
                        Evaluation Evidence Bundle
```

## 7. 什么叫“真实链路”

一次生产链路样本必须满足以下条件：

- 消息经 FastAPI 路由写入，不能直接构造摘要对象。
- 使用独立 PostgreSQL 和 Redis，运行当前 Alembic 迁移。
- 使用 `MessageService` 的实际 Redis 写入和后台摘要调度。
- 摘要由当前 `conversation_summary` Prompt 和真实 summarizer 模型生成。
- Planner 经 `/agent/turns` 的三段式事务执行。
- 创作经 Action confirm 或 `/projects/{id}/runs` 创建实际 WorkflowRun。
- StoryState 由 `StoryStateService.ensure_state_through` 调用真实 Summarizer 生成 typed delta。
- Writer 使用生产 `ContextBuilder` 和生产 Prompt。
- 结果从数据库 Artifact、Run diagnostics 和实际 LLM 调用追踪读取。
- 不允许在 runner 中复制 `catch_up`、状态 reducer 或项目级摘要合并逻辑。

固定剧本来源场景允许通过生产 `ArtifactService` 创建已校验 Artifact，以隔离 Memory 抽取质量；最终端到端场景仍必须至少包含一次完整创作 Run。

## 8. 两类实验为什么必须分开

### 8.1 生产链路资格测试

目的：证明系统真的把 Memory 写入、召回、注入并用于生成。

它只运行当前 `production` 策略，不修改 Memory。失败意味着生产链路有问题，不能进入消融阶段。

### 8.2 冻结输入因果消融

目的：回答“输出改善是否由 Memory 引起”。

所有变体必须共享：

- 相同原始消息；
- 相同 StoryBible、大纲和前集剧本 Artifact ID；
- 相同目标任务；
- 相同 Prompt 版本；
- 相同模型版本与生产温度；
- 相同 RAG 片段；
- 相同输出 Schema 和预算。

只允许替换 Memory 视图。不得为每个变体重新生成 StoryBible 或前集剧本，否则上游随机性会污染结论。

## 9. 消融变体

| 变体 | 对话 Memory | 剧情 Memory | 用途 |
|---|---|---|---|
| `none` | 不提供历史 | 不提供前态 | 最低基线 |
| `recent_only` | 最近 12 条原始消息 | 仅上一集原文事件 | 简单窗口基线 |
| `production` | 当前累计摘要 + 最近窗口/当前请求 | 当前 ContinuityState | 被测方案 |
| `full_history` | 全部原始消息 | 全部前集剧本 | 质量上界与成本下界对照 |
| `oracle` | Golden Memory Atom | Golden Story State | 诊断模型能力上界，不参与发布结论 |

`full_history` 的标准数据保证输入不超过被测模型上下文。超出时样本标记为 `not_comparable`，不得把截断后的结果写成完整历史表现。

## 10. 数据集设计

### 10.1 新增自然语言 E2E 数据集

新增 `backend/tests/golden/memory/memory_e2e_cases_v1.json`。不直接把当前 `【设定】`、`【否决】` 标签暴露给模型，避免模型依赖测试标记。每个 Case 包含：

| 字段 | 含义 |
|---|---|
| `case_id` | 稳定唯一 ID |
| `track` | `dialogue` / `story_state` / `generation` / `recovery` |
| `project_seed` | 项目标题、目标集数和隔离标记 |
| `conversations` | 按顺序重放的自然语言消息 |
| `source_artifacts` | 固定 StoryBible、大纲和前集剧本 |
| `target_task` | 最终 Planner/Writer/Reviser 任务 |
| `memory_atoms` | 不暴露给模型的预期记忆原子 |
| `story_assertions` | 事实、知识、伏笔、道具、时间线真值 |
| `output_assertions` | 最终输出必须满足/不得出现的行为断言 |
| `confounders` | 固定 RAG、近似跨项目内容、无关闲聊 |

### 10.2 Memory Atom 类型

- `confirmed_fact`
- `latest_fact`
- `superseded_fact`
- `veto`
- `long_term_constraint`
- `open_question`
- `stable_preference`
- `ephemeral_chatter`

每个 Atom 必须记录来源消息 sequence、作用域、是否应进入摘要、适用任务和被哪条 Atom 取代。

### 10.3 剧情断言类型

- `fact_present`
- `fact_source`
- `knowledge_allowed`
- `knowledge_forbidden`
- `loop_open`
- `loop_resolved`
- `prop_holder`
- `relationship_state`
- `timeline_before`
- `locked_fact`
- `future_plan_not_occurred`

### 10.4 三档套件

| 套件 | 固定样本 | 重复次数 | 用途 |
|---|---|---:|---|
| `smoke` | `dlg_football_24/48/96/192`；`story_contract` 固定第 1～2 集、生成第 3 集 | 1 | 配置与真实链路首次验证 |
| `release` | football、suspense 四个长度档共 8 组；contract、pawnshop、fakedeath、threekeys 固定第 1～5 集、生成第 6 集 | 3 | 发版前质量与方差门禁 |
| `audit` | 现有 32 组对话；10 个剧情 Case 固定第 1～9 集、生成第 10 集，并对第 1～9 集逐集评分 StoryState | 3 | 完整人工触发审计 |

样本选择按固定 Case ID，不得随机抽取后只汇报最好结果。

## 11. 生产链路执行步骤

### 11.1 环境预检

评测必须使用：

- 数据库名 `drama_memory_eval`；
- Redis DB `15`；
- 当前仓库 Alembic head；
- `EVAL_LLM_ENABLED=1`；
- `MEMORY_EVAL_ALLOW_DESTRUCTIVE=1`；
- 现有 `LLM_API_BASE`、`LLM_API_KEY` 和角色模型配置。

Runner 在清理数据前必须拒绝以下情况：数据库名不是 `drama_memory_eval`、Redis DB 不是 `15`、API Base 指向已知生产域名、`MEMORY_EVAL_ALLOW_DESTRUCTIVE` 未显式开启。

### 11.2 对话写入链

1. 通过项目 API 创建独立项目。
2. 通过会话 API 创建会话。
3. 按原始顺序调用 `POST /conversations/{id}/messages` 或 `/agent/turns`。
4. 每条消息确认数据库 sequence 连续且内容 checksum 匹配。
5. 在阈值点轮询 `conversation_summary` Artifact，不直接等待进程内 Task 对象。
6. 检查摘要覆盖区间、previous ID、digest、Prompt 版本和 input hash。
7. 查询 Redis 最近 12 条，并与数据库最后 12 条逐项比对。

### 11.3 Planner 召回链

1. 发送包含“刚才那个”“继续之前的主线”等指代请求。
2. 通过 EvalTraceLLM 捕获 Planner 实际请求。
3. 检查当前用户请求、最近消息、累计摘要、项目索引和活动 Artifact 是否到达。
4. 检查 Planner 的 intent、target type、episode、澄清与否是否符合真值。
5. 对跨项目近似设定检查零泄漏。

### 11.4 StoryState 抽取链

1. 用生产 `ArtifactService` 创建固定 StoryBible、大纲和剧本 Artifact，保留真实来源链接。
2. 构造确切 StoryWorkset。
3. 调用生产 `StoryStateService.ensure_state_through`，真实 Summarizer 从剧本抽取 typed delta。
4. 读取落库的 EpisodeSummary 和 ContinuityState，而不是读取模型原始回答。
5. 对照 Golden 事实、知识边界、伏笔、道具、关系、时间线和来源评分。
6. 同一 Workset 重放一次，要求零额外模型调用且 Artifact 不重复。

### 11.5 Writer 使用链

1. 写第 N 集前，通过实际 WorkflowRun 触发 `catch_up_project_summaries`。
2. 加载 `through=N-1` 的 StoryState。
3. 捕获 ContextBuilder 的 Manifest 和发给 Writer 的最终请求。
4. 检查摘要和连续性状态是否真正到达。
5. 保存实际 Script Artifact。
6. 对输出行为断言评分：锁定事实、角色知识、伏笔、道具、时间线和否决项。
7. 生成后检查 EpisodeSummary/ContinuityState 是否推进到 N。

### 11.6 修订与采用链

1. 修订第 N 集并生成候选稿。
2. 验证 Reviser 使用 `through=N-1` 前态。
3. 验证连续性检查再次独立加载同一前态。
4. 候选稿违反锁定事实时不得成为 valid。
5. 合法修订成为 valid 后，第 N 集及后缀状态必须显示 stale/pending 并在续写时重建。

## 12. 真实请求追踪

新增仅评测模式启用的 `EvalTraceLLM` 装饰器，转发真实请求并记录：

- agent/skill 名称；
- model/provider；
- Prompt 名称和版本；
- 完整 messages 的本地评测副本；
- 请求与响应 SHA-256；
- token usage；
- latency；
- provider request ID；
- retry 次数与错误码；
-关联 run、turn、project、conversation 和 Artifact ID。

默认生产配置下该装饰器不加载。报告只保存脱敏摘要和 hash；完整 Prompt 只进入本地 Evidence Bundle，禁止提交 Git。

ContextBuilder 额外保存：

- Manifest；
-各 section 的 hash、字符数、token 估算；
- section 是否完整、截断或移除；
-最终 assembled context hash；
- RAG chunk ID。

## 13. 指标与计算

### 13.1 写入层

| 指标 | 计算 | 门槛 |
|---|---|---:|
| 原始消息持久化率 | DB 正确消息数 / 输入消息数 | 100% |
| sequence 连续率 | 无缺口会话数 / 会话数 | 100% |
| Redis 窗口一致率 | 与 DB 最近 12 条完全一致 | 100% |
| 摘要覆盖完整率 | 覆盖到 `message_count-window` | 100% |
| 摘要链连续率 | previous/covered 区间无断裂 | 100% |
| 状态来源正确率 | basis/source 全部匹配 Workset | 100% |
| 幂等重放率 | 同输入无重复 Artifact/模型调用 | 100% |

### 13.2 抽取与召回层

| 指标 | 门槛 |
|---|---:|
| 最新事实准确率 | ≥95% |
| 否决项召回率 | ≥95% |
| 96/192 条后的关键约束召回率 | ≥90% |
| 被替代事实泄漏率 | ≤1% |
| 虚构 Memory Atom 率 | ≤1% |
| 未决问题召回率 | ≥90% |
| 跨项目泄漏 | 0 |
| 剧情事实 precision / recall | ≥95% / ≥90% |
| 角色知识越界 | 0 |
| 伏笔状态 F1 | ≥90% |
| 道具归属准确率 | ≥95% |
| 时间线顺序准确率 | ≥95% |

### 13.3 上下文到达层

| 指标 | 门槛 |
|---|---:|
| 关键 Memory 到达率 | 100% |
| 受保护段静默截断 | 0 |
| Manifest 与实际请求一致率 | 100% |
| 错误版本/错误项目内容到达 | 0 |
| 截断可解释率 | 100% 有原因和标记 |

到达率的分母只包括本任务适用的 Atom，不能用所有存储内容抬高或压低结果。

### 13.4 使用与最终输出层

| 指标 | 门槛 |
|---|---:|
| Planner 指代解析正确率 | ≥95% |
| Planner intent/target 正确率 | ≥95% |
| Writer 关键约束遵守率 | ≥90% |
| 锁定事实违反率 | 0 |
| 角色未学先知率 | 0 |
| 已回收伏笔重复回收率 | 0 |
| 相对 `recent_only` 连续性违规下降 | ≥50% |
| 相对 `full_history` 盲评质量下降 | ≤2 个百分点 |

硬事实由代码判定。开放性质量使用盲化 A/B：隐藏变体名、随机顺序，由独立 Judge 输出结构化维度分；release 套件至少人工复核 20% 的 Judge 分歧样本。LLM Judge 分数不单独作为发布门禁。

### 13.5 成本与延迟层

| 指标 | 门槛 |
|---|---:|
| 96/192 条相对完整历史 token 节省 | ≥60% |
| 普通消息同步 Memory 开销 p95 | ≤50ms（同机、排除 LLM） |
| 摘要失败导致消息失败 | 0 |
| 每有效 Memory Atom 的增量成本 | 记录趋势，不设首版硬门槛 |
| 每集 StoryState 派生模型调用 | 首次 1，幂等重放 0 |

## 14. 故障与攻击场景

| 场景 | 操作 | 必须结果 |
|---|---|---|
| Redis 清空 | 写满窗口后清空 eval Redis DB | 最近消息从 PostgreSQL 恢复，内容一致 |
| 阈值后进程重启 | 第 24 条提交后、摘要完成前重建应用 | 创作 Run catch-up 补齐摘要 |
| 摘要首次超时 | EvalTraceLLM 注入一次可恢复超时 | 消息成功；下次触发/Run 补齐；无重复摘要 |
| 并发阈值消息 | 两请求争夺相邻 sequence | sequence 唯一，摘要 input hash 去重 |
| 跨项目近似设定 | 两项目使用同角色名但冲突年龄 | 召回和 Prompt 零泄漏 |
| 用户改口 | 旧事实后出现明确替代 | 只保留最新事实，旧值不得到达 Writer |
| 剧本版本替换 | 第 N 集产生新版 valid | 后缀状态 stale，前缀可复用 |
| typed delta 非法引用 | 模型引用未知角色/伏笔 | fail closed，不落非法状态 |
| 受保护内容超预算 | 当前目标超过预算 | 明确失败，不静默截断 |
| 可选 RAG 不可用 | 检索返回错误 | Memory 评测继续，Manifest 记录降级 |

## 15. 方差、漂移与公平性

- release/audit 每个真实模型样本运行 3 次，同时报告均值、最差一次和标准差。
- 发布门槛按最差一次判定，避免只汇报平均值掩盖不稳定。
- 固定数据集版本、Prompt 版本、模型名、provider、迁移 revision 和 Git commit。
- 变体运行顺序使用由 `case_id` 派生的固定洗牌，避免时间顺序偏差。
- RAG 在 Memory 因果套件中关闭；系统套件中使用同一批冻结 chunk。
- 生成模型与 Judge 结果分账；Judge 不读取变体名称和 Memory 原文。
- 模型供应商升级后必须新建报告，不覆盖旧报告。

## 16. 结果与证据包

每次运行写入：

```text
backend/tests/evals/results/memory_e2e/<evaluation_run_id>/
├── run_manifest.json
├── case_results.jsonl
├── metric_summary.json
├── llm_calls.jsonl
├── context_manifests.jsonl
├── artifact_index.jsonl
├── failures.jsonl
└── report.md
```

`run_manifest.json` 必须包含：

- evaluation run ID 和时间；
- suite、variant、repetition；
- dataset 文件 hash；
- Git commit 和 dirty 状态；
- Alembic revision；
- provider 和所有角色模型；
- Prompt 版本；
- Memory 关键配置；
-数据库/Redis 脱敏标识；
-总调用、token、费用和耗时。

Git 只提交汇总报告 `docs/MEMORY_E2E_EVAL_REPORT.md`。包含真实对话或完整 Prompt 的 Evidence Bundle 保持 gitignored。

## 17. 实施拆分

本方案预计改动超过 8 个文件，涉及脚本、评测 runner、Golden 数据、LLM 装饰器、评分器和文档；预计 8～12 人日。各阶段可独立合并。

### ME-01：数据契约与确定性评分器（2 人日）

新增：

- `backend/tests/golden/memory/memory_e2e_cases_v1.json`
- `backend/tests/evals/memory_e2e/schema.py`
- `backend/tests/evals/memory_e2e/scorers.py`
- `backend/tests/evals/test_memory_e2e_dataset.py`

验收：自然语言消息不暴露测试标签；Atom 来源、取代关系和输出断言完整；评分器使用伪造好/坏结果验证每个指标方向。

### ME-02：真实链路 runner 与安全预检（2 人日）

新增：

- `backend/scripts/evaluate_memory_e2e.py`
- `backend/tests/evals/memory_e2e/runner.py`
- `backend/tests/evals/memory_e2e/api_driver.py`

验收：通过 ASGI 路由执行消息、Turn、Action、Run 和查询；错误数据库/Redis/缺少显式开关时拒绝清理和运行；smoke 使用 FakeLLM 可重复。

### ME-03：真实 LLM 与上下文追踪（1.5 人日）

新增：

- `backend/tests/evals/memory_e2e/trace_llm.py`
- `backend/tests/evals/memory_e2e/evidence.py`

修改 LLM 工厂，使显式评测模式可以包装真实客户端；默认路径完全不变。

验收：真实请求、usage、延迟、Prompt 版本、ContextManifest 和 Artifact ID 可关联；密钥和 Authorization 不落盘。

### ME-04：StoryState 真实抽取与恢复场景（2 人日）

新增：

- `backend/tests/evals/memory_e2e/story_fixture_loader.py`
- `backend/tests/evals/memory_e2e/faults.py`

两者复用生产 ArtifactService、StoryStateService 和 reducer。

验收：固定剧本经真实 Summarizer 生成 delta；幂等、跨项目、stale、Redis 恢复、摘要失败补做全部有证据。

### ME-05：冻结输入消融与最终输出评分（2 人日）

新增：

- `backend/tests/evals/memory_e2e/variants.py`
- `backend/tests/evals/memory_e2e/generation_scorer.py`

在 runner 内实现五个 Memory 视图；共享上游 Artifact 和固定 RAG；调用同一生产 Skill、Prompt、Schema 和模型。

验收：只有 Memory section hash 在变体间不同；最终输出硬断言和盲评结果可对照；oracle 只用于诊断。

### ME-06：报告、门禁与文档（1～2.5 人日）

新增：

- `docs/MEMORY_E2E_EVAL_REPORT.md`
- `backend/tests/evals/test_memory_e2e_report_contract.py`

更新 `TEST_PLAN.md`、`KNOWN_LIMITATIONS.md` 和 `DEV_PLAN.md`。报告必须标注真实/模拟、模型、样本量、失败样本和置信范围；任一硬门槛失败时命令非零退出。

## 18. 目标命令接口

实施后的固定接口：

```powershell
cd backend

# 不访问真实模型：全量契约和 runner 接线
uv run pytest tests/evals/test_memory_e2e_dataset.py tests/evals/test_memory_e2e_report_contract.py
uv run python scripts/evaluate_memory_e2e.py --suite smoke --provider fake

# 真实模型 smoke
$env:EVAL_LLM_ENABLED = "1"
$env:MEMORY_EVAL_ALLOW_DESTRUCTIVE = "1"
uv run python scripts/evaluate_memory_e2e.py --suite smoke --provider real

# 发版门禁
uv run python scripts/evaluate_memory_e2e.py --suite release --provider real --repeats 3 --fail-on-gate

# 完整审计
uv run python scripts/evaluate_memory_e2e.py --suite audit --provider real --repeats 3 --fail-on-gate
```

Runner 不从命令行接收 API Key；只读取现有 `LLM_API_KEY`。新增可选 `MEMORY_EVAL_JUDGE_MODEL` 只用于盲评，不影响硬事实评分。

## 19. 依赖与凭据

| 依赖 | 用途 | 缺失行为 |
|---|---|---|
| PostgreSQL/pgvector | 真实 Message、Artifact、Run、Event | 拒绝运行 |
| Redis DB 15 | 最近窗口与故障恢复 | 拒绝运行；故障用例显式控制 |
| `LLM_API_BASE` | 真实兼容接口 | real 模式拒绝运行 |
| `LLM_API_KEY` | 真实模型鉴权 | real 模式拒绝运行，永不落报告 |
| 角色模型配置 | Planner/Writer/Summarizer/Reviser | 记录实际回退后的模型名 |

不新增常驻服务、外部数据库或第三方评测平台。

## 20. 风险与防御

### 20.1 最大风险：评测测到的是模型差异，不是 Memory 差异

防御：冻结所有上游输入，只替换 Memory 视图；三次重复；报告最差值和方差。

### 20.2 数据集标签泄漏

防御：E2E 消息自然化，真值存独立字段；模型请求不得包含 Atom 类型和期望标签。

### 20.3 评测代码再次复制生产逻辑

防御：ADR-0001；runner 只调用公开路由和生产应用服务，reference 代码只能评分。

### 20.4 误清理开发或生产数据

防御：数据库名、Redis DB、域名和显式 destructive 开关四重校验；失败即停止。

### 20.5 真实模型成本失控

防御：三档套件、调用上限、token 上限、预计费用预检；超过预算在调用前停止，不产出“部分通过”结论。

### 20.6 Prompt 和模型漂移

防御：Evidence Bundle 固化版本；新旧结果并列，不覆盖历史；不同模型版本不直接合并统计。

## 21. 回滚

评测能力不修改公开 API 和生产 Schema。回滚时删除 runner、数据集、评分器和 eval-only LLM 包装即可；生产 Memory 行为不受影响。评测数据库和 Redis 只包含隔离测试数据，可在安全预检通过后清理。

## 22. 完成标准

只有同时满足以下条件，才能说“Memory 效果已经通过真实链路评估”：

1. release 套件用真实模型完成 3 次重复；
2. 所有写入、隔离、来源、幂等和受保护上下文硬门槛通过；
3. `production` 相比 `recent_only` 连续性违规至少下降 50%；
4. 96/192 条对话相比 `full_history` 节省至少 60% token；
5. 关键约束遵守率至少 90%，锁定事实和知识边界零违规；
6. Manifest 与实际模型请求 100% 一致；
7. Redis 清空、进程重启、摘要失败和版本替换场景全部恢复；
8. 报告公开失败样本、模型/Prompt 版本、费用、方差和未验证项；
9. 人工复核 release 套件中至少 20% 的 Judge 分歧样本；
10. 不再使用 M-01 FakeLLM 数字代表真实模型效果。

## 23. 最脆弱假设

本方案假设固定 Golden 剧本足以代表真实短剧创作中的连续性难点。如果真实用户剧本的叙事复杂度、隐含信息或修辞表达明显更高，结构化抽取分数会被高估。为降低这一风险，首次 release 评测完成后必须把真实人工创作但已脱敏的失败样本按同一 Schema 回灌；在没有这批样本之前，报告只能声称“固定代表集通过”，不能声称覆盖全部真实创作场景。

## 24. 质询结果与锁定回答

| 质询 | 锁定回答 |
|---|---|
| 这是不是又写了一套假的 Memory？ | 不允许。runner 调生产 API/Service；参考实现只能保存真值和评分。 |
| 四个组各自重新创作，结果还能比较吗？ | 不能。先冻结消息和作品工作集，再只替换 Memory 视图。 |
| 当前 Golden 中的标签会不会提示模型答案？ | 会，因此新 E2E 数据把标签移出消息，只保存在不可见真值中。 |
| 摘要召回很好，能否证明 Writer 用好了？ | 不能，必须分别报告抽取、召回、到达和最终遵守。 |
| 为什么不只用 LLM Judge？ | 硬事实可以确定性判断；Judge 只补充开放性质量且需要人工抽查。 |
| 模型一次偶然生成得好怎么办？ | release/audit 固定三次重复，门槛看最差一次并报告方差。 |
| full history 如果超过窗口怎么办？ | 该样本标记 `not_comparable`，不能把裁剪历史伪装成完整历史。 |
| RAG 可能改善结果，怎么证明是 Memory？ | 因果套件关闭 RAG；系统套件冻结同一批 chunk，二者分开报告。 |
| Run 没有 conversation_id，当前会话如何优先？ | 现状只能评估项目级最近摘要合并；报告必须暴露该限制，不能假设已实现当前会话优先。 |
| ContextManifest 记录正确，实际发给模型的 Prompt 可能不同吗？ | 可能，所以必须由 EvalTraceLLM 捕获最终请求并与 Manifest hash 比对。 |
| 真实模型太贵，是否每个 PR 都跑？ | 不跑。PR 跑 FakeLLM 契约；smoke 用于配置验证；release/audit 人工显式开启。 |
| 评测失败会不会污染开发数据？ | 使用专用数据库和 Redis DB，且清理前执行四重安全校验。 |
