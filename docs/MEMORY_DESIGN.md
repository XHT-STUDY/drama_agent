# DramaAgent Memory 系统设计

> 状态：已评审，待实施  
> 日期：2026-09-18  
> 适用范围：对话式 Agent、剧本生成、修订、分批续写与上下文组装

## 1. 结论

DramaAgent 保留现有的分层 Memory 方向，不用通用向量记忆替换项目状态。改造重点是补齐生产调用链、累计摘要和版本化剧情状态，并用可重复的评测证明 Memory 改善了最终创作结果。

系统将 Memory 分成四类：

1. 原始事件，保存完整消息和不可变作品，负责恢复与审计。
2. 工作记忆，保存最近消息，降低高频读取成本。
3. 对话摘要，压缩已经离开短期窗口的历史意图。
4. 剧情状态，记录截至某集、基于某组作品版本成立的事实、人物知识、伏笔和时间线。

`ContextBuilder` 不保存 Memory。它负责在每次模型调用前选择、裁剪和记录实际使用的上下文。

## 2. 设计依据与文档关系

- [DEV_PLAN.md](DEV_PLAN.md) 仍是项目开发契约和状态记录的权威来源。
- [AGENT_NATIVE_IMPLEMENTATION_PLAN.md](AGENT_NATIVE_IMPLEMENTATION_PLAN.md) 阶段三已经定义剧情状态、采用工作集和失效重建。本设计沿用其中的 W3-01 至 W3-07，不另建平行状态模型。
- [MEMORY_IMPLEMENTATION_PLAN.md](MEMORY_IMPLEMENTATION_PLAN.md) 给出本设计的交付顺序、文件范围、验收和回滚方式。
- [DESIGN.md](../DESIGN.md) 中“聊天是控制面，不是内容事实源”的约束继续成立。

若文档冲突，优先级为：`DEV_PLAN.md`、本设计、Memory 实施计划、历史开发日志。当前代码和测试结果优先于所有历史描述。

## 3. 要解决的问题

### 3.1 对话会持续增长

完整历史每轮重发会增加 token、延迟和噪声。只保留最近消息又会丢失早期约束、否决项和未决问题。

### 3.2 故事状态跨越多个剧本版本

人物状态、角色已知信息、伏笔和时间线都依赖具体稿件。剧本修订后，旧状态可能只在旧版本上成立，不能把“项目最新摘要”当成无版本的全局事实。

### 3.3 Memory 可能比遗忘更危险

错误摘要、过期状态、跨项目召回和角色知识越界都会让后续内容建立在错误前提上。系统需要知道一条记忆来自哪里、适用于哪个版本、何时失效。

### 3.4 上下文容量有限

设定、对话历史、剧情状态、参考资料和当前稿件会争夺同一个上下文窗口。关键内容不能被静默截断，可选资料可以降级。

## 4. 目标与非目标

### 4.1 目标

- Redis 丢失、进程重启和摘要失败不造成原始消息或作品丢失。
- 长对话只携带累计摘要和最近消息，同时保留可追溯的原始记录。
- 每个剧情状态都绑定确切 StoryBible、大纲、剧本版本和前态。
- 写作、修订、连续性检查和分批恢复读取同一套状态服务。
- 必需事实缺失或过期时停止依赖生成，并返回可恢复错误。
- 每次上下文组装都有 Manifest，可解释采用、裁剪和降级决策。
- Memory 的收益可以通过消融实验、质量指标、延迟和 token 成本复现。

### 4.2 非目标

- 不让向量搜索决定锁定事实、采用版本或工作流状态。
- 不把全部聊天、代码或作品都复制到第二套 Memory 数据库。
- 不在部署迁移中调用 LLM 重写历史 Artifact。
- 不要求旧项目一次性升级。旧项目按采用工作集惰性派生剧情状态。
- 当前阶段不引入 Mem0、Neo4j 或新的常驻服务。

## 5. 当前实现快照

