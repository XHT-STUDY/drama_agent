🥷 先给结论：这个项目的“意图识别”不是简单的文本分类器，也不是让模型直接决定调用哪个工具，而是一条“规则预检 + 受限 LLM Planner + 服务端二次编译”的安全管线。

核心链路可以概括为：

```text
用户消息
  → 幂等与上下文校验
  → 确定性短语短路
  → 构建项目上下文
  → 规则预检歧义
  → LLM 输出 AgentPlannerOutput
  → 服务端白名单/安全校验
  → clarification / answer / plan 三路分流
  → plan 被编译成 AgentActionPlan
  → 用户确认
  → WorkflowRun
  → 具体创作、评估或修订工作流
```

最重要的一点是：

> “意图识别”的直接输出是 `AgentPlannerOutput`；真正交给执行层的是服务端根据它重新构造的 `AgentActionPlan.command`。  
> 模型不能直接输出 Artifact ID、Run ID、API、工具名或任意执行参数。

---

## 一、系统目前识别哪些意图

领域类型定义了六个意图：[agent_command.py](F:/xht_code/drama_agent/backend/app/domain/agent_command.py:19)

| intent | 含义 | 是否创建或恢复 Run |
|---|---|---:|
| `create_script` | 从创意创建 Story Bible、大纲和剧本 | 是 |
| `explain` | 解释项目状态或已有正文 | 否，只读回答 |
| `evaluate` | 评估整个项目或某一集剧本 | 是 |
| `revise_script` | 修订指定集剧本 | 是 |
| `revise_outline` | 修订分集大纲 | 是 |
| `continue` | 从大纲确认门或剧本批次确认门继续 | 恢复已有 Run |

通常 Planner 的基础白名单是前五种；只有项目确实存在停在确认门的 Run 时，服务端才动态加入 `continue`：[agent_command_service.py](F:/xht_code/drama_agent/backend/app/application/agent_command_service.py:769)。

因此，严格来说它是“五个基础意图 + 一个状态相关意图”。

此外还有 `confirm`、`retry` 等操作，但它们不是 Planner 的正式 intent，而是前置的确定性短语短路。

---

## 二、全流程每个环节的输入和输出

### 1. 前端提交 Agent Turn

入口是：

```http
POST /projects/{project_id}/agent/turns
```

请求结构定义在 [agent.py](F:/xht_code/drama_agent/backend/app/api/v1/agent.py:39)：

```json
{
  "conversation_id": "...",
  "content": "润色第2集对白，让女主更主动",
  "active_context": {
    "artifact_id": "...",
    "artifact_type": "script_draft",
    "episode_number": 2,
    "scene_number": 3,
    "version": 1,
    "checksum": "..."
  },
  "idempotency_key": "...",
  "target_episode_count": 10,
  "staged": true
}
```

主要输入含义：

- `content`：本轮自然语言请求。
- `active_context`：用户当前正在看的稿件、集数、场次和版本。
- `idempotency_key`：避免双击、网络重试导致重复调用模型或重复创建任务。
- `target_episode_count`：创作任务目标集数。
- `staged`：是否先生成设定和大纲，再停下来等待确认。

前端提交、202 轮询和终态处理位于 [use-agent-conversation.ts](F:/xht_code/drama_agent/frontend/src/hooks/use-agent-conversation.ts:148)。

输出暂时不是 intent，而是一条持久化的 `AgentTurn`，初始进入 `planning`。

---

### 2. 幂等、会话和活动上下文校验

服务端首先：

1. 根据整个请求生成 SHA-256 请求哈希。
2. 检查同一个 `idempotency_key` 是否已经处理。
3. 创建或加载会话。
4. 验证 `active_context` 确实属于当前项目，且 Artifact、版本、集数、场次合法。
5. 保存用户消息和 Turn 收据。

代码在 [agent_command_service.py](F:/xht_code/drama_agent/backend/app/application/agent_command_service.py:294)。

这一阶段的输入是 HTTP 请求；输出是：

- 一条用户 Message；
- 一条状态为 `planning` 的 AgentTurn；
- 或重复请求时直接返回以前的结果；
- 或非法上下文、幂等键冲突错误。

这一步还没有调用 LLM。

---

