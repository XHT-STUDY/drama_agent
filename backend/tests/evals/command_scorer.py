"""对话命令评测的联合契约评分器（IR-1.2）。

纯函数模块：输入 (case 标注, AgentPlannerOutput 或错误) → 单条评分 →
聚合指标 → 发版门槛判定。不访问网络、不调用模型，可被 CI 单测与
真实模型 harness（test_agent_command_eval.py）共用。

评分层级（先 turn_type，再 intent，再 target，再 batch）与失败主类
（互斥，§4.3）都在这里唯一定义，保证离线报告与发版门槛同源。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.agent_planner import AgentPlannerOutput

# §3.3：写操作 intent——precision 门槛高于 recall（宁可澄清不可错执行）
WRITE_INTENTS = ("create_script", "evaluate", "revise_script", "revise_outline", "continue")

# §4.3 互斥失败主类。11（关键约束遗漏）与 12（下游执行范围漂移）
# 不属于离线 Planner 评测：前者人工复核约束原子，后者由端到端矩阵覆盖。
FAILURE_PROVIDER = "provider_error"
FAILURE_TRUNCATED = "output_truncated"
FAILURE_INVALID = "invalid_output"
FAILURE_TURN_TYPE = "turn_type_error"
FAILURE_INTENT = "intent_error"
FAILURE_TARGET_TYPE = "target_type_error"
FAILURE_EPISODE = "episode_error"
FAILURE_BATCH = "batch_error"
FAILURE_SHOULD_CLARIFY = "should_clarify_executed"
FAILURE_OVER_CLARIFY = "over_clarification"

# 错误码片段 → 失败主类（与 app.llm.models.LLMErrorCode 对应）
_ERROR_CLASS_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("OUTPUT_TRUNCATED",), FAILURE_TRUNCATED),
    (("TIMEOUT", "RATE_LIMIT", "PROVIDER_ERROR", "INVALID_REQUEST"), FAILURE_PROVIDER),
)


def classify_exception(exc: Exception) -> str:
    """把 runner 捕获的异常映射为 §4.3 失败主类。"""
    text = str(exc).upper()
    for hints, failure in _ERROR_CLASS_HINTS:
        if any(hint in text for hint in hints):
            return failure
    return FAILURE_INVALID


@dataclass
class CaseScore:
    """单条 case 的评分结果（含主失败类与全部次级失败）。"""

    case_id: str
    ok: bool
    expected_turn_type: str
    predicted_turn_type: str | None
    expected_intent: str | None
    predicted_intent: str | None
    turn_type_ok: bool
    intent_ok: bool | None = None
    target_type_ok: bool | None = None
    episode_ok: bool | None = None
    episode_bucket: str | None = None
    relies_on_context: bool = False
    batch_ok: bool | None = None
    failure_class: str | None = None
    sub_failures: list[str] = field(default_factory=list)
    error_class: str | None = None
    error_detail: str | None = None


def score_case(
    case: dict[str, Any],
    output: AgentPlannerOutput | None,
    *,
    error: Exception | None = None,
) -> CaseScore:
    """按 先 turn_type → intent → target → batch 的层级评分一条 case。

    target_type / episode_number / batch_size 仅在标注非空时比较；
    集数比较按 coverage 分桶：explicit_episode → explicit，否则有
    active_context → context（§4.2 目标准确率分列）。
    """
    expected = case["expected"]
    expected_turn = expected["turn_type"]
    expected_intent = expected.get("intent")
    coverage = case.get("coverage") or []

    if error is not None:
        return CaseScore(
            case_id=case["id"],
            ok=False,
            expected_turn_type=expected_turn,
            predicted_turn_type=None,
            expected_intent=expected_intent,
            predicted_intent=None,
            turn_type_ok=False,
            failure_class=classify_exception(error),
            error_class=classify_exception(error),
            error_detail=str(error)[:200],
        )

    assert output is not None  # error is None 时 runner 必须提供 output
    predicted_turn = output.turn_type
    predicted_intent = output.intent
    predicted_target_type = output.target.target_type if output.target else None
    predicted_episode = output.target.episode_number if output.target else None
    predicted_batch = output.batch_size

    turn_type_ok = predicted_turn == expected_turn
    intent_ok: bool | None = None
    if expected_intent is not None or predicted_intent is not None:
        # 期望无 intent（项目状态类 answer / clarification）时，模型多给的
        # intent 也会改变服务端路径（explain 走读原文），必须判错
        intent_ok = predicted_intent == expected_intent

    target_type_ok: bool | None = None
    if expected.get("target_type") is not None:
        target_type_ok = predicted_target_type == expected["target_type"]

    episode_ok: bool | None = None
    episode_bucket: str | None = None
    if expected.get("episode_number") is not None:
        if "explicit_episode" in coverage:
            episode_bucket = "explicit"
        elif case.get("active_context"):
            episode_bucket = "context"
        episode_ok = predicted_episode == expected["episode_number"]

    batch_ok: bool | None = None
    if expected_turn == "plan" and expected_intent == "continue":
        # None == None：写完剩余全部也是明确契约，不是缺失
        batch_ok = predicted_batch == expected.get("batch_size")

    # 目标依赖活动上下文解析：有上下文、无明确文本目标，且标注了
    # target_type / episode_number 任一（§3.3 active context 目标准确率）
    relies_on_context = bool(
        case.get("active_context")
        and "explicit_episode" not in coverage
        and (expected.get("target_type") is not None or expected.get("episode_number") is not None)
    )

    sub_failures: list[str] = []
    if not turn_type_ok:
        sub_failures.append(FAILURE_TURN_TYPE)
    if intent_ok is False:
        sub_failures.append(FAILURE_INTENT)
    if target_type_ok is False:
        sub_failures.append(FAILURE_TARGET_TYPE)
    if episode_ok is False:
        sub_failures.append(FAILURE_EPISODE)
    if batch_ok is False:
        sub_failures.append(FAILURE_BATCH)

    failure_class = _main_failure_class(
        expected_turn=expected_turn,
        predicted_turn=predicted_turn,
        sub_failures=sub_failures,
    )
    ok = not sub_failures
    return CaseScore(
        case_id=case["id"],
        ok=ok,
        expected_turn_type=expected_turn,
        predicted_turn_type=predicted_turn,
        expected_intent=expected_intent,
        predicted_intent=predicted_intent,
        turn_type_ok=turn_type_ok,
        intent_ok=intent_ok,
        target_type_ok=target_type_ok,
        episode_ok=episode_ok,
        episode_bucket=episode_bucket,
        relies_on_context=relies_on_context,
        batch_ok=batch_ok,
        failure_class=failure_class,
        sub_failures=sub_failures,
    )


def _main_failure_class(
    *,
    expected_turn: str,
    predicted_turn: str,
    sub_failures: list[str],
) -> str | None:
    """从次级失败推导唯一主失败类（§4.3 互斥，顺序即优先级）。"""
    if not sub_failures:
        return None
    # 类 9 只针对"应澄清却出了可执行计划"；answer 不产生执行，归 turn_type 错误
    if expected_turn == "clarification" and predicted_turn == "plan":
        return FAILURE_SHOULD_CLARIFY
    if expected_turn in ("plan", "answer") and predicted_turn == "clarification":
        return FAILURE_OVER_CLARIFY
    for candidate in (
        FAILURE_TURN_TYPE,
        FAILURE_INTENT,
        FAILURE_TARGET_TYPE,
        FAILURE_EPISODE,
        FAILURE_BATCH,
    ):
        if candidate in sub_failures:
            return candidate
    return FAILURE_INVALID


# ========================================================================
# 聚合指标
# ========================================================================


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _bool_rate(values: list[bool]) -> float | None:
    return _rate(sum(1 for v in values if v), len(values))


def _prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision = _rate(tp, tp + fp)
    recall = _rate(tp, tp + fn)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall > 0
        else None
    )
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def aggregate(scores: list[CaseScore]) -> dict[str, Any]:
    """把逐条评分聚合为 §4.2 指标：结构化可解析率、turn_type、intent
    micro/macro、澄清 P/R、target/batch 准确率、混淆矩阵与失败分布。"""
    total = len(scores)
    error_cases = [s for s in scores if s.error_class is not None]
    parse_ok = total - len(error_cases)

    # ---- turn_type（三分类）----
    turn_confusion: dict[str, int] = {}
    turn_correct = 0
    for s in scores:
        key = f"{s.expected_turn_type}->{s.predicted_turn_type or 'error'}"
        turn_confusion[key] = turn_confusion.get(key, 0) + 1
        if s.turn_type_ok:
            turn_correct += 1

    # ---- intent P/R/F1（仅在标注意图的样本上；micro + macro）----
    eligible = [s for s in scores if s.expected_intent is not None]
    intent_buckets: dict[str, dict[str, int]] = {}
    for s in eligible:
        assert s.expected_intent is not None  # eligible 过滤的窄化在循环内重申
        bucket = intent_buckets.setdefault(s.expected_intent, {"tp": 0, "fp": 0, "fn": 0})
        if s.intent_ok:
            bucket["tp"] += 1
        else:
            bucket["fn"] += 1
            if s.predicted_intent is not None:
                other = intent_buckets.setdefault(s.predicted_intent, {"tp": 0, "fp": 0, "fn": 0})
                other["fp"] += 1
    intents: dict[str, Any] = {name: _prf(**b) for name, b in intent_buckets.items()}
    micro_tp = sum(b["tp"] for b in intent_buckets.values())
    micro_fp = sum(b["fp"] for b in intent_buckets.values())
    micro_fn = sum(b["fn"] for b in intent_buckets.values())
    f1_values = [v["f1"] for v in intents.values() if v["f1"] is not None]
    macro_f1 = sum(f1_values) / len(f1_values) if f1_values else None

    # ---- 澄清 P/R（防漏澄清，也防过度澄清）----
    expected_clar = [s for s in scores if s.expected_turn_type == "clarification"]
    predicted_clar = [s for s in scores if s.predicted_turn_type == "clarification"]
    clar_tp = sum(1 for s in predicted_clar if s.expected_turn_type == "clarification")

    # ---- target / batch ----
    def _accuracy(scores_subset: list[CaseScore], attr: str) -> float | None:
        judged = [getattr(s, attr) for s in scores_subset if getattr(s, attr) is not None]
        return sum(1 for v in judged if v) / len(judged) if judged else None

    tt_annotated = [s for s in scores if s.target_type_ok is not None]
    ep_explicit = [s for s in scores if s.episode_bucket == "explicit"]
    ep_context = [s for s in scores if s.episode_bucket == "context"]
    # active context 目标准确率：依赖上下文解析目标的 case，target+集数全对
    context_target_ok = [
        all(v is not False for v in (s.target_type_ok, s.episode_ok)) for s in scores if s.relies_on_context
    ]
    batch_judged = [s for s in scores if s.batch_ok is not None]

    failure_breakdown: dict[str, int] = {}
    for s in scores:
        if s.failure_class:
            failure_breakdown[s.failure_class] = failure_breakdown.get(s.failure_class, 0) + 1

    intent_confusion: dict[str, int] = {}
    for s in eligible:
        key = f"{s.expected_intent}->{s.predicted_intent or 'none'}"
        intent_confusion[key] = intent_confusion.get(key, 0) + 1

    write_intents_present = [i for i in WRITE_INTENTS if i in intents]
    write_precision_values = [
        intents[i]["precision"] for i in write_intents_present if intents[i]["precision"] is not None
    ]
    write_recall_values = [
        intents[i]["recall"] for i in write_intents_present if intents[i]["recall"] is not None
    ]

    return {
        "total": total,
        "parse_rate": _rate(parse_ok, total),
        "errors": {
            "count": len(error_cases),
            "by_class": {
                cls: sum(1 for s in error_cases if s.error_class == cls)
                for cls in sorted({s.error_class for s in error_cases if s.error_class})
            },
        },
        "turn_type_accuracy": _rate(turn_correct, total),
        "turn_type_confusion": turn_confusion,
        "intent": {
            "micro": _prf(micro_tp, micro_fp, micro_fn),
            "macro_f1": macro_f1,
            "per_intent": intents,
            "confusion": intent_confusion,
        },
        "clarification": {
            "precision": _rate(clar_tp, len(predicted_clar)),
            "recall": _rate(clar_tp, len(expected_clar)),
            "expected": len(expected_clar),
            "predicted": len(predicted_clar),
        },
        "target": {
            "target_type_accuracy": _accuracy(tt_annotated, "target_type_ok"),
            "episode_accuracy_explicit": _accuracy(ep_explicit, "episode_ok"),
            "episode_accuracy_context": _accuracy(ep_context, "episode_ok"),
            "context_target_accuracy": _bool_rate(context_target_ok),
        },
        "continue": {
            "precision": intents.get("continue", {}).get("precision"),
            "batch_accuracy": _accuracy(batch_judged, "batch_ok"),
        },
        "write_intent": {
            "precision_min": min(write_precision_values) if write_precision_values else None,
            "recall_min": min(write_recall_values) if write_recall_values else None,
        },
        "failure_breakdown": failure_breakdown,
    }


# ========================================================================
# 发版门槛（§3.3 中离线可测子集）
# ========================================================================

# (指标键路径, 门槛, 说明)。键路径支持一级嵌套 "a.b"。
RELEASE_GATES: tuple[tuple[str, float, str], ...] = (
    ("parse_rate", 0.99, "结构化输出可解析率"),
    ("turn_type_accuracy", 0.95, "turn_type 三分类准确率"),
    ("intent.macro_f1", 0.90, "intent macro-F1"),
    ("write_intent.precision_min", 0.98, "写操作 intent 最低 precision"),
    ("write_intent.recall_min", 0.90, "写操作 intent 最低 recall"),
    ("clarification.precision", 0.95, "澄清 precision（防过度澄清）"),
    ("clarification.recall", 0.95, "澄清 recall（防漏澄清）"),
    ("target.target_type_accuracy", 0.99, "明确文本目标类型准确率"),
    ("target.episode_accuracy_explicit", 0.99, "明确文本集数准确率"),
    ("target.context_target_accuracy", 0.95, "active context 目标准确率"),
    ("continue.precision", 0.98, "continue precision"),
    ("continue.batch_accuracy", 0.95, "continue batch_size 准确率"),
)


def _lookup(metrics: dict[str, Any], dotted_key: str) -> Any:
    value: Any = metrics
    for part in dotted_key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def evaluate_gates(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """按 §3.3 门槛逐项判定。

    无数据（指标为 None，如数据集缺该类样本）记 no_data：报告层保留
    可见性，但发版判定（release_gates）把 no_data 也视为不达标——
    §3.3 的门槛是发布合同，"没测到"不等于"达标"。
    """
    results: list[dict[str, Any]] = []
    for key, threshold, description in RELEASE_GATES:
        value = _lookup(metrics, key)
        if value is None:
            status = "no_data"
        elif value >= threshold:
            status = "pass"
        else:
            status = "fail"
        results.append(
            {
                "gate": key,
                "description": description,
                "value": value,
                "threshold": threshold,
                "status": status,
            }
        )
    return results


def release_blocking_gates(gates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """发版阻断项：fail 与 no_data 都阻断（任一存在即非零退出）。"""
    return [g for g in gates if g["status"] != "pass"]


def failed_gates(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """发版判定便捷入口：fail 与 no_data 均视为不达标（§6.4 非零退出）。"""
    return release_blocking_gates(evaluate_gates(metrics))
