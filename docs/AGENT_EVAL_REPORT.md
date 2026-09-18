# Agent 评测报告（J-12 / IR-1）

> 本报告区分两种评测：**CI 契约评测**（FakeLLM / 确定性规则，每次 `make test` 执行）
> 与**真实模型评测**（显式 marker，需真实 API Key）。**真实模型评测不得写入任何模拟数字**——
> 未执行的部分明确标注"未执行"与原因。
>
> IR-1（2026-09-18）重写了评测尺子；IR-2（2026-09-18）把数据扩充为
> 240 条开发集 + 120 条盲测集（合并 360，分布与交叉覆盖由契约守护）：期望输出对齐 Prompt v1.4 的
> clarification/answer/plan 分支（explain 属 answer 分支，不生成 Action/Plan），
> 评分升级为 turn_type → intent → target → batch 联合契约，并建立 §3.3 发版门槛。
> 旧 v1.3 评测结果（60 条、Prompt v1.3）已归档至
> `backend/tests/evals/results/archive/agent_commands_results_prompt-v1.3_2026-09-07.json`，
> 不再代表当前契约——数据集/报告/结果三者的一致性由契约测试守护。

## 1. 评测资产

| 资产 | 规模 | 说明 |
|---|---:|---|
| `backend/tests/evals/agent_commands.json` | 240 条（dataset v4，dev split） | 对话命令 → turn_type/intent/target/batch 联合契约；开发/固定回归集 |
| `backend/tests/evals/agent_commands_holdout.json` | 120 条（dataset v4，holdout split） | 盲测集：不进 Prompt 示例、不按单条失败调词；候选冻结后整体运行 |
| 两文件合并 | **360 条** | 分布恰为 §7.2 配额（create 45 / explain 50 / evaluate 45 / revise_script 55 / revise_outline 45 / continue 40 / clarification+范围外 80）；交叉覆盖下限（active context 60 / 目标冲突 30 / 多轮 60 / 口语 50 / 复合 40 / 白名单漂移 40 / 明确集数 80 / 范围外 40）由契约测试守护 |
| `backend/tests/evals/README.md` | 标注规范 | 字段合同、期望语义要点、split 纪律、已知边界、changelog |
| `backend/tests/evals/agent_outcomes.json` | 32 条 | Run 终态 + 证据 → goal_status/后续建议；覆盖 achieved / partially_achieved / blocked、证据充分性、语义约束、非法后续意图、replan 深度上限 |
| `backend/tests/evals/command_scorer.py` | 评分器 | 纯函数：单条评分（先 turn_type 再 intent 再 target 再 batch）→ 聚合（micro/macro、混淆矩阵、失败分类 §4.3）→ §3.3 发版门槛判定 |
| `backend/tests/evals/test_agent_command_eval.py` | harness | CI：数据集契约（v1.4 语义 + 分布/交叉覆盖/preflight 一致性/盲测不进 Prompt）+ preflight 澄清召回（零模型调用）+ 评分器单测；真实：EVAL_SPLIT（dev/holdout/all）× EVAL_REPEATS（发版 3 次），门槛按最差一次判定，`EVAL_REPORT_ONLY=1` 只产报告 |
| `backend/tests/evals/test_agent_outcome_eval.py` | harness | CI：确定性规则全量断言；真实：语义约束 goal_status 一致率 |

## 2. CI 契约评测（已执行，2026-09-18，IR-2 后）

环境：FakeLLM / 无模型，纳入 `make test`（`-m "not performance and not eval_real"`）。

