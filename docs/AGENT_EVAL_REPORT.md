# Agent 评测报告（J-12）

> 本报告区分两种评测：**CI 契约评测**（FakeLLM / 确定性规则，每次 `make test` 执行）
> 与**真实模型评测**（显式 marker，需真实 API Key）。**真实模型评测不得写入任何模拟数字**——
> 未执行的部分明确标注"未执行"与原因。

## 1. 评测资产

| 资产 | 规模 | 说明 |
|---|---:|---|
| `backend/tests/evals/agent_commands.json` | 55 条 | 对话命令 → 意图/澄清；覆盖五类 intent、中文指代（这里/当前稿）、明确/模糊剧集、active context、冲突约束、越界集数、白名单未开放 |
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

## 3. 真实模型评测（**未执行**）

**状态：未执行——当前环境未配置真实模型 API Key（`EVAL_LLM_ENABLED=1` 与
`LLM_*` 凭证缺失）。以下为执行方式与报告模板；执行后由 harness 自动写入
`backend/tests/evals/results/*.json`，再人工把结果誊入本节，任何字段不得手填估计值。**

执行命令：

```bash
cd backend
EVAL_LLM_ENABLED=1 LLM_API_KEY=<key> LLM_BASE_URL=<url> LLM_PLANNER_MODEL=<model> \
  uv run pytest -m eval_real --no-header -v
```

产出与指标（harness 落盘 `tests/evals/results/agent_commands_results.json` /
`agent_outcomes_results.json`）：

| 指标 | 来源 | 结果 |
|---|---|---|
| provider / model / prompt version | harness 记录 | 待执行 |
| 各 intent precision / recall / F1 | commands 全量 55 条 | 待执行 |
| 澄清召回率 | clarification 用例 | 待执行 |
| goal_status 人工一致率 | outcomes 语义约束用例（人工抽检标注基准） | 待执行 |
| 后续计划可接受率 | 人工评审 recommended_next_action | 待执行 |
| 平均 tokens / P50 / P95 延迟 / 失败分类 | harness 失败列表 + 调用统计 | 待执行 |

> 目标准确率、目标准确率/澄清召回率的人工一致率需要人工标注样本；
> 后续计划可接受率按"建议意图 + 目标集与人工期望一致"判定。

## 4. E2E（Agent Workspace）

`e2e/agent-workspace.spec.ts`（FAKE_LLM_SCENARIO=agent_e2e：内容感知 planner 桩），
覆盖：首次创作计划→确认→完成（含刷新恢复）、模糊修改→澄清无 Run、
重复发送不重复消息、第 3 集修订→版本 Diff（TDD anchor）、大纲修订→部分达成→
一次后续计划→再确认（TDD anchor）、重复确认单 Run。
执行记录见 `docs/TEST_REPORT.md` 与 `make e2e REPEAT=5`。
