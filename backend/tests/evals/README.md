# 对话命令评测数据集 —— 标注规范与纪律

> 适用文件：`agent_commands.json`（开发/固定回归集，240 条）与
> `agent_commands_holdout.json`（盲测集，120 条）。合并恰好 360 条，
> 分布与交叉覆盖下限见 `docs/INTENT_RECOGNITION_OPTIMIZATION_PLAN.md` §7.2，
> 由 `test_agent_command_eval.py::TestCommandDatasetContract` 机器守护。

## 1. Case 字段合同（§4.1）

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✅ | 全局唯一；开发集 `cmd-NNN`，盲测集 `hold-NNN` |
| `split` | ✅ | `dev` / `holdout`，与所在文件一致 |
| `user_request` | ✅ | 用户原文；不含真实隐私、密钥、本地路径 |
| `active_context` | ✅ | 可空；页面上下文（artifact_id 用零值 UUID 占位） |
| `available_intents` | ✅ | 服务端白名单；只允许生产可达状态（默认 5 意图 / +continue / 早期项目窄白名单） |
| `target_episode_count` | ✅ | 项目目标集数（评测统一 10） |
| `expected.turn_type` | ✅ | `plan` / `answer` / `clarification` |
| `expected.intent` | ✅ | 可空；plan 必填（不含 explain）；answer 只允许 `explain`（正文/设定/大纲/评估解释）或空（项目状态）；clarification 必空 |
| `expected.target_type` | ✅ | 可空；revise_outline→outline、revise_script→script、explain→script/outline/story_bible/evaluation、continue→project；evaluate 不标（服务端只消费集数） |
| `expected.episode_number` | ✅ | 可空；明确文本集数或上下文集数；evaluate/revise_script/explain 可标 |
| `expected.batch_size` | ✅ | 仅 plan/continue；`null`=写完剩余全部（是明确契约，不是缺失） |
| `preflight_deterministic` | ✅ | true=确定性 preflight 在模型前澄清（契约测试强制校验真实行为一致） |
| `risk` | ✅ | `write_op` / `read_only` / `out_of_scope` / `safety` |
| `coverage` | ✅ | 覆盖标签（词表见 harness `COVERAGE_VOCAB`；交叉标签，一 case 可计多维） |
| `note` | ✅ | 一句人工标注意图说明（为什么是这个标签/边界在哪） |
| `recent_dialog` | ⬜ | 多轮用例：上一轮对话，`[seq] role: text` 每行一条；harness 以生产 `project_context` 的"最近消息"格式注入 |
| `unresolved_turn_count` | ⬜ | 多轮澄清轮数（配合 recent_dialog） |

## 2. 期望语义要点（Prompt v1.4）

- **只有生成 Action 的请求标 `plan`**；explain 是只读分支，`answer + intent=explain`。
- **项目状态类**（进度/下一步/版本数）是 `answer` 且**不带 intent**——带 explain
  会触发服务端读原文路径，属语义分支错误，评分器按 intent 错误计。
- **查看已有评估**（读 evaluation Artifact）= `answer/explain/evaluation`；
  **重跑评估** = `plan/evaluate`。
- **明确文本目标优先于活动上下文**：文本说第 3 集、页面在第 2 集 → 期望第 3 集。
- **一次一个目标**：多集/多对象修订、跨 intent 复合请求 → `clarification`；
  同一目标的多约束（"节奏拖了，对白也尬"）→ 正常 plan。
- **白名单没有 continue**（无门上 Run）而用户要继续 → `clarification`，不得捏造意图。

## 3. Split 纪律（§7.3）

- **先写边界矩阵，再补样本**：每条 case 对应一个可陈述的边界
  （对象歧义 / 目标冲突 / 范围模糊 / 白名单漂移 / 表达变体），不按"每类凑数量"复制句子。
- **盲测集不进 Prompt 示例**：契约测试做逐字（去空白归一化）比对；
  近似重复由人工复核，评审时对照 `app/prompts/templates/agent_command_planner.md`。
- **盲测集不按单条失败调词**：只在候选版本冻结后整体运行；
  已用于 Prompt 调优的样本只能进开发集。
- **保留困难样本**：不因模型经常失败就改成容易标签；标注只服从生产契约。
- **一人标注、一人复核**；争议 case 在 note 里写决策理由。
- 修改任何标签必须更新两个文件顶部的 `changelog` 并 bump `version`。

## 4. 已知边界（标注时绕开或显式接受）

1. **preflight 多集数正则的过度触发**：文本含两个集数 + 修订动词
   （"修改第2集和第5集"）会被确定性澄清，即使语义是大纲内编辑
   （"调整大纲，第2集和第3集合并"曾被误伤）。在 IR-3 正则收敛前，
   大纲编辑类用例避免出现两个"第N集"字样（改用"前两集"等表述）。
   修复后应把这类表达加回并用 `preflight_consistency` 契约验证。
2. **裸"改/评"动词不命中修订正则**：如"改一下结局""帮我改一下"走模型
   而非 preflight——标 `pf=false`，期望 clarification 由模型给出。
3. **"下一批"语义 = batch 1**：与短路层 `_NEXT_RE` 生产语义一致。
4. **bare 确认/继续短语归短路层**：整句"好的""继续""写5集"（≤16 字符）
   在生产中由 `agent_shortcut` 处理、不经 Planner；评测集不含这类整句
   短路用例（由 `tests/integration/api/test_agent_shortcut.py` 覆盖），
   多轮/复合变体（"好的开始吧，写完剩下的"）才进 Planner 评测。
5. **多轮目标解析依赖 project_context 的最近消息**：preflight 不读对话
   历史，因此"这场戏太干了，重写"+历史（无活动上下文）仍会被确定性澄清
   （cmd-170 显式记录该局限，IR-3 处理）。

## 5. Changelog

| version | 日期 | 变更 |
|---|---|---|
| 4 | 2026-09-18 | IR-2：开发集扩至 240 条（+152），新增盲测集 120 条；新增 recent_dialog/unresolved_turn_count 多轮标注；合并分布与交叉覆盖下限由契约测试守护 |
| 3 | 2026-09-18 | IR-1：期望输出对齐 Prompt v1.4（explain 归 answer 分支）；新增 21 条 continue 基础集；新增 split/target_type/batch_size/risk/coverage 标注 |
| 2 | 2026-08 | W1-03：明确目标优先与集数解析用例 |
| 1 | 2026-08 | J-12：初版 55 条 |

## 6. 运行

```bash
# CI（零模型调用）
python -m pytest tests/evals/test_agent_command_eval.py -q

# 真实模型（需 .env 真实 Key；发版：EVAL_REPEATS=3 且 EVAL_SPLIT=all）
EVAL_LLM_ENABLED=1 EVAL_REPEATS=3 python -m pytest tests/evals/test_agent_command_eval.py -m eval_real -q
# 开发期只产报告：
EVAL_LLM_ENABLED=1 EVAL_REPORT_ONLY=1 EVAL_SPLIT=dev python -m pytest tests/evals/test_agent_command_eval.py -m eval_real -q
```