| 能力 | 当前实现 | 已有价值 | 主要缺口 |
| --- | --- | --- | --- |
| 原始消息 | PostgreSQL `Message` | 完整、有序、可恢复 | 无 |
| 短期窗口 | Redis List，默认 12 条，TTL 7 天 | Redis miss 可回源 DB | 主工作台上下文直接查 DB；Redis 主要写入，未承担主要读取 |
| 会话摘要 | 每 24 条触发一次 `conversation_summary` Artifact | 覆盖区间连续，失败不阻断消息 | 摘要是分段的，不是累计的；跨会话“最新”按不可比较的 sequence 选择 |
| Agent 上下文 | 当前会话摘要、最近消息、项目资产索引 | 有预算和受保护段 | `/agent/turns` 使用无 Memory 挂载的 `MessageService()`，主入口可能不触发摘要 |
| 剧情状态 | `ContinuityState`、`SummarizerSkill`、连续性检查 | 领域模型已经覆盖人物、伏笔、关系和时间线 | Writer 每个 Run 重建初态，只写简化摘要；完整 Summarizer 未接入；状态未按稿件版本持久化 |
| 上下文预算 | `ContextBuilder` 与 `ContextManifest` | 当前目标不静默截断 | required 段缺失目前只告警，关键状态缺失仍可能继续生成 |

## 6. 目标架构

```text
用户消息 / Agent 消息
        │
        ▼
PostgreSQL Message 事件日志
        │
        ├──────────────► Redis 最近消息窗口
        │                        │
        └──► 累计 ConversationSummary Artifact
                                 │
采用工作集 ─► EpisodeSummary ─► ContinuityState Artifact
    │               │                   │
    │               └──── 来源引用 ─────┘
    │
    ├── StoryBible / Outline / Script / Evaluation
    │
    ▼
Memory Read Model
    │
    ├── 权威事实投影
    ├── 当前角色知识投影
    ├── 累计对话摘要 + 最近消息
    └── 可选 RAG / 软记忆
    │
    ▼
ContextBuilder
    │
    ├── protected / required 校验
    ├── 按任务分配预算
    ├── 裁剪可选内容
    └── 记录 ContextManifest
    │
    ▼
Planner / Writer / Reviser / Evaluator
```

## 7. 各层职责

### 7.1 原始事件与作品是恢复依据

PostgreSQL 保存完整消息、Run、事件和 Artifact。Redis、摘要和投影都可从这些数据重建。原始历史不直接等于每轮模型上下文，但不能被摘要覆盖或替换。

Artifact 继续采用不可变版本。剧情状态、单集摘要和连续性检查都创建新 Artifact，不原地修改历史内容。

### 7.2 工作记忆只负责最近消息

短期窗口按 `conversation_id` 隔离，保留最近 N 条。读取规则固定为：

1. Redis 有数据时返回最近 N 条。
2. Redis miss 或连接失败时从 PostgreSQL 恢复。
3. Redis 写入和删除均为 best effort。
4. 所有生产消息入口使用同一个带 Memory 依赖的 `MessageService` 构造方式。

Redis 不能保存任何无法从数据库重建的唯一状态。

### 7.3 对话摘要采用累计语义

当前分段摘要改为累计摘要。第 K 版摘要的输入包括上一版摘要和新离开短期窗口的消息：

```text
summary_k = summarize(summary_k-1, messages[new_from:new_to])
```

摘要 Artifact 至少记录：

| 字段 | 含义 |
| --- | --- |
| `content_schema_version` | 摘要内容版本，新增版本写 `2.0` |
| `conversation_id` | 所属会话 |
| `summary` | 截至 `covered_to_sequence` 的累计摘要 |
| `topics` | 累计主题标签 |
| `covered_from_sequence` | 累计覆盖起点，通常为 1 |
| `covered_to_sequence` | 已压缩到的最后消息序号 |
| `message_count` | 累计覆盖消息数 |
| `previous_summary_artifact_id` | 上一版累计摘要，无则为空 |
| `source_message_digest` | 本次新增消息区间的确定性摘要哈希 |

读取同一会话摘要时按 `covered_to_sequence`、Artifact 版本和创建时间做稳定排序。跨会话不得比较各自的消息 sequence。项目级创作若需要多个会话的信息，应明确合并各会话的最新累计摘要，或生成独立的项目摘要，不能任选一条冒充项目全局记忆。

摘要保留核心创作意图、已确认设定、否决项和未决问题。它不获得高于 StoryBible、采用稿和结构化剧情状态的权威级别。

### 7.4 剧情状态绑定确切作品版本

