"""对话记忆评测(M-01)——MEMORY_IMPLEMENTATION_PLAN §3。

数据集契约、四层评分(写入/召回/使用/成本)、结构化目标组门槛、
当前实现的基线缺口、/agent/turns 生产链路摘要触发探针。
全部确定性(FakeLLM 语义抽取器),不依赖 Docker。
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from tests.evals.memory_harness import (
    evaluate_dialogue_dataset,
    load_dialogue_cases,
)

_DATASET = load_dialogue_cases()
_SUMMARY = evaluate_dialogue_dataset(_DATASET)


# ========================================================================
# 数据集契约(CI)
# ========================================================================


@pytest.mark.contract
class TestDialogueDatasetContract:
    def test_at_least_30_cases(self) -> None:
        assert len(_DATASET["cases"]) >= 30

    def test_covers_all_length_buckets(self) -> None:
        buckets = {c["length_bucket"] for c in _DATASET["cases"]}
        assert buckets == {24, 48, 96, 192}
        for bucket in buckets:
            assert sum(1 for c in _DATASET["cases"]
                       if c["length_bucket"] == bucket) >= 6

    def test_covers_required_tags(self) -> None:
        tags = {t for c in _DATASET["cases"] for t in c["tags"]}
        assert {"preference", "veto", "revision", "open_question",
                "cross_project", "chitchat"} <= tags

    def test_core_facts_outside_recent_window_at_96_plus(self) -> None:
        """96/192 档:改口与核心约束必须落在摘要区(离开短期窗口)。"""
        window = int(_DATASET["window"])
        for case in _DATASET["cases"]:
            if case["length_bucket"] < 96:
                continue
            recent_from = case["message_count"] - window + 1
            assert case["expected"]["revision_position"] < recent_from, case["id"]

    def test_expectation_markers_unique_per_case(self) -> None:
        for case in _DATASET["cases"]:
            exp = case["expected"]
            markers = (exp["superseded_markers"] + exp["veto_markers"]
                       + exp["constraint_markers"] + exp["open_question_markers"])
            for m in exp["latest_facts"]:
                assert m["marker"] not in markers or m["marker"] in exp[
                    "constraint_markers"], case["id"]
            # 最新事实标记不得与作废标记相同
            for f in exp["latest_facts"]:
                assert f["marker"] not in exp["superseded_markers"], case["id"]


# ========================================================================
# 写入层:覆盖语义
# ========================================================================


@pytest.mark.unit
class TestWriteLayer:
    def test_structured_coverage_continuous_and_complete(self) -> None:
        for r in _SUMMARY["case_results"]:
            if r["group"] != "structured" or not r["covered"]:
                continue
            # 覆盖区间连续
            for i in range(len(r["covered"]) - 1):
                assert r["covered"][i][1] + 1 == r["covered"][i + 1][0]
            assert r["coverage_complete"], r["case_id"]

    def test_current_group_keeps_only_latest_segment(self) -> None:
        """当前实现读取只保留最后一段——这是被量化的缺口,不是回归。"""
        for r in _SUMMARY["case_results"]:
            if r["group"] == "current" and len(r["covered"]) > 1:
                assert r["segments_kept"] == 1


# ========================================================================
# 召回层:目标门槛(structured)与基线缺口(current)
# ========================================================================


@pytest.mark.unit
class TestRecallGates:
    def test_structured_meets_design_gates(self) -> None:
        """MEMORY_DESIGN §10.1 门槛(结构化目标实现必须达标)。"""
        g = _SUMMARY["by_group"]["structured"]
        assert g["cross_project_leak_count"] == 0
        assert g["latest_fact_rate"] is not None and g["latest_fact_rate"] >= 0.95
        assert g["veto_recall"] is not None and g["veto_recall"] >= 0.95
        assert g["fabrication_count"] <= _SUMMARY["by_group"]["structured"]["cases"] * 0.01
        bucket96 = _SUMMARY["by_bucket"]["structured"]["96"]
        assert bucket96["constraint_recall"] is not None
        assert bucket96["constraint_recall"] >= 0.90
        bucket192 = _SUMMARY["by_bucket"]["structured"]["192"]
        assert bucket192["constraint_recall"] is not None
        assert bucket192["constraint_recall"] >= 0.90

    def test_current_baseline_gap_is_measurable(self) -> None:
        """基线缺口:分段摘要只读最新一段,长对话约束召回坍缩(锁定基线,
        M-02 修复后此断言将转绿——它验证的是评测能测出缺口)。"""
        bucket96 = _SUMMARY["by_bucket"]["current"]["96"]
        assert bucket96["constraint_recall"] is not None
        assert bucket96["constraint_recall"] < 0.5
        structured96 = _SUMMARY["by_bucket"]["structured"]["96"]
        assert structured96["constraint_recall"] > bucket96["constraint_recall"]

    def test_recent_only_cannot_recall_vetoes(self) -> None:
        g = _SUMMARY["by_group"]["recent_only"]
        assert g["veto_recall"] == 0.0

    def test_no_group_leaks_cross_project(self) -> None:
        for g in _SUMMARY["by_group"].values():
            assert g["cross_project_leak_count"] == 0

    def test_no_group_fabricates(self) -> None:
        for g in _SUMMARY["by_group"].values():
            assert g["fabrication_count"] == 0


# ========================================================================
# 使用层与成本层
# ========================================================================


@pytest.mark.unit
class TestUseAndCostLayers:
    def test_structured_reaches_assembled_context(self) -> None:
        g = _SUMMARY["by_group"]["structured"]
        assert g["use_latest_rate"] == 1.0

    def test_manifest_consistent_for_all_groups(self) -> None:
        for g in _SUMMARY["by_group"].values():
            assert g["manifest_consistent"]

    def test_cost_saving_grows_with_length(self) -> None:
        """结构化记忆相对完整历史的节省随长度增加(96 档 ≥ 40%,
        192 档 ≥ 60%——短档结构性不可能高节省,窗口本身占满)。"""
        for bucket, min_saving in (("96", 0.40), ("192", 0.60)):
            saving = _SUMMARY["by_bucket"]["structured"][bucket][
                "token_saving_vs_full"]
            assert saving is not None and saving >= min_saving, bucket


# ========================================================================
# 确定性:同 seed 重跑一致
# ========================================================================


@pytest.mark.unit
class TestDeterminism:
    def test_same_seed_same_results(self) -> None:
        again = evaluate_dialogue_dataset(load_dialogue_cases())
        assert json.dumps(again, sort_keys=True) == json.dumps(_SUMMARY, sort_keys=True)


# ========================================================================
# 生产链路探针:/agent/turns 是否实际触发摘要(M-01 报告事实)
# ========================================================================


@pytest.mark.unit
class TestProductionWiringProbe:
    def test_agent_turns_service_has_no_summary_mount(self) -> None:
        """基线事实(锁定,供 MEMORY_EVAL_REPORT 引用):
        AgentCommandService / AgentActionLifecycle 默认构造的 MessageService
        无摘要与短期记忆挂载——/agent/turns 目前不触发摘要。
        M-02 统一 MessageService 工厂后此断言应更新为"已挂载"。"""
        from app.agents.base import BaseAgent
        from app.application.agent_action_lifecycle import AgentActionLifecycle
        from app.application.agent_command_service import AgentCommandService
        from app.application.conversation_service import MessageService
        from app.core.config import Settings
        from app.llm.fake import FakeLLM

        cmd_service = AgentCommandService(
            settings=Settings(app_env="test"),
            planner_agent=BaseAgent(name="planner", llm=FakeLLM()),
        )
        inner = cast(MessageService, cmd_service._message_service)
        assert inner._summary is None  # type: ignore[attr-defined]
        assert inner._short_term is None  # type: ignore[attr-defined]

        lifecycle_service = AgentActionLifecycle()
        inner2 = cast(MessageService, lifecycle_service._messages)
        assert inner2._summary is None  # type: ignore[attr-defined]

    def test_conversations_api_service_has_summary_mount(self) -> None:
        """对照:普通消息 API 的 MessageService 挂载了记忆(M-02 的目标
        是让所有真实消息入口共享同一构造方式)。"""
        from app.api.v1.conversations import _get_msg_service

        svc = _get_msg_service()
        assert svc._summary is not None  # type: ignore[attr-defined]
        assert svc._short_term is not None  # type: ignore[attr-defined]


# ========================================================================
# 报告产物契约(供 evaluate_memory.py 使用)
# ========================================================================


@pytest.mark.contract
class TestReportShape:
    def test_summary_has_group_and_bucket_breakdown(self) -> None:
        assert set(_SUMMARY["by_group"]) == {"none", "recent_only", "current",
                                             "structured"}
        for g in _SUMMARY["by_group"]:
            assert set(_SUMMARY["by_bucket"][g]) == {"24", "48", "96", "192"}


def get_summary() -> dict[str, Any]:
    """供脚本/其他测试复用的汇总入口。"""
    return cast(dict[str, Any], _SUMMARY)