### 3. 确定性短语短路

系统会先检测非常明确的短语，如：

- `确认`
- `好的`
- `继续写`
- `下一集`
- `先写5集`
- `写全部`
- `重试`

识别规则在 [agent_shortcut.py](F:/xht_code/drama_agent/backend/app/skills/agent_shortcut.py:1)。

输入：

```text
用户原始 content
```

输出：

```python
("confirm", None)
("continue", 5)
("retry", None)
None
```

如果命中且存在可操作目标，就直接：

- 确认最新的 proposed Action；
- 恢复停在确认门上的 Run；
- 从断点重试最近失败的 Run。

这条路径完全不调用 Planner。实际分流在 [agent_command_service.py](F:/xht_code/drama_agent/backend/app/application/agent_command_service.py:905)。

为了避免误执行，它采用整句匹配和 16 字长度限制。例如：

- `好的`：可以直接确认。
- `好的，改成悬疑吧`：不能短路，会回到 Planner，因为它还包含新的修改要求。

---

### 4. 构建 Planner 上下文

没有短路时，`AgentContextService` 会构建受预算约束的项目上下文：[agent_context_service.py](F:/xht_code/drama_agent/backend/app/application/agent_context_service.py:104)。

输入包括：

- 当前项目；
- 最近会话消息和会话摘要；
- 用户本轮请求；
- Story Bible 摘要；
- 分集大纲摘要；
- 已有剧本的集数/版本索引；
- 已有评估的索引；
- 当前选中的 Artifact 摘要。

输出是一个 `project_context` 字符串和 ContextManifest。

这里有一个重要边界：

> Planner 上下文不包含完整剧本正文，只包含足够做路由和目标判断的摘要、索引和当前目标信息。

所以 Planner 可以判断“这是解释第3集”，但不应该凭摘要回答“第3集人物为什么突然翻脸”。正文解释会在后面走独立的原文读取流程。

---

### 5. 生成动态白名单和 `AgentPlannerInput`

服务端组装的 Planner 输入结构在 [agent_planner.py](F:/xht_code/drama_agent/backend/app/domain/agent_planner.py:23)：

```json
{
  "user_request": "润色第2集对白，让女主更主动",
  "project_title": "项目名称",
  "target_episode_count": 10,
  "available_intents": [
    "create_script",
    "explain",
    "evaluate",
    "revise_script",
    "revise_outline"
  ],
  "active_context": null,
  "project_context": "...",
  "unresolved_turn_count": 0
}
```

其中 `available_intents` 是服务端提供的能力边界，不由客户端或模型决定。

`unresolved_turn_count` 表示最近连续澄清了多少轮；达到三轮仍无法解决时，系统会给用户列出合法命令示例，而不是无限追问。

---

### 6. 模型调用前的确定性预检

在真正调用模型前，系统会先用规则检查：[agent_command_planner.py](F:/xht_code/drama_agent/backend/app/skills/agent_command_planner.py:137)。

主要规则有：

#### 集数越界

项目只有 10 集，用户说：

```text
评估第11集
```

直接输出澄清：

```json
{
  "turn_type": "clarification",
  "clarification_question": "项目只有 10 集，请确认你要操作第 11 集，还是改为项目范围？"
}
```

不调用 LLM。

#### 修改目标缺失

```text
帮我改一下这里
```

但没有 `active_context`，系统不知道“这里”指什么，直接追问目标。

#### 一次修改多个目标

```text
修改第2集和第5集
```

当前策略是一次只修改一个目标，因此直接要求用户选择。

#### 约束冲突

例如文本同时出现明显的“保留”和“删除”冲突模式，会要求用户明确取舍。

#### 修订能力没有开放

如果当前白名单不包含修订意图，而请求明显是在修改内容，也不会让模型硬选其他 intent。

这一层的输出只有两种：

- `AgentPlannerOutput(turn_type="clarification")`
- `None`，表示没有确定性阻断，继续调用模型。

现有测试会用“只要调用 LLM 就立即报错”的桩证明这些 case 确实零模型调用。

---

### 7. LLM Planner 做受限语义识别

通过预检后，系统使用 `agent_command_planner` Prompt 调用模型，温度为 `0.1`，要求结构化输出：[agent_command_planner.py](F:/xht_code/drama_agent/backend/app/skills/agent_command_planner.py:259)。

