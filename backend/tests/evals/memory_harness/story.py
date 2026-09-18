"""剧情连续性评测 harness(M-01)。

四个消融组:
- none        无状态:Writer 上下文不含任何前情;
- recent_only 仅上一集:只带第 N-1 集的原始事件文本;
- current     当前实现:复刻 write_episode 生产路径——
              EpSummary(summary=f"第 n 集完成: {title}",
                        key_events=前三场动作[:30]) + ContinuityManager
              (生产 Writer 从不传 resolved_loop_ids/character_updates,
               伏笔永远 open、角色知识不更新,如实测量);
- structured  目标实现:typed delta 纯 reducer(作者事实 / 角色知识 /
              伏笔 / 道具 / 关系 / 时间线 / 作者未来计划),
              为 M-03 的可执行规格。

评分全部确定性:substring 探针 + 状态比对,不使用 LLM Judge。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from app.domain.continuity import (
    CharacterState,
    ContinuityState,
    EpisodeSummary,
    StoryLoop,
)
from app.memory.continuity import ContinuityManager

_GOLDEN = Path(__file__).resolve().parents[2] / "golden" / "memory"
GROUPS = ("none", "recent_only", "current", "structured")


def load_story_cases() -> dict[str, Any]:
    return cast("dict[str, Any]",
                json.loads((_GOLDEN / "story_cases.json").read_text(encoding="utf-8")))


# ========================================================================
# structured 参考状态(M-03 typed delta 的可执行规格)
# ========================================================================


@dataclass
class StructuredState:
    """绑定确切工作集的剧情状态(参考实现)。"""

    through_episode: int = 0
    project_id: str = ""
    # fact_id → {text, source_episode, source_scene}
    facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    # character_id → {fact_id: learned_episode}
    knowledge: dict[str, dict[str, int]] = field(default_factory=dict)
    # loop_id → {description, status, introduced_episode, resolved_episode}
    loops: dict[str, dict[str, Any]] = field(default_factory=dict)
    # prop_id → {holder_character_id, source_episode}
    props: dict[str, dict[str, Any]] = field(default_factory=dict)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    # 未揭示的作者计划:reveal_episode 之前不得进入"已发生"事实
    future_plans: list[dict[str, Any]] = field(default_factory=list)
    locked_facts: list[str] = field(default_factory=list)


def initial_structured_state(case: dict[str, Any]) -> StructuredState:
    bible = case["characters"]
    state = StructuredState(
        project_id=case["project_id"], locked_facts=list(case["locked_facts"])
    )
    chars = [bible["protagonist"], bible["antagonist"], *bible["supporting_characters"]]
    for ch in chars:
        state.knowledge[ch["character_id"]] = {}
    for loop_desc in case.get("open_loops", []):
        lid = f"loop_{len(state.loops) + 1:03d}"
        state.loops[lid] = {
            "description": loop_desc, "status": "open",
            "introduced_episode": 0, "resolved_episode": None,
        }
    for plan in case.get("future_plans", []):
        state.future_plans.append(dict(plan, revealed=False))
    return state


def apply_delta(state: StructuredState, episode: dict[str, Any]) -> StructuredState:
    """纯 reducer:应用一集 typed delta,返回新状态(不修改入参)。

    服务端规则(MEMORY_DESIGN §7.4):
    - 事实带来源(集数/场次);伏笔用稳定 ID,重开允许 open→resolved→open;
    - 角色知识只按显式 learned=True 增加,learned=False 不删除已有知识;
    - 计划在 reveal_episode 当集转 revealed。
    """
    n = int(episode["episode_number"])
    d = episode["typed_delta"]
    nxt = StructuredState(
        through_episode=n,
        project_id=state.project_id,
        facts={k: dict(v) for k, v in state.facts.items()},
        knowledge={c: dict(f) for c, f in state.knowledge.items()},
        loops={k: dict(v) for k, v in state.loops.items()},
        props={k: dict(v) for k, v in state.props.items()},
        relationships=[dict(r) for r in state.relationships],
        timeline=[dict(t) for t in state.timeline],
        future_plans=[dict(p) for p in state.future_plans],
        locked_facts=list(state.locked_facts),
    )

    for fact in d.get("facts", []):
        fid = fact["fact_id"]
        if fid not in nxt.facts:  # 事实不可变:首见记录来源
            nxt.facts[fid] = {
                "text": fact["text"], "source_episode": n,
                "source_scene": fact["source_scene"],
            }
    for k in d.get("knowledge", []):
        if k["learned"]:
            known = nxt.knowledge.setdefault(k["character_id"], {})
            known.setdefault(k["fact_id"], n)
    for loop in d.get("loops_introduced", []):
        lid = loop["loop_id"]
        entry = nxt.loops.get(lid)
        if entry is None:
            nxt.loops[lid] = {
                "description": loop["description"], "status": "open",
                "introduced_episode": n, "resolved_episode": None,
            }
        else:  # 重开:resolved → open
            entry["status"] = "open"
            entry["resolved_episode"] = None
            entry["reopened_episode"] = n
    for lid in d.get("loops_resolved", []):
        entry = nxt.loops.get(lid)
        if entry is not None and entry["status"] == "open":
            entry["status"] = "resolved"
            entry["resolved_episode"] = n
    for p in d.get("props", []):
        nxt.props[p["prop_id"]] = {
            "holder_character_id": p["holder_character_id"],
            "source_episode": n, "source_scene": p["source_scene"],
        }
    for r in d.get("relationship_changes", []):
        nxt.relationships.append({**r, "episode_number": n})
    for t in d.get("timeline_events", []):
        nxt.timeline.append({**t, "episode_number": n})
    for plan in nxt.future_plans:
        if not plan["revealed"] and n >= int(plan["reveal_episode"]):
            plan["revealed"] = True
    return nxt


# ========================================================================
# 各组的 Writer 上下文(第 N 集视角:状态截至 N-1)
# ========================================================================


def _name_of(case: dict[str, Any], cid: str) -> str:
    ch = case["characters"]
    for c in [ch["protagonist"], ch["antagonist"], *ch["supporting_characters"]]:
        if c["character_id"] == cid:
            return str(c["name"])
    return cid


def render_structured_context(case: dict[str, Any], state: StructuredState,
                              target_episode: int) -> str:
    """structured 组上下文:截至 target_episode-1 的事实/知识/伏笔/道具。"""
    parts: list[str] = []
    facts = {fid: f for fid, f in state.facts.items()
             if f["source_episode"] < target_episode}
    if facts:
        parts.append("## 已发生事实(作者视角)")
        for fid, f in sorted(facts.items()):
            parts.append(
                f"- [{fid}] {f['text']}(第{f['source_episode']}集第{f['source_scene']}场)"
            )
    parts.append("## 角色已知信息")
    for cid, known in state.knowledge.items():
        items = sorted(
            (fid, ep) for fid, ep in known.items() if ep < target_episode
        )
        if items:
            names = "、".join(f"{fid}(第{ep}集得知)" for fid, ep in items)
            parts.append(f"- 已知[{cid}] {_name_of(case, cid)}: {names}")
    open_loops = [(lid, lp) for lid, lp in state.loops.items() if lp["status"] == "open"]
    resolved = [(lid, lp) for lid, lp in state.loops.items() if lp["status"] == "resolved"]
    if open_loops:
        parts.append("## 未闭合伏笔")
        for lid, lp in open_loops:
            parts.append(f"- [{lid}] {lp['description']}")
    if resolved:
        parts.append("## 已回收伏笔")
        for lid, lp in resolved:
            parts.append(f"- [{lid}] {lp['description']}(第{lp['resolved_episode']}集回收)")
    if state.props:
        parts.append("## 道具归属")
        for pid, p in sorted(state.props.items()):
            if p["source_episode"] < target_episode:
                parts.append(
                    f"- {pid} 现由 {_name_of(case, p['holder_character_id'])} 持有"
                )
    if state.timeline:
        events = [t for t in state.timeline if t["episode_number"] < target_episode]
        if events:
            parts.append("## 时间线(已发生)")
            events.sort(key=lambda t: (t["episode_number"], t["order_in_episode"]))
            for t in events:
                parts.append(f"- 第{t['episode_number']}集: {t['description']}")
    if state.locked_facts:
        parts.append("## 锁定事实")
        parts.extend(f"- {x}" for x in state.locked_facts)
    pending = [p for p in state.future_plans if not p["revealed"]]
    if pending:
        parts.append("## 作者计划(未发生,不得写入正文事实)")
        parts.extend(f"- (计划) {p['text']}" for p in pending)
    return "\n".join(parts)


def render_current_context(case: dict[str, Any], target_episode: int) -> str:
    """current 组:复刻 write_episode 的 ContinuityManager 标题摘要路径。"""
    bible = case["characters"]
    sb_loops = list(case.get("open_loops", []))
    manager = ContinuityManager()
    state = ContinuityState(
        through_episode=0,
        locked_facts=list(case["locked_facts"]),
        open_loops=[
            StoryLoop(loop_id=f"loop_{i + 1:03d}", description=d,
                     introduced_episode=0, status="open")
            for i, d in enumerate(sb_loops)
        ],
        character_states={
            c["character_id"]: CharacterState(
                character_id=c["character_id"], current_goal=c["visible_goal"],
                last_updated_episode=0,
            )
            for c in [bible["protagonist"], bible["antagonist"],
                      *bible["supporting_characters"]]
        },
    )
    for ep in case["episodes"]:
        n = int(ep["episode_number"])
        if n >= target_episode:
            break
        actions = [s["action"] for s in ep["scenes"]][:3]
        stub = EpisodeSummary(
            episode_number=n,
            summary=f"第 {n} 集完成: {ep['title']}",
            key_events=[a[:30] for a in actions],
            ending_state="",
        )
        state = manager.update_after_episode(state, stub)
    return manager.get_context_for_episode(state, target_episode)


def render_recent_context(case: dict[str, Any], target_episode: int) -> str:
    """recent_only 组:仅第 N-1 集的原始事件文本。"""
    eps = {int(e["episode_number"]): e for e in case["episodes"]}
    prev = eps.get(target_episode - 1)
    if prev is None:
        return ""
    d = prev["typed_delta"]
    lines = [f"## 上一集(第{target_episode - 1}集)事件"]
    for fact in d.get("facts", []):
        lines.append(f"- {fact['text']}")
    for k in d.get("knowledge", []):
        if k["learned"]:
            lines.append(f"- {_name_of(case, k['character_id'])}得知 {k['fact_id']}")
    lines.extend(f"- 伏笔引入: {lp['description']}" for lp in d.get("loops_introduced", []))
    lines.extend(f"- 道具: {p['prop_id']} → {_name_of(case, p['holder_character_id'])}"
                 for p in d.get("props", []))
    return "\n".join(lines)


def build_context(case: dict[str, Any], state: StructuredState | None,
                  group: str, target_episode: int) -> str:
    if group == "none":
        return ""
    if group == "recent_only":
        return render_recent_context(case, target_episode)
    if group == "current":
        return render_current_context(case, target_episode)
    if group == "structured":
        assert state is not None
        return render_structured_context(case, state, target_episode)
    raise ValueError(f"未知消融组: {group}")


# ========================================================================
# 评分
# ========================================================================


@dataclass
class StoryResult:
    case_id: str
    group: str
    # 知识边界:必需已知缺失(missing)+ 未学先知(leak)
    knowledge_missing: int = 0
    knowledge_leak: int = 0
    # 未来计划提前当已发生(leak)
    future_plan_leak: int = 0
    # 伏笔(through=10)
    loop_tp: int = 0
    loop_fp: int = 0
    loop_fn: int = 0
    loop_open_hits: int = 0
    loop_open_total: int = 0
    # 道具归属正确性
    prop_hits: int = 0
    prop_total: int = 0
    # 锁定事实可用性
    locked_fact_hits: int = 0
    locked_fact_total: int = 0
    # 来源正确性(结构化必须 100%;其余组无来源记 0)
    fact_source_hits: int = 0
    fact_source_total: int = 0
    # 状态重放一致(重放两次序列化一致)
    replay_consistent: bool = False
    # 跨项目隔离
    cross_case_leaks: int = 0
    timeline_present: bool = False

    @property
    def violations(self) -> int:
        return self.knowledge_missing + self.knowledge_leak + self.future_plan_leak

    @property
    def loop_f1(self) -> float | None:
        denom = self.loop_tp + self.loop_fp + self.loop_fn
        if denom == 0:
            return None
        precision = self.loop_tp / (self.loop_tp + self.loop_fp) if self.loop_tp + self.loop_fp else 0.0
        recall = self.loop_tp / (self.loop_tp + self.loop_fn) if self.loop_tp + self.loop_fn else 0.0
        return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def ground_truth_state(case: dict[str, Any], through: int) -> StructuredState:
    state = initial_structured_state(case)
    for ep in case["episodes"]:
        if int(ep["episode_number"]) > through:
            break
        state = apply_delta(state, ep)
    return state


def evaluate_case(dataset: dict[str, Any], case: dict[str, Any], group: str) -> StoryResult:
    result = StoryResult(case_id=case["id"], group=group)
    all_cases = cast(list[dict[str, Any]], dataset["cases"])
    other_texts = [
        f["text"]
        for other in all_cases if other["id"] != case["id"]
        for e in other["episodes"] for f in e["typed_delta"].get("facts", [])
    ]

    replay_state = initial_structured_state(case)
    for ep in case["episodes"]:
        replay_state = apply_delta(replay_state, ep)
    replay_state2 = initial_structured_state(case)
    for ep in case["episodes"]:
        replay_state2 = apply_delta(replay_state2, ep)
    result.replay_consistent = (
        json.dumps(replay_state.__dict__, sort_keys=True, ensure_ascii=False)
        == json.dumps(replay_state2.__dict__, sort_keys=True, ensure_ascii=False)
    )

    for target in range(2, 11):
        truth = ground_truth_state(case, target - 1)
        state = ground_truth_state(case, target - 1) if group == "structured" else None
        context = build_context(case, state, group, target)
        next_ep = next(e for e in case["episodes"]
                       if int(e["episode_number"]) == target)

        # 必需知识:下一集出场角色 ≤ target-1 已知的事实必须可见
        for cid in next_ep["characters_in_episode"]:
            known = {fid for fid, ep in truth.knowledge.get(cid, {}).items()
                     if ep <= target - 1}
            for fid in known:
                fact = truth.facts.get(fid)
                if fact and fact["text"] not in context:
                    result.knowledge_missing += 1
        # 未学先知:角色尚未得知的事实不得出现在"该角色的已知行"里
        # (作者事实区出现是合法的——作者视角与角色知识分离)
        knowledge_lines = [ln for ln in context.splitlines() if "已知[" in ln]
        for cid in next_ep["characters_in_episode"]:
            not_yet = {fid for fid in truth.facts
                       if fid not in truth.knowledge.get(cid, {})
                       or truth.knowledge[cid][fid] > target - 1}
            if not not_yet:
                continue
            own_lines = [ln for ln in knowledge_lines if f"已知[{cid}]" in ln]
            for fid in not_yet:
                if any(fid in ln for ln in own_lines):
                    result.knowledge_leak += 1
        # 未来计划提前泄露(未揭示计划不得以已发生文本出现)
        for plan in case.get("future_plans", []):
            if target <= int(plan["reveal_episode"]):
                leaked = plan["text"] in context and "(计划)" not in context
                if leaked:
                    result.future_plan_leak += 1
        # 道具归属
        for pid in truth.props:
            holder_name = _name_of(case, truth.props[pid]["holder_character_id"])
            result.prop_total += 1
            if f"{pid} 现由 {holder_name} 持有" in context:
                result.prop_hits += 1
        # 锁定事实
        for lf in case["locked_facts"]:
            result.locked_fact_total += 1
            if lf in context:
                result.locked_fact_hits += 1
        # 跨项目隔离
        result.cross_case_leaks += sum(1 for t in other_texts if t and t in context)
        if group == "structured" and target >= 3:
            result.timeline_present = result.timeline_present or (
                "## 时间线" in context
            )

    # 伏笔状态(through=10,resolved 类 F1 + open 可见性)
    final = ground_truth_state(case, 10)
    if group == "structured":
        predicted = replay_state
    elif group == "current":
        # 生产路径:Writer 从不回收伏笔 → 全部预测 open
        predicted = None
    else:
        predicted = None
    for lid, truth_loop in final.loops.items():
        pred_status: str | None = None
        if group == "structured":
            pred_loop = predicted.loops.get(lid) if predicted else None
            pred_status = pred_loop["status"] if pred_loop else None
        elif group == "current":
            pred_status = "open"
        # none / recent_only:无从判断 → 全部计入 fn(真 resolved)与不可见
        truth_status = truth_loop["status"]
        if pred_status == "resolved" and truth_status == "resolved":
            result.loop_tp += 1
        elif pred_status == "resolved" and truth_status != "resolved":
            result.loop_fp += 1
        elif pred_status != "resolved" and truth_status == "resolved":
            result.loop_fn += 1
    # open 伏笔可见性(第 10 集视角:截至第 9 集 open 的伏笔描述必须可见)
    truth9 = ground_truth_state(case, 9)
    ctx10 = build_context(
        case, ground_truth_state(case, 9) if group == "structured" else None,
        group, 10,
    )
    result.loop_open_total = sum(
        1 for lp in truth9.loops.values() if lp["status"] == "open"
    )
    result.loop_open_hits = sum(
        1 for lp in truth9.loops.values()
        if lp["status"] == "open" and lp["description"] in ctx10
    )

    # 来源正确性(structured 专属)
    if group == "structured":
        for fid, f in replay_state.facts.items():
            result.fact_source_total += 1
            truth_fact = final.facts.get(fid)
            if truth_fact and (
                f["source_episode"] == truth_fact["source_episode"]
                and f["source_scene"] == truth_fact["source_scene"]
            ):
                result.fact_source_hits += 1

    return result


def evaluate_dataset(dataset: dict[str, Any],
                     groups: tuple[str, ...] = GROUPS) -> dict[str, Any]:
    results = [evaluate_case(dataset, c, g) for c in dataset["cases"] for g in groups]
    by_group: dict[str, Any] = {}
    for g in groups:
        rs = [r for r in results if r.group == g]
        none_violations = sum(
            r.violations for r in results if r.group == "none" and r.case_id in
            {x.case_id for x in rs}
        )
        total_violations = sum(r.violations for r in rs)
        f1s = [r.loop_f1 for r in rs if r.loop_f1 is not None]
        by_group[g] = {
            "cases": len(rs),
            "knowledge_missing": sum(r.knowledge_missing for r in rs),
            "knowledge_leak": sum(r.knowledge_leak for r in rs),
            "future_plan_leak": sum(r.future_plan_leak for r in rs),
            "violations": total_violations,
            "violation_reduction_vs_none": (
                1 - total_violations / none_violations if none_violations else None
            ),
            "loop_f1_avg": sum(f1s) / len(f1s) if f1s else None,
            "loop_open_recall": (
                sum(r.loop_open_hits for r in rs) / sum(r.loop_open_total for r in rs)
                if sum(r.loop_open_total for r in rs) else None
            ),
            "prop_accuracy": (
                sum(r.prop_hits for r in rs) / sum(r.prop_total for r in rs)
                if sum(r.prop_total for r in rs) else None
            ),
            "locked_fact_rate": (
                sum(r.locked_fact_hits for r in rs) / sum(r.locked_fact_total for r in rs)
                if sum(r.locked_fact_total for r in rs) else None
            ),
            "fact_source_rate": (
                sum(r.fact_source_hits for r in rs) / sum(r.fact_source_total for r in rs)
                if sum(r.fact_source_total for r in rs) else None
            ),
            "replay_consistent": all(r.replay_consistent for r in rs),
            "cross_case_leaks": sum(r.cross_case_leaks for r in rs),
        }
    return {"groups": list(groups), "by_group": by_group,
            "case_results": [r.__dict__ | {"violations": r.violations,
                                           "loop_f1": r.loop_f1}
                             for r in results]}
