"""剧情连续性评测(M-01)——MEMORY_IMPLEMENTATION_PLAN §3。

数据集契约、知识边界 / 伏笔 / 道具 / 锁定事实 / 来源 / 重放的确定性断言。
四组:none / recent_only / current(生产标题摘要路径)/ structured(目标)。
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import pytest

from tests.evals.memory_harness import (
    evaluate_story_dataset,
    load_story_cases,
)

_DATASET = load_story_cases()


@lru_cache(maxsize=1)
def _summary() -> dict[str, Any]:
    """全量评测结果(进程内缓存;import 不触发执行)。"""
    return evaluate_story_dataset(_DATASET)


_SUMMARY = _summary()


# ========================================================================
# 数据集契约(CI)
# ========================================================================


@pytest.mark.contract
class TestStoryDatasetContract:
    def test_at_least_10_cases_with_10_episodes(self) -> None:
        assert len(_DATASET["cases"]) >= 10
        for case in _DATASET["cases"]:
            assert len(case["episodes"]) == 10, case["id"]
            assert [e["episode_number"] for e in case["episodes"]] == list(range(1, 11))

    def test_covers_required_dimensions(self) -> None:
        """人物知识 / 伏笔 / 道具 / 时间线 / 锁定事实 / 作者计划全覆盖。"""
        has_knowledge = any(e["typed_delta"].get("knowledge")
                            for c in _DATASET["cases"] for e in c["episodes"])
        has_loops = any(e["typed_delta"].get("loops_introduced")
                        or e["typed_delta"].get("loops_resolved")
                        for c in _DATASET["cases"] for e in c["episodes"])
        has_props = any(e["typed_delta"].get("props")
                        for c in _DATASET["cases"] for e in c["episodes"])
        has_timeline = any(e["typed_delta"].get("timeline_events")
                           for c in _DATASET["cases"] for e in c["episodes"])
        assert has_knowledge and has_loops and has_props and has_timeline
        assert all(c["locked_facts"] for c in _DATASET["cases"])
        assert all(c.get("future_plans") for c in _DATASET["cases"])

    def test_every_case_has_secret_knowledge_boundary(self) -> None:
        """每组都有"作者已知 / 角色未知"的知识边界事实。"""
        for case in _DATASET["cases"]:
            later_learned = [
                k for e in case["episodes"]
                for k in e["typed_delta"].get("knowledge", []) if k["learned"]
            ]
            assert later_learned, case["id"]
            assert len(later_learned) >= 2, case["id"]

    def test_loop_lifecycle_covers_resolved_and_open(self) -> None:
        resolved_cases = [
            c for c in _DATASET["cases"]
            if any(e["typed_delta"].get("loops_resolved") for e in c["episodes"])
        ]
        assert len(resolved_cases) >= 8

    def test_prop_transfer_chains(self) -> None:
        props = [
            (c["id"], p["prop_id"])
            for c in _DATASET["cases"] for e in c["episodes"]
            for p in e["typed_delta"].get("props", [])
        ]
        assert len(props) >= 10
        # 至少一个道具多次易主
        counts: dict[str, int] = {}
        for _, pid in props:
            counts[pid] = counts.get(pid, 0) + 1
        assert any(v >= 2 for v in counts.values())


# ========================================================================
# 结构化目标组门槛(MEMORY_DESIGN §10.2)
# ========================================================================


@pytest.mark.unit
class TestStructuredGates:
    def test_knowledge_boundary_zero_leak(self) -> None:
        g = _SUMMARY["by_group"]["structured"]
        assert g["knowledge_leak"] == 0
        assert g["future_plan_leak"] == 0

    def test_no_missing_required_knowledge(self) -> None:
        g = _SUMMARY["by_group"]["structured"]
        assert g["knowledge_missing"] == 0

    def test_locked_facts_deterministic(self) -> None:
        g = _SUMMARY["by_group"]["structured"]
        assert g["locked_fact_rate"] == 1.0

    def test_fact_sources_correct(self) -> None:
        g = _SUMMARY["by_group"]["structured"]
        assert g["fact_source_rate"] == 1.0

    def test_state_replay_consistent(self) -> None:
        assert _SUMMARY["by_group"]["structured"]["replay_consistent"]

    def test_loop_recall_f1_gate(self) -> None:
        g = _SUMMARY["by_group"]["structured"]
        assert g["loop_f1_avg"] is not None and g["loop_f1_avg"] >= 0.90
        assert g["loop_open_recall"] is not None and g["loop_open_recall"] >= 0.90

    def test_prop_accuracy_full(self) -> None:
        assert _SUMMARY["by_group"]["structured"]["prop_accuracy"] == 1.0

    def test_violation_reduction_over_50_percent(self) -> None:
        g = _SUMMARY["by_group"]["structured"]
        assert g["violation_reduction_vs_none"] is not None
        assert g["violation_reduction_vs_none"] >= 0.50

    def test_cross_case_isolation(self) -> None:
        for g in _SUMMARY["by_group"].values():
            assert g["cross_case_leaks"] == 0


# ========================================================================
# 当前实现基线(M-01 记录,不做门槛断言)
# ========================================================================


@pytest.mark.unit
class TestCurrentBaseline:
    def test_current_path_loses_knowledge_and_loops(self) -> None:
        """基线事实:生产 write_episode 的标题摘要路径不携带角色知识与
        伏笔回收(M-03/M-04 的改造对象)。"""
        g = _SUMMARY["by_group"]["current"]
        assert g["knowledge_missing"] > 0
        assert g["loop_f1_avg"] is not None and g["loop_f1_avg"] < 0.5
        # 锁定事实仍可用(ContinuityState 从 StoryBible 复制)
        assert g["locked_fact_rate"] == 1.0

    def test_recent_only_barely_improves_over_none(self) -> None:
        none_g = _SUMMARY["by_group"]["none"]
        recent = _SUMMARY["by_group"]["recent_only"]
        assert recent["violations"] < none_g["violations"]
        assert (recent["violation_reduction_vs_none"] or 0) < 0.2


# ========================================================================
# 确定性
# ========================================================================


@pytest.mark.unit
class TestDeterminism:
    def test_same_seed_same_results(self) -> None:
        again = evaluate_story_dataset(load_story_cases())
        assert json.dumps(again, sort_keys=True) == json.dumps(
            _SUMMARY, sort_keys=True)