Prompt 当前版本是 1.4，位于 [agent_command_planner.md](F:/xht_code/drama_agent/backend/app/prompts/templates/agent_command_planner.md:1)。

模型负责判断：

- 本轮是 `clarification`、`answer` 还是 `plan`；
- intent 是什么；
- 目标类型和目标集数；
- 用户约束；
- 可读步骤；
- 预期影响；
- `continue` 时的批量集数。

它不负责：

- 选择真实工具；
- 输出 Artifact ID；
- 输出 Run ID；
- 输出 SQL/API；
- 决定确认策略；
- 直接执行工作流。

---

## 三、“意图识别”环节的最终输出是什么

直接输出是 `AgentPlannerOutput`：[agent_planner.py](F:/xht_code/drama_agent/backend/app/domain/agent_planner.py:59)。

完整结构为：

```json
{
  "turn_type": "clarification | answer | plan",
  "intent": "create_script | explain | evaluate | revise_script | revise_outline | continue | null",
  "target": {
    "target_type": "project | story_bible | outline | script | evaluation",
    "episode_number": 2
  },
  "constraints": [
    "对白更自然",
    "女主更主动"
  ],
  "steps": [
    {
      "title": "识别修订目标",
      "description": "定位第2集并保留既有剧情事实"
    }
  ],
  "expected_impact": [
    "生成第2集的新版本"
  ],
  "clarification_question": null,
  "answer": null,
  "batch_size": null
}
```

因此，意图识别结果不是单独一个 `intent` 标签，而是一个带有分支类型、目标选择器、用户约束和交互内容的结构化结果。

### `turn_type` 比 `intent` 更先决定下游行为

#### `clarification`

表示现在还不能安全执行：

```json
{
  "turn_type": "clarification",
  "intent": null,
  "clarification_question": "你希望修改哪一个目标：大纲、剧本，还是指定集数？"
}
```

#### `answer`

表示这是只读问答：

```json
{
  "turn_type": "answer",
  "intent": "explain",
  "answer": "我来读取第3集原文。"
}
```

项目状态类问题也可能是：

```json
{
  "turn_type": "answer",
  "intent": null,
  "answer": "当前已完成3集剧本。"
}
```

#### `plan`

表示识别出了一个需要执行的业务动作：

```json
{
  "turn_type": "plan",
  "intent": "revise_script",
  "target": {
    "target_type": "script",
    "episode_number": 2
  },
  "constraints": ["对白更自然"],
  "steps": [...]
}
```

---

## 四、模型输出之后还有哪些强制校验

模型输出会经过 `_validate_output()`：[agent_command_planner.py](F:/xht_code/drama_agent/backend/app/skills/agent_command_planner.py:220)。

主要检查包括：

- intent 必须在服务端本轮提供的白名单中；
- `plan` 必须有 target 和 steps；
- `clarification` 不能同时携带 intent、target 或 answer；
- `answer` 必须有答复；
- 输出任何位置不得包含 API、SQL、工具调用、URL、UUID 或 Artifact ID；
-所有 Pydantic Schema 都是 `extra="forbid"`，额外字段直接拒绝。

因此即使模型试图输出：

```json
{
  "intent": "delete_database"
}
```

或：

```text
调用某工具操作 artifact_id=...
```

也会被拒绝，Turn 最终进入 `failed`，不会执行。

---

## 五、下游如何使用这个输出

### 分支一：澄清

`turn_type="clarification"` 时：

- 保存一条 `kind="clarification"` 的 Assistant Message；
- AgentTurn 从 `planning` 变为 `needs_input`；
- 不创建 AgentAction；
- 不创建 Run；
- 等用户下一轮补充信息。

代码在 [agent_command_service.py](F:/xht_code/drama_agent/backend/app/application/agent_command_service.py:1108)。

---

### 分支二：回答或解释

`turn_type="answer"` 时：

- 保存文本消息；
- AgentTurn 变成 `answered`；
- 不创建 AgentAction；
- 不需要用户确认；
- 不创建 WorkflowRun。

对于普通项目状态问题，直接使用 Planner 的 `answer`。

