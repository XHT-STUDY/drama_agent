"""对话记忆评测 harness(M-01)。

四个消融组(与 MEMORY_IMPLEMENTATION_PLAN §3 一致):
- none        无 Memory:只携带当前请求,历史全部丢弃;
- recent_only 仅短期窗口:最近 window 条原始消息;
- current     当前实现:ConversationSummaryManager 分段摘要语义 +
              latest_project_summary_text 只读最新一段 + 最近消息
              (复刻 app/memory/summary.py 的生产读取行为);
- structured  目标实现:累计摘要(summary_k = f(summary_{k-1}, 新区间)) +
              最近消息(MEMORY_DESIGN §7.3)。

摘要器是确定性抽取(FakeLLM 语义):保留带【设定】【否决】【要求】【问题】
标签的用户消息;【改口】按 KEY 撤销旧设定。四组共用同一抽取器,
差异只在记忆策略,保证测的是策略而非抽取质量。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from app.domain.context import CharacterRatioEstimator, TaskKind
from app.memory.context_builder import ContextBuilder

_GOLDEN = Path(__file__).resolve().parents[2] / "golden" / "memory"
GROUPS = ("none", "recent_only", "current", "structured")

_RETAIN_TAGS = ("设定", "否决", "要求", "问题")
_REVISION_TAG = "改口"

_estimator = CharacterRatioEstimator()


def load_dialogue_cases() -> dict[str, Any]:
    return cast("dict[str, Any]",
                json.loads((_GOLDEN / "dialogue_cases.json").read_text(encoding="utf-8")))


# ========================================================================
# 确定性抽取器(FakeLLM 摘要语义)
# ========================================================================


def _tagged_lines(messages: list[dict[str, Any]]) -> list[tuple[int, str, str]]:
    """抽取带信号标签的行 → [(sequence, tag, content)]。

    只看 user 消息;数据集中的闲聊/助手消息无标签,自然被丢弃。
    """
    out: list[tuple[int, str, str]] = []
    for m in messages:
        if m["role"] != "user":
            continue
        content = m["content"]
        if not content.startswith("【"):
            continue
        head = content[1:content.find("】")]
        if head in _RETAIN_TAGS or head == _REVISION_TAG:
            out.append((m["sequence"], head, content))
    return out


def _line_key(content: str) -> str | None:
    """【TAG】KEY：CONTENT → KEY(改口撤销的目标键)。"""
    body = content[content.find("】") + 1:]
    if "：" not in body:
        return None
    return body.split("：", 1)[0]


def extract_lines(messages: list[dict[str, Any]],
                  previous: list[str] | None = None) -> list[str]:
    """累计抽取:previous 为上一版累计摘要的行,返回新累计。

    【改口】KEY：… 删除先前同 KEY 的【设定】行(最新事实胜出),
    其余保留行按 sequence 升序排列。
    """
    lines: list[tuple[int, str]] = [
        (int(x.split("]", 1)[0].lstrip("[")), x) for x in (previous or [])
    ]
    for seq, tag, content in _tagged_lines(messages):
        if tag == _REVISION_TAG:
            key = _line_key(content)
            if key is not None:
                lines = [ln for ln in lines if not _same_key(ln[1], key)]
        lines.append((seq, f"[{seq}] {content}"))
    lines.sort(key=lambda x: x[0])
    return [text for _, text in lines]


def _same_key(line: str, key: str) -> bool:
    body = line[line.find("】") + 1:] if "】" in line else line
    lk = _line_key(f"【设定】{body}")
    return lk is not None and lk == key


# ========================================================================
# 记忆策略(四个消融组)
# ========================================================================


@dataclass
class MemoryView:
    """一组记忆策略为某个会话产出的可读记忆。"""

    group: str
    summary_text: str = ""      # 摘要区(可为空)
    recent_text: str = ""       # 最近消息区(原始消息)
    covered: list[tuple[int, int]] = field(default_factory=list)  # 摘要覆盖区间
    segments_kept: int = 0      # 读取时实际可见的摘要段数


def _recent_window(messages: list[dict[str, Any]], window: int) -> list[dict[str, Any]]:
    return messages[-window:]


def _render_recent(messages: list[dict[str, Any]]) -> str:
    return "\n".join(f"[{m['sequence']}] {m['role']}: {m['content']}" for m in messages)


def _segment_bounds(count: int, threshold: int, window: int) -> list[tuple[int, int]]:
    """复刻 ConversationSummaryManager 的分段边界。

    每达到 threshold 整数倍触发一次;covered_to = count - window;
    covered_from = 上一段 covered_to + 1。
    """
    bounds: list[tuple[int, int]] = []
    prev_to = 0
    for n in range(threshold, count + 1, threshold):
        covered_to = n - window
        if covered_to < 1:
            continue
        covered_from = prev_to + 1
        if covered_from > covered_to:
            continue
        bounds.append((covered_from, covered_to))
        prev_to = covered_to
    return bounds


def build_view(case: dict[str, Any], group: str,
               *, summarize_fn=None) -> MemoryView:
    """组装指定消融组的记忆视图。

    summarize_fn(segment_messages, previous_lines) → retained_lines,
    缺省为确定性抽取器;真实模型评测注入 LLM 版本(脚本 real provider)。
    """
    summarize = summarize_fn or extract_lines
    messages = cast(list[dict[str, Any]], case["messages"])
    count = int(case["message_count"])
    threshold = int(case.get("_threshold", 24))
    window = int(case.get("_window", 12))
    recent = _recent_window(messages, window)

    if group == "none":
        return MemoryView(group="none")

    if group == "recent_only":
        return MemoryView(group=group, recent_text=_render_recent(recent))

    bounds = _segment_bounds(count, threshold, window)

    if group == "current":
        # 生产读取:latest_project_summary_text 只返回覆盖终点最大的
        # 那一段摘要文本,早期段落不进入上下文。
        if not bounds:
            return MemoryView(group=group, recent_text=_render_recent(recent))
        last_from, last_to = bounds[-1]
        seg = [m for m in messages if last_from <= m["sequence"] <= last_to]
        lines = summarize(seg, None)
        return MemoryView(
            group=group,
            summary_text="\n".join(lines),
            recent_text=_render_recent(recent),
            covered=bounds,
            segments_kept=1,
        )

    if group == "structured":
        # 累计语义:逐段滚动,每段输入 = 上一版累计 + 新区间。
        cumulative: list[str] = []
        covered: list[tuple[int, int]] = []
        for seg_from, seg_to in bounds:
            seg = [m for m in messages if seg_from <= m["sequence"] <= seg_to]
            cumulative = summarize(seg, cumulative)
            covered.append((seg_from, seg_to))
        return MemoryView(
            group=group,
            summary_text="\n".join(cumulative),
            recent_text=_render_recent(recent),
            covered=covered,
            segments_kept=len(covered),
        )

    raise ValueError(f"未知消融组: {group}")


def view_text(view: MemoryView) -> str:
    return "\n".join(x for x in (view.summary_text, view.recent_text) if x)


# ========================================================================
# 评分:写入 / 召回 / 使用 / 成本
# ========================================================================


@dataclass
class DialogueResult:
    """单 case × 单组的四层评分。"""

    case_id: str
    group: str
    length_bucket: int
    # 写入层:摘要覆盖区间是否连续、无重叠、不越界
    covered: list[tuple[int, int]] = field(default_factory=list)
    segments_kept: int = 0
    coverage_continuous: bool = True
    coverage_complete: bool = True   # 除最近窗口外全部被摘要覆盖
    # 召回层
    latest_fact_hits: int = 0
    latest_fact_total: int = 0
    superseded_leaks: int = 0        # 已改口旧事实仍出现(越少越好)
    veto_hits: int = 0
    veto_total: int = 0
    constraint_hits: int = 0
    constraint_total: int = 0
    open_question_hits: int = 0
    open_question_total: int = 0
    fabrications: int = 0            # 虚构标记出现数(越少越好)
    cross_project_leaks: int = 0     # 跨项目标记出现数(必须 0)
    # 使用层:经 ContextBuilder 组装后关键项是否仍在
    use_latest_hits: int = 0
    use_latest_total: int = 0
    use_manifest_consistent: bool = True
    # 成本层
    history_tokens: int = 0          # 记忆区估算 token
    full_history_tokens: int = 0     # 完整历史估算 token(对照)
    details: dict[str, Any] = field(default_factory=dict)


def _view_for(dataset: dict[str, Any], case: dict[str, Any], group: str,
              summarize_fn=None) -> MemoryView:
    return build_view(
        dict(case, _threshold=dataset["threshold"], _window=dataset["window"]),
        group, summarize_fn=summarize_fn,
    )


def _project_view_text(dataset: dict[str, Any], case: dict[str, Any], group: str,
                       summarize_fn=None) -> str:
    """项目级记忆(current:按 covered_to 最大取一段——复刻生产跨会话读取;
    structured:当前会话累计 + 其余会话累计的有界合并,标注来源会话)。
    """
    project_id = case["project_id"]
    convs = [c for c in dataset["cases"] if c["project_id"] == project_id]
    views = [_view_for(dataset, c, group, summarize_fn) for c in convs]
    if group == "current":
        # 生产 latest_project_summary_text:跨会话取 covered_to 最大的一条
        best = max(views, key=lambda v: (v.covered[-1][1] if v.covered else -1))
        return view_text(best)
    # structured:当前会话优先,其余按覆盖终点稳定排序做有界合并
    current_view = _view_for(dataset, case, group, summarize_fn)
    others = [v for v in views if v is not current_view]
    others.sort(key=lambda v: v.covered[-1][1] if v.covered else 0, reverse=True)
    parts = [view_text(current_view)]
    parts += [f"(其他会话记忆) {view_text(v)}" for v in others[:2]]
    return "\n".join(p for p in parts if p)


def evaluate_case(dataset: dict[str, Any], case: dict[str, Any], group: str,
                  *, summarize_fn=None) -> DialogueResult:
    exp = case["expected"]
    case_cfg = dict(case, _threshold=dataset["threshold"], _window=dataset["window"])
    view = build_view(case_cfg, group, summarize_fn=summarize_fn)
    text = view_text(view)
    result = DialogueResult(case_id=case["id"], group=group,
                            length_bucket=int(case["length_bucket"]))

    # ---- 写入层 ----
    window = int(dataset["window"])
    result.covered = list(view.covered)
    result.segments_kept = view.segments_kept
    if view.covered:
        result.coverage_continuous = all(
            view.covered[i][1] + 1 == view.covered[i + 1][0]
            for i in range(len(view.covered) - 1)
        )
        expect_to = max(0, int(case["message_count"]) - window)
        result.coverage_complete = view.covered[-1][1] >= expect_to

    # ---- 召回层(直接在记忆文本上判) ----
    latest = [f["marker"] for f in exp["latest_facts"]]
    result.latest_fact_total = len(latest)
    result.latest_fact_hits = sum(1 for m in latest if m in text)
    result.superseded_leaks = sum(1 for m in exp["superseded_markers"] if m in text)
    result.veto_total = len(exp["veto_markers"])
    result.veto_hits = sum(1 for m in exp["veto_markers"] if m in text)
    result.constraint_total = len(exp["constraint_markers"])
    result.constraint_hits = sum(1 for m in exp["constraint_markers"] if m in text)
    result.open_question_total = len(exp["open_question_markers"])
    result.open_question_hits = sum(1 for m in exp["open_question_markers"] if m in text)
    result.fabrications = sum(1 for m in exp["fabrication_negatives"] if m in text)

    # ---- 跨项目泄漏(项目级合并视图) ----
    project_text = _project_view_text(dataset, case, group, summarize_fn)
    result.cross_project_leaks = sum(
        1 for m in exp["cross_project_negatives"] if m in project_text
    )

    # ---- 使用层:经 ContextBuilder(WRITER 策略)组装 ----
    builder = ContextBuilder(budget_tokens=24_000)
    assembled, manifest = builder.build_for(
        TaskKind.WRITER,
        user_request="继续创作",
        story_bible_outline="(评测桩)设定略",
        previous_summary_continuity=text,
        current_target="第 N 集大纲(评测桩)",
    )
    result.use_latest_total = len(latest)
    result.use_latest_hits = sum(1 for m in latest if m in assembled)
    # Manifest 一致性(成本契约):未截断段的估算 == 同一估算器对输入的估算;
    # 总估算 == 对最终组装文本的估算;截断必须显式记录原因。
    consistent = manifest.estimated_tokens == _estimator.estimate(assembled)
    if text and "previous_summary_continuity" not in manifest.sections_used:
        consistent = consistent and bool(manifest.truncation_reasons)  # 必须记录移除原因
    if "previous_summary_continuity" in manifest.sections_truncated:
        consistent = consistent and any(
            "previous_summary_continuity" in r for r in manifest.truncation_reasons
        )
    else:
        consistent = consistent and manifest.section_estimates.get(
            "previous_summary_continuity", 0
        ) == _estimator.estimate(text)
    result.use_manifest_consistent = consistent

    # ---- 成本层 ----
    full_history = "\n".join(f"[{m['sequence']}] {m['role']}: {m['content']}"
                             for m in case["messages"])
    result.history_tokens = _estimator.estimate(text)
    result.full_history_tokens = _estimator.estimate(full_history)
    return result


def evaluate_dataset(dataset: dict[str, Any],
                     groups: tuple[str, ...] = GROUPS,
                     *, summarize_fn=None) -> dict[str, Any]:
    """全量评测 → 汇总指标(按组 × 长度档)。"""
    results = [evaluate_case(dataset, c, g, summarize_fn=summarize_fn)
               for c in dataset["cases"] for g in groups]
    summary: dict[str, Any] = {"groups": list(groups), "by_group": {}, "by_bucket": {}}
    for g in groups:
        rs = [r for r in results if r.group == g]
        summary["by_group"][g] = _aggregate(rs)
        summary["by_bucket"][g] = {
            str(bucket): _aggregate([r for r in rs if r.length_bucket == bucket])
            for bucket in sorted({r.length_bucket for r in rs})
        }
    summary["case_results"] = [dict(r.__dict__) for r in results]
    return summary


def _aggregate(rs: list[DialogueResult]) -> dict[str, Any]:
    def rate(hits: int, total: int) -> float | None:
        return hits / total if total else None

    full_tokens = rs[0].full_history_tokens if rs else 0
    return {
        "cases": len(rs),
        "latest_fact_rate": rate(sum(r.latest_fact_hits for r in rs),
                                 sum(r.latest_fact_total for r in rs)),
        "superseded_leak_rate": rate(sum(r.superseded_leaks for r in rs),
                                     sum(r.superseded_leaks for r in rs)
                                     + sum(r.latest_fact_total for r in rs)),
        "veto_recall": rate(sum(r.veto_hits for r in rs),
                            sum(r.veto_total for r in rs)),
        "constraint_recall": rate(sum(r.constraint_hits for r in rs),
                                  sum(r.constraint_total for r in rs)),
        "open_question_recall": rate(sum(r.open_question_hits for r in rs),
                                     sum(r.open_question_total for r in rs)),
        "fabrication_count": sum(r.fabrications for r in rs),
        "cross_project_leak_count": sum(r.cross_project_leaks for r in rs),
        "use_latest_rate": rate(sum(r.use_latest_hits for r in rs),
                                sum(r.use_latest_total for r in rs)),
        "manifest_consistent": all(r.use_manifest_consistent for r in rs),
        "history_tokens_avg": (
            sum(r.history_tokens for r in rs) / len(rs) if rs else 0
        ),
        "full_history_tokens": full_tokens,
        "token_saving_vs_full": (
            1 - (sum(r.history_tokens for r in rs) / len(rs)) / full_tokens
            if rs and full_tokens else None
        ),
    }