剧情状态沿用 [AGENT_NATIVE_IMPLEMENTATION_PLAN.md](AGENT_NATIVE_IMPLEMENTATION_PLAN.md) 阶段三的契约。每一集正文和它的派生状态是两个提交点：正文先保存，随后生成 `episode_summary` 和 `continuity_state`。派生失败时保留正文并把状态标为待恢复，不能用标题摘要冒充完整状态。

状态至少能回答：

- 当前状态截至第几集；
- 使用了哪一版 StoryBible、大纲和前集剧本；
- 每条事实从哪一稿、哪一场得到；
- 角色当前知道什么，不知道什么；
- 哪些伏笔仍开放，哪些已经回收；
- 哪些内容是作者计划，哪些已经在正文发生；
- 状态是否相对当前采用工作集过期。

`ContinuityState` 更新由纯 reducer 完成。LLM 只输出经过 Pydantic 校验的 typed delta，不能直接覆盖完整状态，也不能伪造用户确认或来源引用。

### 7.5 ContextBuilder 负责读取策略

输入按权威性和任务需要分为三组：

- `protected`：当前用户约束、当前目标正文、适用锁定事实和必要前态。必须完整进入上下文，超预算时返回结构化错误。
- `required`：任务成立所必需的来源。缺失、跨项目、版本不匹配或过期时停止生成。
- `optional`：RAG、补充摘要和风格参考。可以裁剪或降级，但必须在 Manifest 记录原因。

权威冲突时采用固定顺序：

```text
当前明确用户指令
  > 当前采用工作集和结构化剧情状态
  > StoryBible 锁定事实
  > 当前会话累计摘要和最近消息
  > 项目历史摘要
  > RAG 与可选软记忆
```

低权威内容发现冲突时只产生 warning，不覆盖高权威事实。

## 8. 写入与读取流程

### 8.1 消息写入

1. 在数据库事务中锁定会话并写入带 sequence 的 Message。
2. 提交后写 Redis，失败只记录日志。
3. 达到摘要阈值时调度累计摘要。摘要失败不回滚消息。
4. 下一次摘要、上下文构建或创作 Run 发现摘要落后时，可以补做缺失区间。
5. 创建摘要时用覆盖终点、上一摘要 ID、Prompt 版本和消息 digest 组成幂等输入。

摘要不应阻塞普通消息确认。没有新增任务队列前，采用现有进程内执行能力做 post-commit best effort，并在后续读路径检查缺口。进入创作 Run 时允许等待补齐必要摘要，因为这段延迟属于后台创作过程，不占消息提交延迟。

### 8.2 单集生成与状态推进

1. 冻结本次 Run 的作品工作集。
2. 加载 `through = episode - 1` 的确切连续性状态。
3. ContextBuilder 校验 required/protected 内容并组装 Writer 上下文。
4. 保存本集候选正文。
5. Summarizer 从正文和前态生成 typed delta。
6. 服务端校验来源、ID、集数和知识边界。
7. reducer 生成新状态，原子保存 EpisodeSummary、ContinuityState、链接和事件。
8. 派生失败时保留正文，记录 `derivation_pending_episode`，重试只补派生。

### 8.3 修订与采用变化

修订第 N 集时，检查使用 `through = N - 1` 的前态。采用新版本后，第 N 集及其后缀状态标记为待复核；前缀来源完全相同时可以复用。GET 查询只读状态，不调用模型。重建动作由显式 Run 执行。

## 9. 失败与降级

| 故障 | 行为 |
| --- | --- |
| Redis 不可用 | 从 PostgreSQL 读取最近消息 |
| 摘要 LLM 失败 | 消息正常保存，保留摘要缺口并可补做 |
| 可选 RAG 不可用 | 继续生成，Manifest 记录 warning |
| 必要状态缺失 | 返回 `STORY_STATE_GAP`，不调用 Writer |
| 状态相对工作集过期 | 返回 `STORY_STATE_STALE`，提示刷新状态 |
| 受保护内容超预算 | 返回 `PROTECTED_CONTEXT_TOO_LARGE` |
| 派生提交前崩溃 | 依据 input hash 重试，数据库结果不得重复 |
| SSE 发布失败 | Artifact 仍有效，客户端通过 DB 轮询恢复 |

## 10. 评测方法