| 指标 | 结果 | 验证方式 |
|---|---|---|
| 数据集规模与分布 | commands 240 + holdout 120 = 360 ✅（dataset v4，合并分布恰为 §7.2 配额）、outcomes ≥30 ✅（32） | 契约测试 |
| 交叉覆盖下限 | ✅ active context 62/60、目标冲突 32/30、多轮 60/60、口语 50/50、复合 40/40、白名单漂移 45/40、明确集数 100/80、范围外 40/40 | 契约测试 |
| preflight 一致性 | ✅ 360 条全部：plan/answer 期望不被 preflight 拦截；pf=1 期望确实被拦截 | 契约测试 |
| 盲测集不进 Prompt | ✅ 120 条 holdout 原文逐字（去空白归一化）不出现在 Planner 模板 | 契约测试 |
| v1.4 期望语义 | ✅ plan 不带 explain；answer 带 intent ∈ {None, explain}；clarification 无 intent/target/batch | 契约测试 |
| 六类 intent + 澄清覆盖 | ✅（explain 经 answer 分支覆盖；continue ≥20，其中白名单漂移 ≥5、复合请求 ≥2） | 契约测试 |
| preflight 澄清召回率（14 条确定性歧义/越界/冲突用例） | **100%**（14/14，零模型调用——"调用即失败" LLM 桩证明） | `TestPreflightEval` |
| 评分器语义 | ✅ answer/explain 记 TP；intent 对但集数错 → episode 门槛失败；continue 对但 batch 错 → batch 门槛失败；应澄清却执行/过度澄清分类正确；写操作最低 precision 门槛生效 | `TestScorerSemantics` |
| 结果文件新鲜度 | ✅（当前无结果文件；若存在则 dataset 版本/条数/Prompt 版本必须与资产一致，防旧结果冒充） | 契约测试 |
| Outcome 确定性规则一致率（26 条） | **100%**（goal_status/剩余约束/建议意图全匹配） | `TestDeterministicOutcomeEval` |

CI 层同时覆盖的恢复/并发契约（详见 `docs/TEST_PLAN.md` §8）：Turn 幂等收据、
并发确认单活跃 Run、lease 接管、checkpoint 恢复、一次后续计划不重复——
`tests/integration/api/test_agent_turns.py`、`test_agent_actions.py`、
`tests/integration/workflow/test_dispatcher_recovery.py`、
`tests/integration/events/test_agent_action_events.py`。

## 3. 真实模型评测

### 3.1 对话命令

**Prompt v1.4 / dataset v4：未执行。** 原因：真实评测需要显式授权费用与可用
Key（`EVAL_LLM_ENABLED=1`）；IR-2 刚完成数据扩充，尚未发起候选版本评测。
执行方式（就绪）：`backend` 目录下 `EVAL_LLM_ENABLED=1 EVAL_REPEATS=3
python -m pytest tests/evals/test_agent_command_eval.py -m eval_real -q`
（默认 EVAL_SPLIT=all 同时评开发集与盲测集；同一配置跑 3 次，报告均值与
最差值，门槛按最差一次判定）；`EVAL_REPORT_ONLY=1` 只产报告。结果文件将
写入 dataset 版本、git commit、Prompt 版本、模型、provider、运行时间、
split 与重复次数。

历史结果（**Prompt v1.3 / 60 条旧语义数据集，仅供趋势对比，不代表 v1.4 契约**，
已归档 `results/archive/agent_commands_results_prompt-v1.3_2026-09-07.json`）：

| intent | precision | recall | F1 | tp/fp/fn |
|---|---|---|---|---|
| create_script | 93.75% | 100% | 96.8% | 15/1/0 |
| explain | 100% | 66.7% | 80.0% | 4/0/2 |
| evaluate | 100% | 87.5% | 93.3% | 7/0/1 |
| revise_script | 100% | 40.0% | 57.1% | 4/0/6 |
| revise_outline | 100% | 20.0% | 33.3% | 1/0/4 |
| **澄清召回率** | **87.5%**（7/8） | | | |

旧结果解读（v1.3 语境，两项 harness 局限已在 IR-1 修正）：
- 旧 harness 把 `answer/explain` 记为失败（要求除 clarification 外一律 plan），
  explain 真实召回被低估；
- 旧数据集没有 continue / target_type / batch_size 标注，目标与批次维度不可测；
- revise_script / revise_outline 召回偏低部分来自 project_context 评测桩，
  v1.4 Prompt 已给修订类请求无上下文时的判定规则。

### 3.2 Outcome 语义评测（未执行）

需真实 Key + 人工标注基准；harness 与配置注入已修复就绪（同 3.1 执行方式）。

## 4. E2E（Agent Workspace）

`e2e/agent-workspace.spec.ts`（FAKE_LLM_SCENARIO=agent_e2e：内容感知 planner 桩），
覆盖：首次创作计划→确认→完成（含刷新恢复）、模糊修改→澄清无 Run、
重复发送不重复消息、第 3 集修订→版本 Diff（TDD anchor）、大纲修订→部分达成→
一次后续计划→再确认（TDD anchor）、重复确认单 Run。
执行记录见 `docs/TEST_REPORT.md` 与 `make e2e REPEAT=5`。
