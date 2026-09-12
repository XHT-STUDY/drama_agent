# Agent 评测报告（J-12）

> 本报告区分两种评测：**CI 契约评测**（FakeLLM / 确定性规则，每次 `make test` 执行）
> 与**真实模型评测**（显式 marker，需真实 API Key）。**真实模型评测不得写入任何模拟数字**——
> 未执行的部分明确标注"未执行"与原因。

## 1. 评测资产

| 资产 | 规模 | 说明 |
|---|---:|---|
| `backend/tests/evals/agent_commands.json` | 60 条 | 对话命令 → 意图/澄清；覆盖五类 intent、中文指代（这里/当前稿）、明确/模糊剧集、active context、冲突约束、越界集数、白名单未开放 |
| `backend/tests/evals/agent_outcomes.json` | 32 条 | Run 终态 + 证据 → goal_status/后续建议；覆盖 achieved / partially_achieved / blocked、证据充分性、语义约束、非法后续意图、replan 深度上限 |
| `backend/tests/evals/test_agent_command_eval.py` | harness | CI：数据集契约 + preflight 澄清召回（零模型调用）；真实：全量 P/R/F1 |
| `backend/tests/evals/test_agent_outcome_eval.py` | harness | CI：确定性规则全量断言；真实：语义约束 goal_status 一致率 |

## 2. CI 契约评测（已执行，2026-08-23）

环境：FakeLLM / 无模型，纳入 `make test`（`-m "not performance and not eval_real"`）。

| 指标 | 结果 | 验证方式 |
|---|---|---|
| 数据集规模契约 | commands ≥50 ✅（55）、outcomes ≥30 ✅（32） | 契约测试 |
| 五类 intent 覆盖 | ✅ 全覆盖 | 契约测试 |
| preflight 澄清召回率（12 条确定性歧义/越界/冲突用例） | **100%**（12/12，零模型调用——"调用即失败" LLM 桩证明） | `TestPreflightEval` |
| Outcome 确定性规则一致率（26 条） | **100%**（goal_status/剩余约束/建议意图全匹配） | `TestDeterministicOutcomeEval` |
| 白名单外建议拦截 | ✅ explain 等只读意图不可能出现在确定性建议 | 同上 |
| 深度上限 | replan_depth=1 不再生成子提案（集成层由 J-09 事件测试覆盖） | 数据集 + 事件测试 |

CI 层同时覆盖的恢复/并发契约（详见 `docs/TEST_PLAN.md` §8）：Turn 幂等收据、
并发确认单活跃 Run、lease 接管、checkpoint 恢复、一次后续计划不重复——
`tests/integration/api/test_agent_turns.py`、`test_agent_actions.py`、
`tests/integration/workflow/test_dispatcher_recovery.py`、
`tests/integration/events/test_agent_action_events.py`。

## 3. 真实模型评测

### 3.1 对话命令（已执行，2026-09-07）——首次真实模型评测

环境：`EVAL_LLM_ENABLED=1`，provider `openai_compatible`，model `deepseek-v4-pro-0813`，
Planner Prompt **v1.3.0**（本次评测同时验证了 v1.3 输出纪律 + planner max_tokens 4096
的有效性：60/60 用例全部产出可解析输出，**0 例 INVALID_OUTPUT / 截断失败**——
修复前真实使用中 Planner 曾因 completion 被推理 token 挤爆而三连失败）。

> 执行方式备注：pytest 全局 `APP_ENV=test` 会使 Settings 跳过 `.env` 源，
> harness 已改为显式 init kwargs 注入真实配置（见 test_agent_command_eval.py）。

| intent | precision | recall | F1 | tp/fp/fn |
|---|---|---|---|---|
| create_script | 93.75% | 100% | 96.8% | 15/1/0 |
| explain | 100% | 66.7% | 80.0% | 4/0/2 |
| evaluate | 100% | 87.5% | 93.3% | 7/0/1 |
| revise_script | 100% | 40.0% | 57.1% | 4/0/6 |
| revise_outline | 100% | 20.0% | 33.3% | 1/0/4 |
| **澄清召回率** | **87.5%**（7/8） | | | |

失败分类：**0 例调用/解析失败**；未命中均为"应出计划却澄清/改判"（recall 损失）。

解读与后续：
- create_script（主路径）F1 96.8%，可用；precision 全线 ≥93.75%，误判计划罕见。
- revise_script / revise_outline 召回偏低：harness 的 project_context 为评测桩
  （"项目上下文略"），修订类请求缺乏可引用上下文时模型倾向保守澄清——
  属 harness 局限与 prompt 调优空间，不是路由正确性问题（P=100%）。
  后续调优方向：v1.4 prompt 给"修订类请求在无上下文时的判定规则"。
- 澄清召回 87.5%（1 例漏澄清），"unknown" 1 例 FP。
- goal_status 一致率 / 后续计划可接受率（outcome 语义用例）：未执行，
  需人工标注基准；harness 已就绪（test_agent_outcome_eval.py）。

### 3.2 Outcome 语义评测（未执行）

需真实 Key + 人工标注基准；harness 与配置注入已修复就绪（同 3.1 执行方式）。

## 4. E2E（Agent Workspace）

`e2e/agent-workspace.spec.ts`（FAKE_LLM_SCENARIO=agent_e2e：内容感知 planner 桩），
覆盖：首次创作计划→确认→完成（含刷新恢复）、模糊修改→澄清无 Run、
重复发送不重复消息、第 3 集修订→版本 Diff（TDD anchor）、大纲修订→部分达成→
一次后续计划→再确认（TDD anchor）、重复确认单 Run。
执行记录见 `docs/TEST_REPORT.md` 与 `make e2e REPEAT=5`。