Memory 评测分成写入、召回、使用和成本四层。只验证 Artifact 存在不能说明 Memory 有效。

### 10.1 对话记忆

建立 30 组可审查对话，分别扩展到 24、48、96 和 192 条消息，覆盖偏好、否决、改口、未决问题、跨项目近似设定和无关闲聊。

指标与门槛：

| 指标 | 门槛 |
| --- | ---: |
| 跨项目泄漏 | 0 |
| 最新事实胜率 | ≥ 95% |
| 明确否决召回率 | ≥ 95% |
| 96 条消息后的关键约束召回率 | ≥ 90% |
| 虚构记忆率 | ≤ 1% |
| Redis 清空后原始消息恢复率 | 100% |

### 10.2 剧情连续性

建立至少 10 个十集 StoryBible 场景，固定人物状态、知识边界、伏笔、道具归属、时间顺序和反转。比较无 Memory、最近消息、当前实现和新版结构化状态四组。

指标与门槛：

| 指标 | 门槛 |
| --- | ---: |
| 锁定事实确定性检查 | 100% |
| 项目和版本来源正确率 | 100% |
| 人物知识越界率 | 0 |
| 状态重放一致率 | 100% |
| 伏笔召回与回收 F1 | ≥ 90% |
| 相对无 Memory 的连续性违规下降 | ≥ 50% |

### 10.3 成本与交互

- 对比完整历史、当前实现和新版实现的输入 token、首 token 延迟、总延迟与摘要成本。
- 目标是在质量下降不超过 2 个百分点时，相比完整历史节省至少 60% 的历史上下文 token。
- 普通消息提交不等待摘要 LLM。
- ContextManifest 的预算、截断和引用记录必须与实际 Prompt 一致。

所有自动化评测默认用 FakeLLM。真实模型评测需要显式开关、独立预算和固定样本，结果必须记录模型、Prompt 版本、时间与样本数。

## 11. Mem0 的使用边界

Mem0 适合抽取并语义召回跨会话的用户偏好、历史决定和软性经验。它的默认流程会根据新消息检索已有 Memory，再由 LLM 决定增加、更新或删除；作用域可按用户、Agent 和 Run 隔离。参考资料：

- [Mem0 Memory Types](https://docs.mem0.ai/core-concepts/memory-types)
- [Mem0 Graph Memory](https://docs.mem0.ai/open-source/features/graph-memory)
- [Mem0 论文](https://arxiv.org/abs/2504.19413)

当前不引入 Mem0。以后只有在评测证明“跨项目用户偏好召回”是主要质量缺口，并且现有 PostgreSQL、pgvector 方案无法以较低成本满足时，才启动 shadow mode 试验。

即使引入，Mem0 也只能保存软记忆：类型偏好、节奏偏好、语言风格和常见修改倾向。锁定事实、采用版本、人物状态、角色知识、伏笔和工作流状态继续由 PostgreSQL 与 Artifact 管理。

## 12. 与编码 Agent Memory 的区别

Claude Code 和 pi 主要保存项目规则、用户反馈、任务进度、工具调用和文件操作，代码与 Git 仍是事实源。Claude Code 把 `CLAUDE.md` 与 auto memory 分开，自动记忆只保存未来仍有用且不能直接从代码推导的内容。pi 的累计压缩保留最近消息、上一份摘要、工具调用边界和文件操作。

DramaAgent 还要维护虚构世界在某个作品版本上的状态。它需要领域 reducer、来源引用、有效区间和后缀失效机制，不能只依赖会话压缩或语义召回。

参考资料：

- [Claude Code Memory](https://code.claude.com/docs/en/memory)
- [pi Compaction and Branch Summarization](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md)

## 13. 已锁定决策

1. PostgreSQL 和不可变 Artifact 是唯一权威持久层。
2. Redis 只保存可重建的最近消息窗口。
3. 对话摘要采用累计语义，不能用某个会话的 sequence 代表项目时间。
4. 剧情状态绑定确切作品工作集，并保存来源引用。
5. LLM 只生成受校验的摘要或 delta，服务端控制 ID、来源、有效区间和状态更新。
6. 必需 Memory 缺失时停止生成，可选增强缺失时允许降级。
7. 先建立评测基线，再改实现；Mem0 不进入当前交付范围。