对于正文问题，如果输出是：

```json
{
  "turn_type": "answer",
  "intent": "explain"
}
```

服务端不会相信 Planner 凭摘要生成的解释，而会继续执行：

1. 根据明确集数、对象或活动上下文定位确切 Artifact。
2. 读取剧本场次、大纲或 Story Bible 原文。
3. 调用 `ArtifactExplainerSkill`。
4. 要求模型只返回 `source_index + quote`。
5. 验证引用确实出现在原文中。
6. 再回填真实 `artifact_id/version/checksum`。

这条链路在 [agent_command_service.py](F:/xht_code/drama_agent/backend/app/application/agent_command_service.py:790) 和 [agent_context_service.py](F:/xht_code/drama_agent/backend/app/application/agent_context_service.py:164)。

如果没有可验证引文，系统不会附带未经核实的剧情解释。

---

### 分支三：计划

`turn_type="plan"` 时，模型输出还不能直接执行。

服务端调用 `_build_action_plan()` 将它编译成 `AgentActionPlan`：[agent_command_service.py](F:/xht_code/drama_agent/backend/app/application/agent_command_service.py:1225)。

最终计划包含：

```json
{
  "goal": "按用户要求修订第2集剧本并重评",
  "intent": "revise_script",
  "command": {
    "intent": "revise_script",
    "source_script_id": "服务端解析出的真实ID",
    "episode_number": 2,
    "constraints": ["对白更自然", "女主更主动"]
  },
  "target": {
    "target_type": "script",
    "episode_number": 2
  },
  "constraints": [...],
  "steps": [...服务端固定模板...],
  "expected_impact": [...]
}
```

这里发生了几件关键的“二次编译”：

- 模型只给出 `episode_number=2`；
- 服务端查询第2集最新有效剧本；
- 服务端补入真实 `source_script_id`；
- 服务端记录该稿件的 `version + checksum` 快照；
- 模型给出的 `steps` 不直接用于执行，实际计划步骤来自服务端固定模板；
- 模型给出的约束会进入真正的修订命令。

然后系统创建：

```text
AgentAction(status="proposed", requires_confirmation=true)
```

并把 Turn 置为 `action_proposed`。

---

## 六、用户确认后如何衔接工作流

用户点击确认或回复“确认”后，服务端不会接受客户端重新提交的计划内容，而只读取此前持久化的服务端 Plan：[agent.py](F:/xht_code/drama_agent/backend/app/api/v1/agent.py:176)。

确认过程包括：

1. 锁定 AgentAction。
2. 检查是否已确认过，重复确认复用原 Run。
3. 检查来源 Artifact 是否仍是最新有效版本。
4. 比较 Artifact ID、version、checksum。
5. 如果内容已更新，把 Action 标记为 `stale`，要求重新规划。
6. 将 intent 映射成固定 Workflow action。
7. 把 command 转换成 Run config。
8. 创建或恢复 WorkflowRun。
9. 调度 Worker 执行。

映射关系在 [agent_command_service.py](F:/xht_code/drama_agent/backend/app/application/agent_command_service.py:101)：

```python
{
    "create_script": "create_script",
    "evaluate": "evaluate",
    "revise_script": "revise_script",
    "revise_outline": "revise_outline",
}
```

`explain` 不创建 Run。

`continue` 不创建新 Run，而是恢复已有的 gated Run。

---

## 七、不同 intent 的下游数据如何转换

| Planner intent | 服务端补充的信息 | Run config | 下游工作流 |
|---|---|---|---|
| `create_script` | 原始用户请求、有效集数、是否阶段暂停 | `user_input`、`outline_count`、`script_count`、`stop_after` | Creation Workflow |
| `evaluate` | project/episode 范围、目标剧本快照 | `scope`、`episode_number` | Evaluation Workflow |
| `revise_script` | 最新有效剧本 ID、版本、checksum | `source_script_artifact_id`、集数、用户约束 | Conversational Revision Workflow |
| `revise_outline` | 最新有效大纲 ID、版本、checksum | `source_outline_artifact_id`、用户约束 | Outline Revision Workflow |
| `explain` | 确切原文和可验证引用 | 无 Run config | 直接回答 |
| `continue` | 已暂停 Run ID、阶段世代、批量集数 | 恢复已有 Run | 原创作工作流继续执行 |

