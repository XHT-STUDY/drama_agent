"""对话命令评测 harness（J-12 / IR-1 重写）。

三种模式：
- CI（默认）：数据集契约（v1.4 语义 + §4.1 标注合同）+ 确定性 preflight
  子集（"调用即失败"的 LLM 桩证明零模型调用）+ 评分器纯函数单测
  （伪造结果验证 target/batch 指标与失败分类）；
- 真实模型（pytest -m eval_real，需 EVAL_LLM_ENABLED=1 + 真实 Key）：
  全量用例跑 AgentCommandPlannerSkill，按 command_scorer 计算联合指标
  （turn_type → intent → target → batch），写入 tests/evals/results/；
  默认执行 §3.3 发版门槛（任一不达标即失败），EVAL_REPORT_ONLY=1
  只产报告不设门（开发期）。

评分逻辑在 command_scorer.py 唯一定义，本文件只做编排。
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from app.agents.base import BaseAgent
from app.domain.agent_planner import AgentPlannerInput, AgentPlannerOutput
from app.llm.fake import FakeLLM
from app.llm.models import LLMCallResult
from app.prompts.loader import PromptLoader
from app.skills.agent_command_planner import (
    DEFAULT_AVAILABLE_INTENTS,
    AgentCommandPlannerSkill,
)
from tests.evals.command_scorer import (
    FAILURE_BATCH,
    FAILURE_EPISODE,
    FAILURE_OVER_CLARIFY,
    FAILURE_SHOULD_CLARIFY,
    WRITE_INTENTS,
    aggregate,
    classify_exception,
    evaluate_gates,
    release_blocking_gates,
    score_case,
)

EVAL_DIR = Path(__file__).parent
DATASET = EVAL_DIR / "agent_commands.json"
HOLDOUT_DATASET = EVAL_DIR / "agent_commands_holdout.json"
RESULTS_DIR = EVAL_DIR / "results"
RESULTS_FILE = RESULTS_DIR / "agent_commands_results.json"

# 词表唯一来源在 command_scorer.WRITE_INTENTS；explain 是只读 intent
ALL_INTENTS = WRITE_INTENTS + ("explain",)
EXPECTED_KEYS = {"turn_type", "intent", "target_type", "episode_number", "batch_size"}
COVERAGE_VOCAB = {
    "holdout",
    "create",
    "staged_create",
    "content_explain",
    "project_status",
    "explicit_episode",
    "active_context",
    "context_conflict",
    "continue",
    "whitelist_drift",
    "compound_request",
    "multi_target",
    "conflict_constraint",
    "out_of_range",
    "no_context_reference",
    "out_of_scope",
    "colloquial",
    "outline_edit",
    "project_scope_eval",
    "ambiguous_target",
    "multi_turn",
}
RISK_VOCAB = {"write_op", "read_only", "out_of_scope", "safety"}
DATASET_VERSION = 5
# §7.2 合并分布（开发集 + 盲测集，恰好 360）
DISTRIBUTION_QUOTA = {
    "create_script": 45,
    "explain": 50,
    "evaluate": 45,
    "revise_script": 55,
    "revise_outline": 45,
    "continue": 40,
    "clarification": 80,
}
# §7.2 交叉覆盖下限（合并计；ac/wl 维度按字段语义统计而非标签）
CROSS_FLOORS = {
    "active_context": 60,  # active_context 字段非空
    "context_conflict": 30,
    "multi_turn": 60,
    "colloquial": 50,
    "compound_request": 40,
    "whitelist_vary": 40,  # available_intents != 生产默认白名单
    "explicit_episode": 80,
    "out_of_scope": 40,
}
DEFAULT_WL_SET = set(DEFAULT_AVAILABLE_INTENTS)


def load_dataset() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(DATASET.read_text(encoding="utf-8")))


def load_holdout_dataset() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(HOLDOUT_DATASET.read_text(encoding="utf-8")))


def load_cases() -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], load_dataset()["cases"])


def load_all_cases() -> list[dict[str, Any]]:
    """开发集 + 盲测集（交叉覆盖与分布契约按合并口径检查）。"""
    return load_cases() + cast(list[dict[str, Any]], load_holdout_dataset()["cases"])


class ExplodeOnCallLLM(FakeLLM):
    """任何 generate_structured 调用直接失败——证明 preflight 零模型调用。"""

    async def generate_structured(
        self, schema: Any, messages: list[dict[str, str]], **kwargs: Any
    ) -> LLMCallResult:
        raise AssertionError("preflight 确定性用例不应调用 LLM")


def planner_input(case: dict[str, Any]) -> AgentPlannerInput:
    # 多轮用例：recent_dialog 以生产 project_context 的"最近消息"格式注入
    dialog = case.get("recent_dialog")
    project_context = f"（评测桩）最近消息:\n{dialog}" if dialog else "（评测桩：项目上下文略）"
    return AgentPlannerInput(
        user_request=case["user_request"],
        project_title="评测项目",
        target_episode_count=case.get("target_episode_count", 10),
        # 兜底白名单与生产默认一致（无门上 Run 的项目状态）
        available_intents=case.get("available_intents") or list(DEFAULT_AVAILABLE_INTENTS),
        active_context=case.get("active_context"),
        project_context=project_context,
        unresolved_turn_count=case.get("unresolved_turn_count", 0),
    )


def asyncio_run(coro: Any) -> Any:
    """同步测试内执行协程（评测逐条串行，无需事件循环复用）。"""
    import asyncio

    return asyncio.run(coro)


# ========================================================================
# 数据集契约（CI）——v1.4 期望输出语义 + §4.1 标注合同
# ========================================================================


@pytest.mark.contract
class TestCommandDatasetContract:
    def test_dataset_version_and_split_sizes(self) -> None:
        dev, holdout = load_dataset(), load_holdout_dataset()
        assert dev["version"] == DATASET_VERSION
        assert holdout["version"] == DATASET_VERSION
        assert len(dev["cases"]) == 240, "开发/固定回归集 240 条（§7.2）"
        assert len(holdout["cases"]) == 120, "盲测集 120 条（§7.2）"
        assert all(c["split"] == "dev" for c in dev["cases"])
        assert all(c["split"] == "holdout" for c in holdout["cases"])

    def test_case_ids_unique_and_required_keys_present(self) -> None:
        cases = load_all_cases()
        ids = [c["id"] for c in cases]
        assert len(ids) == len(set(ids)) == 360, "两文件合计恰好 360 且 ID 全局唯一（§7.6）"
        for c in cases:
            assert {
                "id",
                "split",
                "user_request",
                "active_context",
                "available_intents",
                "target_episode_count",
                "expected",
                "preflight_deterministic",
                "risk",
                "coverage",
                "note",
            } <= set(c)
            assert set(c["expected"]) <= EXPECTED_KEYS
            assert c["risk"] in RISK_VOCAB
            assert set(c["coverage"]) <= COVERAGE_VOCAB
            if "recent_dialog" in c:
                assert c["recent_dialog"].startswith("["), "多轮对话用 [seq] role: 格式"

    def test_expected_semantics_follow_v14_contract(self) -> None:
        """期望字段与 Prompt v1.4 分支语义一致，不再训练旧契约。"""
        for c in load_all_cases():
            exp = c["expected"]
            turn, intent = exp["turn_type"], exp["intent"]
            assert turn in {"clarification", "answer", "plan"}
            if turn == "plan":
                assert intent in ALL_INTENTS
                assert intent != "explain", "explain 不生成 Action/Plan（v1.4）"
            elif turn == "answer":
                # 项目状态类不带 intent；正文/设定/大纲解释带 explain
                assert intent in {None, "explain"}
            else:
                assert intent is None, "clarification 不得携带 intent"
                assert exp["target_type"] is None and exp["episode_number"] is None
                assert exp["batch_size"] is None

    def test_field_dependencies_are_legal(self) -> None:
        for c in load_all_cases():
            exp = c["expected"]
            if exp["episode_number"] is not None:
                # evaluate 只标集数（服务端只消费 episode，target_type 不约束）；
                # revise_script 的集数目标必须是 script；explain 读单集评估
                # 时可以是 evaluation，其余解释目标是 script
                assert exp["intent"] in {"evaluate", "revise_script", "explain"}
                if exp["intent"] == "revise_script":
                    assert exp["target_type"] == "script", "集数目标只对 script 合法"
                elif exp["intent"] == "explain":
                    assert exp["target_type"] in {"script", "evaluation"}
            if exp["batch_size"] is not None:
                assert (exp["turn_type"], exp["intent"]) == ("plan", "continue")
            if c.get("active_context") and "context_conflict" not in c["coverage"]:
                # 上下文用例的期望目标必须与上下文一致（冲突用例除外）
                ac_ep = c["active_context"].get("episode_number")
                if "explicit_episode" not in c["coverage"] and exp["episode_number"]:
                    assert exp["episode_number"] == ac_ep

    def test_combined_distribution_matches_quota(self) -> None:
        """合并分布恰好等于 §7.2 配额（45/50/45/55/45/40/80）。"""
        counts: dict[str, int] = {}
        for c in load_all_cases():
            exp = c["expected"]
            if exp["turn_type"] == "plan":
                label = exp["intent"]
            elif exp["turn_type"] == "answer":
                label = "explain"
            else:
                label = "clarification"
            counts[label] = counts.get(label, 0) + 1
        assert counts == DISTRIBUTION_QUOTA, counts

    def test_cross_coverage_floors(self) -> None:
        """§7.2 交叉覆盖下限（合并口径；同一 case 可计入多个维度）。"""
        counts: dict[str, int] = dict.fromkeys(CROSS_FLOORS, 0)
        for c in load_all_cases():
            cov = set(c["coverage"])
            for key in CROSS_FLOORS:
                if key == "active_context":
                    if c.get("active_context") is not None:
                        counts[key] += 1
                elif key == "whitelist_vary":
                    if set(c["available_intents"]) != DEFAULT_WL_SET:
                        counts[key] += 1
                elif key in cov:
                    counts[key] += 1
        short = {k: (v, CROSS_FLOORS[k]) for k, v in counts.items() if v < CROSS_FLOORS[k]}
        assert not short, f"交叉覆盖不足: {short}"

    def test_holdout_not_exposed_in_prompt_template(self) -> None:
        """盲测集原文不得逐字出现在 Planner Prompt 模板中（§7.6）。

        逐字/去空白归一化匹配是确定性部分；"近似重复"由标注纪律
        （tests/evals/README.md）人工复核保障。
        """
        template = (
            PromptLoader()
            .get("agent_command_planner")
            .render(
                user_request="",
                project_title="",
                target_episode_count="",
                available_intents="[]",
                active_context="null",
                project_context="",
                unresolved_turn_count="0",
                external_tools="",
            )
        )
        normalized_template = "".join(template.split())
        for c in load_holdout_dataset()["cases"]:
            normalized_request = "".join(c["user_request"].split())
            assert normalized_request not in normalized_template, f"盲测用例 {c['id']} 出现在 Prompt 模板中"

    def test_preflight_consistency(self) -> None:
        """期望 plan/answer 的用例不得被确定性 preflight 拦截；
        pf=1 用例必须被拦截且输出澄清——标注与生产行为一致。"""

        from app.skills.agent_command_planner import _preflight_clarification

        for c in load_all_cases():
            output = _preflight_clarification(planner_input(c))
            fired = output is not None
            label = f"{c['id']}: {c['user_request'][:24]}"
            if c["expected"]["turn_type"] in ("plan", "answer"):
                assert not fired, f"preflight 会拦截 plan/answer 用例 → {label}"
            if c["preflight_deterministic"]:
                assert fired and output is not None and output.turn_type == "clarification", (
                    f"pf=1 用例未被 preflight 拦截 → {label}"
                )

    def test_covers_all_intents_and_clarification(self) -> None:
        cases = load_all_cases()
        plan_intents = {c["expected"]["intent"] for c in cases if c["expected"]["turn_type"] == "plan"}
        answer_intents = {c["expected"]["intent"] for c in cases if c["expected"]["turn_type"] == "answer"}
        assert set(WRITE_INTENTS) <= plan_intents, "五类写操作 intent 都必须有 plan 样本"
        assert "explain" in answer_intents, "explain 属于 answer 分支（v1.4）"
        assert any(c["expected"]["turn_type"] == "clarification" for c in cases)

    def test_continue_base_set_at_least_20(self) -> None:
        continue_cases = [c for c in load_cases() if "continue" in c["coverage"]]
        assert len(continue_cases) >= 20
        assert sum(1 for c in continue_cases if c["expected"]["turn_type"] == "plan") >= 12
        assert sum(1 for c in continue_cases if "whitelist_drift" in c["coverage"]) >= 5, (
            "白名单无 continue 必须覆盖"
        )
        assert sum(1 for c in continue_cases if "compound_request" in c["coverage"]) >= 2

    def test_covers_preflight_active_context_and_conflict(self) -> None:
        cases = load_cases()
        preflight = [
            c for c in cases if c["expected"]["turn_type"] == "clarification" and c["preflight_deterministic"]
        ]
        assert len(preflight) >= 10
        assert sum(1 for c in cases if c.get("active_context")) >= 5
        assert sum(1 for c in cases if "context_conflict" in c["coverage"]) >= 1

    def test_saved_results_cannot_masquerade_as_current(self) -> None:
        """结果文件若存在，其 dataset/prompt/条数必须与当前资产一致。

        防止 v1.3 旧结果冒充 v1.4 评测（P0-5 漂移）。历史结果归档在
        results/archive/，不参与本检查。契约逻辑本身由
        TestResultsFreshnessContract 用伪造文件验证（真实文件缺席时
        契约不会静默失明）。
        """
        if not RESULTS_FILE.exists():
            pytest.skip("尚无真实模型结果文件（真实评测未执行）")
        check_results_freshness(RESULTS_FILE)


def check_results_freshness(results_file: Path) -> None:
    """断言结果文件的 dataset 版本 / 条数 / Prompt 版本与当前资产一致。"""
    report = cast(dict[str, Any], json.loads(results_file.read_text(encoding="utf-8")))
    data = load_dataset()
    prompt_version = PromptLoader().get("agent_command_planner").version
    assert report["dataset_version"] == data["version"], "结果文件是旧数据集"
    assert report["total"] == len(data["cases"]), "结果条数与数据集不一致"
    assert report["prompt_version"] == prompt_version, "结果文件是旧 Prompt"


@pytest.mark.unit
class TestResultsFreshnessContract:
    """新鲜度契约逻辑本身必须可红：伪造旧数据集/旧 Prompt 的结果文件。"""

    @staticmethod
    def _write_report(path: Path, **overrides: Any) -> None:
        data = load_dataset()
        report: dict[str, Any] = {
            "dataset_version": data["version"],
            "total": len(data["cases"]),
            "prompt_version": PromptLoader().get("agent_command_planner").version,
        }
        report.update(overrides)
        path.write_text(json.dumps(report), encoding="utf-8")

    def test_current_report_passes(self, tmp_path: Path) -> None:
        report_file = tmp_path / "results.json"
        self._write_report(report_file)
        check_results_freshness(report_file)  # 不抛即通过

    def test_old_dataset_version_is_rejected(self, tmp_path: Path) -> None:
        report_file = tmp_path / "results.json"
        self._write_report(report_file, dataset_version=data_version_minus_one())
        with pytest.raises(AssertionError, match="旧数据集"):
            check_results_freshness(report_file)

    def test_wrong_case_count_is_rejected(self, tmp_path: Path) -> None:
        report_file = tmp_path / "results.json"
        self._write_report(report_file, total=60)
        with pytest.raises(AssertionError, match="条数"):
            check_results_freshness(report_file)

    def test_old_prompt_version_is_rejected(self, tmp_path: Path) -> None:
        report_file = tmp_path / "results.json"
        self._write_report(report_file, prompt_version="0.0.1")
        with pytest.raises(AssertionError, match="旧 Prompt"):
            check_results_freshness(report_file)


def data_version_minus_one() -> int:
    return int(load_dataset()["version"]) - 1


# ========================================================================
# 确定性 preflight 评测（CI，零模型调用）
# ========================================================================


@pytest.mark.unit
class TestPreflightEval:
    def test_preflight_clarification_recall_is_100_percent(self) -> None:
        """全部 preflight 确定性用例都在调用模型前澄清（explode 桩证明）。"""
        skill = AgentCommandPlannerSkill()
        agent = BaseAgent(name="planner", llm=ExplodeOnCallLLM(seed=42))
        loader = PromptLoader()

        hits = 0
        total = 0
        failures: list[str] = []
        for case in load_cases():
            if not case["preflight_deterministic"]:
                continue
            total += 1
            output = cast(
                AgentPlannerOutput,
                asyncio_run(
                    skill.execute(
                        {
                            "input": planner_input(case),
                            "agent": agent,
                            "prompt_loader": loader,
                        }
                    )
                ),
            )
            if output.turn_type == "clarification":
                hits += 1
            else:
                failures.append(f"{case['id']}: {case['user_request'][:30]} → {output.turn_type}")

        assert total >= 10
        recall = hits / total
        assert recall == 1.0, f"preflight 澄清召回率 {recall:.2%}，失败: {failures}"


# ========================================================================
# 评分器单测（CI，伪造结果验证指标与门槛——IR-1 验收）
# ========================================================================


def _case(expected: dict[str, Any], **extra: Any) -> dict[str, Any]:
    base = {
        "id": "fake-001",
        "split": "dev",
        "user_request": "fake",
        "active_context": None,
        "available_intents": list(ALL_INTENTS),
        "target_episode_count": 10,
        "expected": {k: expected.get(k) for k in EXPECTED_KEYS},
        "preflight_deterministic": False,
        "risk": "write_op",
        "coverage": expected.get("_coverage", []),
        "note": "伪造评分用例",
    }
    base.update(extra)
    return base


def _output(
    turn_type: str = "plan",
    intent: str | None = None,
    target_type: str | None = None,
    episode: int | None = None,
    batch: int | None = None,
) -> AgentPlannerOutput:
    from app.domain.agent_planner import PlannerTarget

    return AgentPlannerOutput(
        turn_type=turn_type,  # type: ignore[arg-type]
        intent=intent,
        target=PlannerTarget(target_type=target_type, episode_number=episode)  # type: ignore[arg-type]
        if target_type or episode
        else None,
        batch_size=batch,
    )


@pytest.mark.unit
class TestScorerSemantics:
    def test_answer_explain_counted_as_tp_not_fn(self) -> None:
        """v1.4 验收：answer/explain 正确输出被记 TP，不再当 FN。"""
        case = _case(
            {
                "turn_type": "answer",
                "intent": "explain",
                "target_type": "script",
                "episode_number": 3,
                "_coverage": ["content_explain", "explicit_episode"],
            }
        )
        score = score_case(case, _output("answer", "explain", "script", 3))
        assert score.ok
        metrics = aggregate([score])
        assert metrics["intent"]["per_intent"]["explain"]["tp"] == 1
        assert metrics["intent"]["per_intent"]["explain"]["fn"] == 0
        assert metrics["turn_type_accuracy"] == 1.0

    def test_project_status_answer_must_not_carry_intent(self) -> None:
        """项目状态类 answer 带上 explain 会改变服务端读取路径，必须判错。"""
        case = _case({"turn_type": "answer", "intent": None})
        score = score_case(case, _output("answer", "explain"))
        assert not score.ok
        assert score.intent_ok is False

    def test_intent_correct_but_episode_wrong_fails_target_gate(self) -> None:
        """伪造 intent 正确但集数错误 → target 指标失败（IR-1 验收）。"""
        case = _case(
            {
                "turn_type": "plan",
                "intent": "revise_script",
                "target_type": "script",
                "episode_number": 3,
                "_coverage": ["explicit_episode"],
            }
        )
        score = score_case(case, _output("plan", "revise_script", "script", 2))
        assert score.turn_type_ok and score.intent_ok
        assert score.episode_ok is False
        assert score.failure_class == FAILURE_EPISODE
        metrics = aggregate([score])
        assert metrics["target"]["episode_accuracy_explicit"] == 0.0
        gates = {g["gate"]: g for g in evaluate_gates(metrics)}
        assert gates["target.episode_accuracy_explicit"]["status"] == "fail"

    def test_continue_correct_but_batch_wrong_fails_batch_gate(self) -> None:
        """伪造 continue 正确但 batch_size 错误 → batch 指标失败（IR-1 验收）。"""
        case = _case({"turn_type": "plan", "intent": "continue", "target_type": "project", "batch_size": 5})
        score = score_case(case, _output("plan", "continue", "project", batch=1))
        assert score.intent_ok and score.target_type_ok
        assert score.batch_ok is False
        assert score.failure_class == FAILURE_BATCH
        metrics = aggregate([score])
        assert metrics["continue"]["batch_accuracy"] == 0.0
        gates = {g["gate"]: g for g in evaluate_gates(metrics)}
        assert gates["continue.batch_accuracy"]["status"] == "fail"

    def test_batch_null_equals_write_all(self) -> None:
        case = _case(
            {"turn_type": "plan", "intent": "continue", "target_type": "project", "batch_size": None}
        )
        assert score_case(case, _output("plan", "continue", "project")).ok

    def test_should_clarify_executed_vs_over_clarification(self) -> None:
        should_clarify = _case({"turn_type": "clarification"})
        executed = score_case(should_clarify, _output("plan", "create_script", "project"))
        assert executed.failure_class == FAILURE_SHOULD_CLARIFY

        should_plan = _case({"turn_type": "plan", "intent": "create_script"})
        clarified = score_case(should_plan, _output("clarification"))
        assert clarified.failure_class == FAILURE_OVER_CLARIFY

    def test_expected_clarification_answered_is_turn_type_error(self) -> None:
        """answer 不产生执行——"应澄清却答了"是 turn_type 错误，不是类 9。"""
        should_clarify = _case({"turn_type": "clarification"})
        answered = score_case(should_clarify, _output("answer", None))
        assert answered.failure_class == "turn_type_error"

    def test_release_mode_blocks_on_no_data_gates(self) -> None:
        """§3.3 门槛是发布合同：缺数据（no_data）同样阻断发版。"""
        scores = [
            score_case(
                _case({"turn_type": "plan", "intent": "create_script"}, id=f"a-{i}"),
                _output("plan", "create_script", "project"),
            )
            for i in range(20)
        ]
        metrics = aggregate(scores)
        gates = {g["gate"]: g for g in evaluate_gates(metrics)}
        assert gates["continue.precision"]["status"] == "no_data"
        blocking = release_blocking_gates(evaluate_gates(metrics))
        assert any(g["gate"] == "continue.precision" for g in blocking)

    def test_clarification_precision_and_recall(self) -> None:
        cases = [_case({"turn_type": "clarification"}, id=f"c-{i}") for i in range(4)]
        cases.append(_case({"turn_type": "plan", "intent": "evaluate"}, id="p-1"))
        scores = [
            score_case(cases[0], _output("clarification")),  # TP
            score_case(cases[1], _output("clarification")),  # TP
            score_case(cases[2], _output("plan", "evaluate")),  # 漏澄清
            score_case(cases[3], _output("clarification")),  # TP
            score_case(cases[4], _output("clarification")),  # 过度澄清
        ]
        metrics = aggregate(scores)
        assert metrics["clarification"]["recall"] == 0.75
        assert metrics["clarification"]["precision"] == 0.75

    def test_write_intent_precision_min_gate(self) -> None:
        """写操作 precision 门槛按六类写意图中的最低值判定。"""
        scores = []
        for i in range(10):
            scores.append(
                score_case(
                    _case({"turn_type": "plan", "intent": "create_script"}, id=f"a-{i}"),
                    _output("plan", "create_script", "project"),
                )
            )
        # 一条 evaluate 预测错成 create_script → create precision 10/11 < 0.98
        scores.append(
            score_case(
                _case({"turn_type": "plan", "intent": "evaluate"}, id="b-0"),
                _output("plan", "create_script", "project"),
            )
        )
        metrics = aggregate(scores)
        assert metrics["write_intent"]["precision_min"] < 0.98
        gates = {g["gate"]: g for g in evaluate_gates(metrics)}
        assert gates["write_intent.precision_min"]["status"] == "fail"

    def test_error_classification_by_exception(self) -> None:
        case = _case({"turn_type": "plan", "intent": "create_script"})
        assert classify_exception(RuntimeError("LLM_OUTPUT_TRUNCATED")) == "output_truncated"
        assert classify_exception(RuntimeError("llm_timeout")) == "provider_error"
        assert classify_exception(ValueError("Planner 输出无效: invalid_output")) == "invalid_output"
        score = score_case(case, None, error=RuntimeError("llm_rate_limited"))
        assert score.failure_class == "provider_error"
        metrics = aggregate([score])
        assert metrics["parse_rate"] == 0.0

    def test_confusion_matrices_are_counted(self) -> None:
        scores = [
            score_case(
                _case({"turn_type": "plan", "intent": "evaluate"}, id="1"), _output("answer", "explain")
            ),
            score_case(
                _case({"turn_type": "plan", "intent": "evaluate"}, id="2"), _output("plan", "evaluate")
            ),
        ]
        metrics = aggregate(scores)
        assert metrics["turn_type_confusion"]["plan->answer"] == 1
        assert metrics["intent"]["confusion"]["evaluate->evaluate"] == 1

    def test_context_target_accuracy_uses_active_context_cases(self) -> None:
        active = {
            "artifact_id": "00000000-0000-0000-0000-000000000001",
            "artifact_type": "script_draft",
            "episode_number": 3,
            "version": 2,
        }
        case = _case(
            {
                "turn_type": "plan",
                "intent": "revise_script",
                "target_type": "script",
                "episode_number": 3,
                "_coverage": ["active_context"],
            },
            active_context=active,
        )
        ok = score_case(case, _output("plan", "revise_script", "script", 3))
        wrong = score_case(case, _output("plan", "revise_script", "script", 5))
        metrics = aggregate([ok, wrong])
        assert metrics["target"]["context_target_accuracy"] == 0.5
        assert metrics["target"]["episode_accuracy_context"] == 0.5

    def test_release_not_blocked_when_all_gates_pass(self) -> None:
        """全量主标签 + 目标/上下文标注都有足量正确样本时无阻断项。"""
        scores = []
        # 覆盖六类写 intent + explain + clarification，全对 → 指标全 1.0
        plan_specs = [
            ("create_script", "project"),
            ("evaluate", None),
            ("revise_script", "script"),
            ("revise_outline", "outline"),
            ("continue", "project"),
        ]
        for intent, tt in plan_specs:
            for i in range(5):
                case = _case(
                    {
                        "turn_type": "plan",
                        "intent": intent,
                        "target_type": tt,
                        "batch_size": (2 if intent == "continue" else None),
                    },
                    id=f"{intent}-{i}",
                )
                scores.append(
                    score_case(case, _output("plan", intent, tt, batch=2 if intent == "continue" else None))
                )
        # 明确集数（explicit 桶）与活动上下文（context 桶）标注
        for i in range(5):
            scores.append(
                score_case(
                    _case(
                        {
                            "turn_type": "answer",
                            "intent": "explain",
                            "target_type": "script",
                            "episode_number": 1,
                            "_coverage": ["content_explain", "explicit_episode"],
                        },
                        id=f"expl-{i}",
                    ),
                    _output("answer", "explain", "script", 1),
                )
            )
        active = {
            "artifact_id": "00000000-0000-0000-0000-000000000001",
            "artifact_type": "script_draft",
            "episode_number": 3,
            "version": 2,
        }
        for i in range(5):
            scores.append(
                score_case(
                    _case(
                        {
                            "turn_type": "plan",
                            "intent": "revise_script",
                            "target_type": "script",
                            "episode_number": 3,
                            "_coverage": ["active_context"],
                        },
                        id=f"ctx-{i}",
                        active_context=active,
                    ),
                    _output("plan", "revise_script", "script", 3),
                )
            )
        for i in range(5):
            scores.append(
                score_case(_case({"turn_type": "clarification"}, id=f"clar-{i}"), _output("clarification"))
            )
        metrics = aggregate(scores)
        assert release_blocking_gates(evaluate_gates(metrics)) == []


# ========================================================================
# 真实模型评测（pytest -m eval_real；EVAL_LLM_ENABLED=1 才执行）
# ========================================================================

REAL_EVAL_ENABLED = os.environ.get("EVAL_LLM_ENABLED") == "1"
REPORT_ONLY = os.environ.get("EVAL_REPORT_ONLY") == "1"
# dev=仅开发集；holdout=仅盲测集；all=两者（发版评测要求 all）
EVAL_SPLIT = os.environ.get("EVAL_SPLIT", "all")
# 同一候选配置重复次数：报告均值与最差值，门槛按最差一次判定（§7.6）
EVAL_REPEATS = int(os.environ.get("EVAL_REPEATS", "1"))


def _select_cases(split: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    dev, holdout = load_dataset(), load_holdout_dataset()
    by_split: dict[str, list[dict[str, Any]]] = {
        "dev": dev["cases"],
        "holdout": holdout["cases"],
    }
    if split == "all":
        cases = by_split["dev"] + by_split["holdout"]
    elif split in by_split:
        cases = by_split[split]
    else:
        raise ValueError(f"EVAL_SPLIT 非法: {split}（dev|holdout|all）")
    counts = {name: len(c) for name, c in by_split.items()}
    return cases, counts


@pytest.mark.eval_real
@pytest.mark.skipif(not REAL_EVAL_ENABLED, reason="需要 EVAL_LLM_ENABLED=1 与真实模型 Key")
class TestRealModelCommandEval:
    def test_run_full_dataset_and_report_metrics(self) -> None:
        """选定 split × repeats → 联合指标 + §3.3 门槛 → 结果落盘 results/。

        - EVAL_SPLIT：dev / holdout / all（默认 all，发版要求两集齐评）；
        - EVAL_REPEATS：同一配置重复次数（发版评测 3 次），报告每次
          明细与均值/最差值，门槛按最差一次判定（不以最好一次发布）；
        - 默认发版模式：任一门槛 fail/no_data 即测试失败（非零退出）；
          EVAL_REPORT_ONLY=1 开发期只产报告。
        """
        from dotenv import dotenv_values

        from app.core.config import Settings
        from app.llm.openai_compatible import OpenAICompatibleLLM

        # pytest 全局设 APP_ENV=test → Settings 跳过 .env 源且强制 fake
        # provider（防泄漏设计）。真实评测必须以 init kwargs 显式注入真实
        # 配置（init 优先级最高，不受 conftest 环境影响）。
        repo_root = Path(__file__).resolve().parents[3]
        real: dict[str, Any] = {
            key.lower(): value
            for key, value in dotenv_values(repo_root / ".env").items()
            if value and key.startswith(("LLM_", "RUN_"))
        }
        # **real 的键来自 .env 的 LLM_*/RUN_* 前缀，值类型由 Settings 校验；
        # dict[str, Any] 展开无法静态穷举 kwargs，此处显式放弃逐键检查
        settings = Settings(app_env="local", **cast(Any, real))
        agent = BaseAgent(name="planner", llm=OpenAICompatibleLLM(settings))
        skill = AgentCommandPlannerSkill()
        loader = PromptLoader()

        cases, split_counts = _select_cases(EVAL_SPLIT)
        started_at = datetime.now(UTC).isoformat()
        runs: list[dict[str, Any]] = []
        for repeat in range(1, EVAL_REPEATS + 1):
            scores = []
            for case in cases:
                try:
                    output = cast(
                        AgentPlannerOutput,
                        asyncio_run(
                            skill.execute(
                                {"input": planner_input(case), "agent": agent, "prompt_loader": loader}
                            )
                        ),
                    )
                except Exception as exc:  # noqa: BLE001 - 失败分类计入报告
                    scores.append(score_case(case, None, error=exc))
                    continue
                scores.append(score_case(case, output))

            metrics = aggregate(scores)
            gates = evaluate_gates(metrics)
            runs.append(
                {
                    "repeat": repeat,
                    "split": EVAL_SPLIT,
                    "total": len(cases),
                    "metrics": metrics,
                    "gates": gates,
                    "failures": [
                        {
                            "id": s.case_id,
                            "failure_class": s.failure_class,
                            "sub_failures": s.sub_failures,
                            "expected_turn_type": s.expected_turn_type,
                            "predicted_turn_type": s.predicted_turn_type,
                            "expected_intent": s.expected_intent,
                            "predicted_intent": s.predicted_intent,
                            "error_detail": s.error_detail,
                        }
                        for s in scores
                        if not s.ok
                    ],
                }
            )

        # 均值与最差值（逐门槛取所有 run 中的最低值；门槛按最差一次判定）
        gate_keys = [g["gate"] for g in runs[0]["gates"]]
        worst_gates: list[dict[str, Any]] = []
        for key in gate_keys:
            per_run = [next(g for g in r["gates"] if g["gate"] == key) for r in runs]
            values = [g["value"] for g in per_run if g["value"] is not None]
            worst = min(per_run, key=lambda g: (g["value"] is None, g["value"] or 0))
            worst_gates.append(
                {
                    "gate": key,
                    "description": worst["description"],
                    "threshold": worst["threshold"],
                    "worst_value": worst["value"],
                    "mean_value": (sum(values) / len(values)) if values else None,
                    "per_run_values": [g["value"] for g in per_run],
                    "status": worst["status"],
                }
            )

        report: dict[str, Any] = {
            "provider": settings.llm_provider,
            "model": settings.llm_planner_model,
            "prompt_version": loader.get("agent_command_planner").version,
            "dataset_version": load_dataset()["version"],
            "dataset_split_counts": split_counts,
            "git_commit": _git_commit(),
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "repeats": EVAL_REPEATS,
            "split": EVAL_SPLIT,
            "mode": "report_only" if REPORT_ONLY else "release",
            "total": len(cases),
            "runs": runs,
            "gates_worst": worst_gates,
            "gate_summary": {
                "blocking": [g["gate"] for g in worst_gates if g["status"] != "pass"],
            },
        }

        RESULTS_DIR.mkdir(exist_ok=True)
        results_name = (
            "agent_commands_results.json"
            if EVAL_SPLIT == "all"
            else f"agent_commands_results_{EVAL_SPLIT}.json"
        )
        results_file = RESULTS_DIR / results_name
        results_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[eval] report written to {results_file}")

        # 可解析率兜底：模型不可用应显式失败而非全量 error 通过
        for r in runs:
            parse_rate = r["metrics"]["parse_rate"]
            assert parse_rate is not None and parse_rate > 0.8, (
                f"repeat {r['repeat']}：超过 20% 用例调用/解析失败，结果不可用"
            )
        if not REPORT_ONLY:
            # 发版模式：最差一次判门槛；fail 与 no_data 都阻断（§6.4/§7.6）
            blocking = [g for g in worst_gates if g["status"] != "pass"]
            assert not blocking, "发版门槛未达标（按最差一次判定）:\n" + "\n".join(
                f"  {g['gate']}: worst={g['worst_value']} mean={g['mean_value']}"
                f" threshold={g['threshold']}（{g['description']}）"
                for g in blocking
            )


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=EVAL_DIR,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - git 不可用时报告标注 unknown
        return "unknown"
