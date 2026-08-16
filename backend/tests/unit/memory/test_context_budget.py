"""ContextBuilder 完整化单元测试 (G-02).

验收覆盖：
- 不同任务上下文组成不同（分任务策略生效）；
- 任何构建结果都保留输出缓冲（current_target 永不截断）；
- 当前稿件超限抛 ContextTooLargeError 而非静默截断；
- rag_chunk_ids 从调用方回填 manifest；
- 边界 token 预算（保底 / 临界值）；
- TokenEstimator 可注入（默认字符比 1.5）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.context import (
    CharacterRatioEstimator,
    ContextSection,
    ContextTooLargeError,
    TokenEstimator,
    get_policy,
)
from app.memory.context_builder import ContextBuilder


def _make_sections(
    *,
    system_rules: str = "系统规则",
    user_request: str = "用户请求内容" * 60,          # 360 字符
    story_bible_outline: str = "设定内容" * 120,      # 480 字符
    previous_summary_continuity: str = "连续性内容" * 50,
    rag_fragments: str = "参考资料内容" * 50,
    current_target: str = "",
) -> dict[str, Any]:
    return {
        "system_rules": system_rules,
        "user_request": user_request,
        "story_bible_outline": story_bible_outline,
        "previous_summary_continuity": previous_summary_continuity,
        "rag_fragments": rag_fragments,
        "current_target": current_target,
    }


# ========================================================================
# 不同任务上下文组成不同
# ========================================================================


class TestTaskPolicies:
    """G-02 验收：不同任务上下文组成不同。"""

    def test_requirement_vs_writer_allocation_differs(self) -> None:
        """requirement 保用户请求；writer 保设定——裁剪结果不同。"""
        builder = ContextBuilder(budget_tokens=2000)
        sections = _make_sections()

        _, req_manifest = builder.build_for("requirement", **sections)
        _, wr_manifest = builder.build_for("writer", **sections)

        # requirement：user_request 权重高（0.35）→ 不截断；story 权重低 → 截断
        assert "user_request" not in req_manifest.sections_truncated
        assert "story_bible_outline" in req_manifest.sections_truncated
        # writer：story 权重高（0.25）→ 不截断；user_request 权重低 → 截断
        assert "user_request" in wr_manifest.sections_truncated
        assert "story_bible_outline" not in wr_manifest.sections_truncated

        # manifest 记录各自任务类型
        assert req_manifest.task == "requirement"
        assert wr_manifest.task == "writer"

    def test_all_six_policies_defined(self) -> None:
        """六类任务均有独立策略，且权重合法。"""
        for task in (
            "requirement", "story_bible", "outline",
            "writer", "evaluator", "reviser",
        ):
            policy = get_policy(task)
            assert policy.task.value == task
            assert policy.ratios, f"{task} 策略缺少权重"
            assert ContextSection.CURRENT_TARGET not in policy.ratios
            assert all(v >= 0 for v in policy.ratios.values())

    def test_unknown_task_falls_back_to_writer(self) -> None:
        """未知任务回退 writer 策略（防御），不抛异常。"""
        builder = ContextBuilder(budget_tokens=2000)
        _, manifest = builder.build_for("bogus_task", **_make_sections())
        assert manifest.task == "writer"

    def test_legacy_build_is_writer_policy(self) -> None:
        """C-06 build() 入口等价于 writer 策略（G-02 保留）。"""
        builder = ContextBuilder(budget_tokens=2000)
        _, manifest = builder.build(**_make_sections())
        assert manifest.task == "writer"


# ========================================================================
# 输出缓冲保留 + current_target 不静默截断
# ========================================================================


class TestOutputBuffer:
    """G-02 验收：任何构建结果都保留输出缓冲。"""

    def test_current_target_preserved_when_others_cut(self) -> None:
        """辅助段被裁剪时 current_target 完整保留。"""
        builder = ContextBuilder(budget_tokens=1000)
        current = "关键场景内容必须完整" * 100  # 400 字符 → 约 267 tokens
        sections = _make_sections(
            system_rules="规则" * 200,
            story_bible_outline="设定" * 300,
            rag_fragments="RAG" * 300,
            current_target=current,
        )

        text, manifest = builder.build_for("writer", **sections)

        assert "current_target" in manifest.sections_used
        assert "current_target" not in manifest.sections_truncated
        assert "current_target" not in manifest.sections_cut
        assert not manifest.has_current_target_cut()
        # 组装文本包含完整 current_target（其他段可被截断，但当前稿件必须完整）
        assert current in text
        # 其他段确实被裁剪了（预算确实吃紧）
        assert manifest.sections_truncated

    def test_empty_current_target_ok(self) -> None:
        """current_target 为空时不影响构建（其他段正常组装）。"""
        builder = ContextBuilder(budget_tokens=1000)
        _, manifest = builder.build_for(
            "writer", **_make_sections(
                system_rules="规则", user_request="请求",
                story_bible_outline="设定",
                previous_summary_continuity="", rag_fragments="",
                current_target="",
            )
        )
        assert "system_rules" in manifest.sections_used
        assert manifest.estimated_tokens > 0


class TestContextTooLarge:
    """G-02 验收：当前稿件超限抛异常而非静默截断。"""

    def test_raises_when_current_target_exceeds_budget(self) -> None:
        """current_target 单独超过总预算 → ContextTooLargeError。"""
        builder = ContextBuilder(budget_tokens=500)
        sections = _make_sections(current_target="内容" * 1000)  # 2000 字符

        with pytest.raises(ContextTooLargeError) as exc_info:
            builder.build_for("writer", **sections)

        assert exc_info.value.code == "CONTEXT_TOO_LARGE"
        assert exc_info.value.status_code == 413

    def test_just_below_budget_succeeds(self) -> None:
        """current_target 恰好能放下时正常构建（临界边界）。"""
        # 500 tokens * 1.5 = 750 字符上限；600 字符可放下
        builder = ContextBuilder(budget_tokens=500)
        _, manifest = builder.build_for(
            "writer",
            system_rules="", user_request="", story_bible_outline="",
            previous_summary_continuity="", rag_fragments="",
            current_target="场" * 600,
        )
        assert manifest.estimated_tokens > 0
        assert "current_target" in manifest.sections_used


# ========================================================================
# rag_chunk_ids 回填 + 估算记录
# ========================================================================


class TestRagBackfill:
    """G-02：rag_chunk_ids 从 RetrievalResult 回填 manifest。"""

    def test_rag_chunk_ids_backfilled(self) -> None:
        builder = ContextBuilder(budget_tokens=4000)
        chunks = ["uuid-1", "uuid-2", "uuid-3"]
        _, manifest = builder.build_for(
            "writer",
            system_rules="", user_request="", story_bible_outline="",
            previous_summary_continuity="", rag_fragments="参考",
            rag_chunk_ids=chunks,
            current_target="目标",
        )
        assert manifest.rag_chunk_ids == chunks

    def test_rag_chunk_ids_default_empty(self) -> None:
        builder = ContextBuilder(budget_tokens=4000)
        _, manifest = builder.build_for(
            "writer",
            system_rules="", user_request="", story_bible_outline="",
            previous_summary_continuity="", rag_fragments="参考",
            current_target="目标",
        )
        assert manifest.rag_chunk_ids == []


class TestManifestEstimates:
    """G-02：context_manifest 保存 token 估算与裁剪原因。"""

    def test_section_estimates_recorded(self) -> None:
        builder = ContextBuilder(budget_tokens=4000)
        sections = _make_sections(current_target="目标" * 20)
        _, manifest = builder.build_for("writer", **sections)

        assert isinstance(manifest.section_estimates, dict)
        for key in manifest.sections_used:
            assert manifest.section_estimates[key] > 0

    def test_truncation_reasons_recorded(self) -> None:
        builder = ContextBuilder(budget_tokens=800)
        sections = _make_sections(
            system_rules="规则" * 100,
            user_request="请求" * 100,
            story_bible_outline="设定" * 100,
        )
        _, manifest = builder.build_for("writer", **sections)

        assert manifest.sections_truncated
        assert manifest.truncation_reasons
        assert all(isinstance(r, str) for r in manifest.truncation_reasons)

    def test_truncation_reasons_mention_section(self) -> None:
        """裁剪原因含被裁分段名（可审计）。"""
        builder = ContextBuilder(budget_tokens=600)
        _, manifest = builder.build_for(
            "writer",
            system_rules="规则" * 100,
            user_request="", story_bible_outline="", previous_summary_continuity="",
            rag_fragments="", current_target="目标",
        )
        if manifest.sections_truncated:
            assert any(
                manifest.sections_truncated[0] in r
                for r in manifest.truncation_reasons
            )


# ========================================================================
# TokenEstimator 可注入
# ========================================================================


class TestTokenEstimator:
    """G-02：token estimator adapter 默认与注入。"""

    def test_default_character_ratio(self) -> None:
        est = CharacterRatioEstimator()
        # 1 token ≈ 1.5 字符（保守）
        assert est.estimate("") == 0
        assert est.estimate("123") == 2  # 3 / 1.5 = 2

    def test_injected_estimator_used(self) -> None:
        """自定义字符比注入后，估算随之变化。"""

        class _WideEstimator(TokenEstimator):
            """3 字符/token。"""

            @property
            def chars_per_token(self) -> float:
                return 3.0

            def estimate(self, text: str) -> int:
                return len(text) // 3 if text else 0

        builder = ContextBuilder(
            budget_tokens=4000, token_estimator=_WideEstimator()
        )
        _, manifest = builder.build_for(
            "writer",
            system_rules="", user_request="", story_bible_outline="",
            previous_summary_continuity="", rag_fragments="",
            current_target="目标内容",
        )
        assert manifest.estimated_tokens == len("## 当前任务目标\n\n目标内容") // 3

    def test_invalid_ratio_rejected(self) -> None:
        with pytest.raises(ValueError):
            CharacterRatioEstimator(chars_per_token=0)


# ========================================================================
# 边界 token 预算
# ========================================================================


class TestBoundaryBudget:
    """边界预算：小预算尽力组装，大预算不浪费。"""

    def test_tiny_budget_still_keeps_current_target(self) -> None:
        builder = ContextBuilder(budget_tokens=100)
        _, manifest = builder.build_for(
            "writer",
            system_rules="规则" * 50,
            user_request="请求" * 50,
            story_bible_outline="设定" * 50,
            previous_summary_continuity="连续性" * 50,
            rag_fragments="RAG" * 50,
            current_target="目标",  # 极小 → 总能放下
        )
        assert "current_target" in manifest.sections_used
        assert "current_target" not in manifest.sections_truncated
        assert manifest.budget_remaining >= 0

    def test_large_budget_no_truncation(self) -> None:
        builder = ContextBuilder(budget_tokens=100000)
        _, manifest = builder.build_for("writer", **_make_sections())
        assert manifest.sections_truncated == []
        assert manifest.sections_cut == []
        assert manifest.budget_remaining >= 0
        assert manifest.estimated_tokens <= manifest.budget_total

    def test_budget_remaining_equals_total_minus_estimate(self) -> None:
        builder = ContextBuilder(budget_tokens=4000)
        _, manifest = builder.build_for(
            "writer",
            system_rules="规则", user_request="", story_bible_outline="",
            previous_summary_continuity="", rag_fragments="",
            current_target="目标",
        )
        assert manifest.budget_remaining == max(
            0, manifest.budget_total - manifest.estimated_tokens
        )