具体 Run config 构造见 [agent_command_service.py](F:/xht_code/drama_agent/backend/app/application/agent_command_service.py:1444)，Worker 分发见 [workflow_dispatcher.py](F:/xht_code/drama_agent/backend/app/application/workflow_dispatcher.py:476)。

---

## 八、典型 case 全链路

### Case 1：发起创作

用户：

```text
写一个被青训队抛弃的足球少年逆袭短剧
```

流程：

1. 不命中确认/续跑短路。
2. 规则预检没有歧义。
3. Planner 输出：

```json
{
  "turn_type": "plan",
  "intent": "create_script",
  "target": {"target_type": "project"},
  "constraints": [],
  "steps": [...]
}
```

4. 服务端忽略模型给出的执行步骤，生成固定创作计划。
5. `CreateScriptCommand.user_input` 使用用户原文。
6. 集数按以下优先级确定：

```text
本次请求 target_episode_count
  > 项目的 target_episode_count
  > 系统默认值
```

7. 产生 proposed Action。
8. 用户确认后创建 `action=create_script` 的 Run。
9. Worker 依次生成需求、Story Bible、大纲。
10. 默认 `staged=true` 时停在大纲确认门，不立即写剧本。

---

### Case 2：明确修订第2集

用户：

```text
润色第2集的对白，让女主更主动
```

流程：

1. 规则提取到 `episode_number=2`，没有必要追问。
2. Planner 应输出：

```json
{
  "turn_type": "plan",
  "intent": "revise_script",
  "target": {
    "target_type": "script",
    "episode_number": 2
  },
  "constraints": [
    "润色对白",
    "让女主更主动"
  ]
}
```

3. 服务端查询第2集最新 valid `script_draft`。
4. 如果不存在，Turn 失败并返回“第2集没有可修订的有效剧本”。
5. 如果存在，服务端生成 `ReviseScriptCommand`，包含真实 source ID。
6. 同时保存来源版本快照。
7. 用户确认时再次检查这份剧本有没有被更新。
8. 工作流依次执行：

```text
锁定目标
→ 若缺评估则先评估
→ 生成修订计划和候选稿
→ 连续性检查
→ 重新评估
```

9. 用户约束会被拼接成 `user_instruction` 注入修订工作流：[workflow_dispatcher.py](F:/xht_code/drama_agent/backend/app/application/workflow_dispatcher.py:606)。

---

### Case 3：含糊的“改这里”

用户：

```text
帮我改一下这里
```

且没有 `active_context`。

结果由规则层直接产生：

```json
{
  "turn_type": "clarification",
  "clarification_question": "你希望修改哪一个目标：大纲、剧本，还是指定集数？"
}
```

特点：

- 零 LLM 调用；
- 不生成 Action；
- 不产生 Run；
- Turn 状态为 `needs_input`。

如果用户正在第3集剧本页，并传入合法 `active_context`，Planner 才可以把“这里”解析为第3集或当前场景。

---

### Case 4：明确文本目标覆盖页面上下文

用户当前正在查看第2集，但说：

```text
修改第3集结尾
```

优先级是：

```text
消息中明确写出的目标 > active_context
```

所以应选择第3集，而不是第2集。

这个规则既写进了预检，也写进了 Prompt。相关测试在 [test_agent_command_planner.py](F:/xht_code/drama_agent/backend/tests/unit/skills/test_agent_command_planner.py:134)。

---

### Case 5：正文解释

用户：

```text
第三集这场翻脸为什么这么突然？
```

流程：

1. Planner 判定为：

```json
{
  "turn_type": "answer",
  "intent": "explain"
}
```

2. 服务端从“第三集”中解析集数。
3. 读取第3集最新有效剧本。
4. 如果正文在预算内，读取各场原文；如果活动上下文指定了某一场，只读该场。
5. 可附带 Story Bible 作为辅助来源。
6. Explainer 生成带引用的解释。
7. 服务端验证 quote 确实存在于原文。
8. 返回回答和引用元数据。
9. 不创建 Action、Run 或新 Artifact。

如果整集正文太长，又没有指定场次，系统会要求缩小范围，而不是截断正文后假装已经读完。

---

### Case 6：确认和继续

用户收到计划后回复：

```text
确认
```

系统直接确认当前会话最新的 proposed Action，不经 Planner。

创作生成完大纲并停在确认门后，用户说：

```text
先写5集
```

系统直接解析为：

```python
("continue", 5)
```

恢复已有 Run，本批写5集。

如果用户写的是较长复合指令：

```text
继续写，但把第三集结尾改成开放式
```

它不会命中短路，而会回到 Planner，因为这里同时包含继续与新约束。

---

## 九、这个设计的关键安全边界

该实现把模型权限压得很低：

- 模型只能选择服务端提供的 intent。
- 模型不能创造工具或 Workflow。
- 模型不能提供 Artifact ID。
- 真实目标 ID 由服务端查询。
- 可执行步骤由服务端模板生成。
- 修改和评估计划需要用户确认。
- 确认时检查来源版本是否过期。
- 同项目只能有一个活跃 Run。
- Turn、确认和续跑都有幂等保护。
- 全部结构使用严格 Schema，额外字段不被静默接受。

因此可以把它理解成：

```text
LLM = 自然语言归一化器
服务端 = 权限边界 + 目标解析器 + 计划编译器
Workflow = 真正执行者
```

---

## 十、实现中值得注意的几个点

### 1. Planner 输出的 `steps` 只是中间信息

对于可执行 plan，`_build_action_plan()` 会重新生成服务端固定步骤。模型输出的 `steps` 主要用于满足 Planner 契约，不会成为任意执行指令。

真正被保留下游使用的主要是：

- `intent`
- `target.episode_number`
- `constraints`
- `batch_size`
- 部分 `expected_impact`

### 2. 目标 ID 安全，但集数判断仍依赖模型

服务端禁止模型输出 Artifact ID，并根据集数查询真实 Artifact，这是安全的。

不过 `revise_script` 和单集 `evaluate` 的 `_build_action_plan()` 仍从 `output.target.episode_number` 取集数。也就是说，虽然规则层能解析用户消息中的集数，但它没有把解析结果强制覆盖回模型输出；模型如果漏掉集数，修订计划可能失败。

更稳妥的实现可以在服务端再次从用户原文提取集数，并与模型输出做一致性校验。

### 3. 用户约束可能被模型遗漏

修订工作流使用的是 `output.constraints`，而不是原始用户文本的确定性结构化结果。因此模型若漏掉“保留人物关系”“不能新增角色”等约束，下游也会漏掉。

可以考虑同时保留：

```text
raw_user_request + structured_constraints
```

供确认界面和修订工作流交叉核对。

### 4. 单集评估链路存在值得核查的范围风险

Plan 和 Run config 会保存：

```json
{
  "scope": "episode",
  "episode_number": 3
}
```

但当前 Dispatcher 构造 Evaluation Workflow 初始状态时，读取的是项目全部最新有效剧本：[workflow_dispatcher.py](F:/xht_code/drama_agent/backend/app/application/workflow_dispatcher.py:562)。

Evaluation Workflow 又直接评估状态中的 `script_artifact_ids`。从当前代码看，存在“用户要求评估第3集，但实际把全部剧本送入评估”的风险，建议专门补一条端到端测试确认。

### 5. 当前真实模型评测结果不是最新版本

目前数据集已有 65 条 case，但仓库中的真实模型结果文件仍记录：

- Prompt 版本：1.3
- Case 数：60
- `create_script` F1：约 96.8%
- `evaluate` F1：约 93.3%
- `explain` F1：80%
- `revise_script` F1：约 57.1%
- `revise_outline` F1：约 33.3%
- 澄清召回：87.5%

结果见 [agent_commands_results.json](F:/xht_code/drama_agent/backend/tests/evals/results/agent_commands_results.json:1)。

当前 Prompt 已是 1.4，数据集也扩充到 65 条，所以旧报告不能代表当前模型效果。特别是历史上的修订意图召回率偏低，值得重新跑一次真实模型评测。

本次我运行了当前的 Planner 单元测试、Schema 契约测试和确定性评测，共 26 项，全部通过；这验证的是规则和结构契约，不等同于重新验证真实模型准确率。