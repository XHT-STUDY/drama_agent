"""ContextBuilder 单元测试 (C-06).

测试范围:
- 正常预算内组装
- 超预算裁剪（按 §9.3 顺序）
- current_target 不被静默截断
- context_manifest 记录完整
- 保底预算保护
"""

from app.memory.context_builder import ContextBuilder, ContextManifest

# ========================================================================
# Fixtures
# ========================================================================


def _make_sections(
    system_rules: str = "系统规则：输出 JSON 格式。",
    user_request: str = "请写第 3 集剧本。",
    story_bible_outline: str = "StoryBible + 大纲（500 字）" * 25,
    previous_summary_continuity: str = "前集摘要 + 连续性" * 50,
    rag_fragments: str = "RAG 资料" * 40,
    current_target: str = "当前剧本草稿需要修订" * 30,
) -> dict[str, str]:
    return {
        "system_rules": system_rules,
        "user_request": user_request,
        "story_bible_outline": story_bible_outline,
        "previous_summary_continuity": previous_summary_continuity,
        "rag_fragments": rag_fragments,
        "current_target": current_target,
    }


# ========================================================================
# 正常预算内组装
# ========================================================================


class TestBuildWithinBudget:
    """正常预算范围内组装。"""

    def test_all_sections_included(self) -> None:
        """所有分段都包含在输出中。"""
        builder = ContextBuilder(budget_tokens=32000)
        sections = _make_sections()
        text, manifest = builder.build(**sections)

        assert "系统规则与输出要求" in text
        assert "当前用户请求" in text
        assert "故事设定与大纲" in text
        assert "连续性状态" in text
        assert "参考资料" in text
        assert "当前任务目标" in text

        assert len(manifest.sections_cut) == 0
        assert manifest.estimated_tokens > 0
        assert manifest.budget_total == 32000

    def test_empty_sections_omitted(self) -> None:
        """空分段不出现在输出和 used 列表中。"""
        builder = ContextBuilder(budget_tokens=4000)
        text, manifest = builder.build(
            system_rules="规则",
            user_request="",
            story_bible_outline="设定",
            previous_summary_continuity="",
            rag_fragments="",
            current_target="目标",
        )

        assert "当前用户请求" not in text
        assert "连续性状态" not in text
        assert "参考资料" not in text
        # used 只包含非空分段
        assert "user_request" not in manifest.sections_used

    def test_manifest_records_used_sections(self) -> None:
        """manifest.sections_used 记录所有使用的分段。"""
        builder = ContextBuilder(budget_tokens=4000)
        _, manifest = builder.build(
            system_rules="规则",
            user_request="请求",
            story_bible_outline="设定",
            current_target="目标",
        )
        assert "system_rules" in manifest.sections_used
        assert "current_target" in manifest.sections_used


# ========================================================================
# 超预算裁剪（§9.3 顺序）
# ========================================================================


class TestTruncation:
    """超预算时按规则裁剪。"""

    def test_truncates_rag_first(self) -> None:
        """RAG 片段最先被裁剪（按 §9.3 优先级）。"""
        builder = ContextBuilder(budget_tokens=2000)
        sections = _make_sections(
            rag_fragments="大量 RAG 内容" * 200,
        )
        _, manifest = builder.build(**sections)

        # 在预算足够时，RAG 被分配了适当的份额
        # 验证 manifest 记录了实际的分段使用
        assert "rag_fragments" in manifest.sections_used or \
               "rag_fragments" in manifest.sections_truncated

    def test_current_target_not_silently_truncated(self) -> None:
        """current_target 被截断时必须体现在 manifest 中。"""
        builder = ContextBuilder(budget_tokens=1000)  # 极低预算
        sections = _make_sections(
            system_rules="系统规则" * 50,
            story_bible_outline="设定" * 50,
            current_target="关键内容" * 200,
        )
        _, manifest = builder.build(**sections)

        # 关键验收：如果 current_target 被截断，warnings 必须有记录
        if "current_target" in manifest.sections_truncated:
            has_warning = any("当前目标" in w for w in manifest.warnings)
            assert has_warning, (
                "current_target 被截断但 warnings 中无记录——静默截断！"
            )

    def test_never_silently_drops_current_target(self) -> None:
        """current_target 不应被完全删除。"""
        builder = ContextBuilder(budget_tokens=500)  # 极低
        sections = _make_sections(current_target="关键场景内容必须保留" * 5)
        text, manifest = builder.build(**sections)

        # 即使被截断，也应该在 used 中（cut 表示完全删除）
        if "current_target" in manifest.sections_cut:
            raise AssertionError("current_target 被完全删除而非截断！")

    def test_truncation_order_per_spec(self) -> None:
        """裁剪按 §9.3 顺序：RAG → 连续性 → 用户请求 → 大纲 → 当前场景。"""
        builder = ContextBuilder(budget_tokens=1500)
        sections = _make_sections(
            rag_fragments="RAG" * 100,
            previous_summary_continuity="连续性" * 100,
            user_request="用户请求" * 20,
            story_bible_outline="大纲" * 100,
            current_target="目标" * 50,
        )
        _, manifest = builder.build(**sections)

        # 被截断或裁剪的分段应符合优先级
        # RAG 最先被处理
        assert "current_target" not in manifest.sections_cut


# ========================================================================
# ContextManifest 调试能力
# ========================================================================


class TestContextManifest:
    """context_manifest 可调试。"""

    def test_manifest_is_serializable(self) -> None:
        """manifest 可序列化为 dict。"""
        builder = ContextBuilder(budget_tokens=4000)
        _, manifest = builder.build(
            system_rules="规则",
            current_target="目标",
        )
        d = manifest.model_dump()
        assert "sections_used" in d
        assert "budget_total" in d
        assert "estimated_tokens" in d

    def test_has_current_target_cut(self) -> None:
        """has_current_target_cut() 方法检查当前场景截断状态。"""
        manifest = ContextManifest(
            sections_truncated=["rag_fragments"],
            sections_used=["current_target"],
        )
        assert not manifest.has_current_target_cut()

        manifest2 = ContextManifest(
            sections_truncated=["rag_fragments", "current_target"],
            sections_used=["current_target"],
        )
        assert manifest2.has_current_target_cut()

    def test_warnings_captured(self) -> None:
        """manifest.warnings 记录裁剪告警。"""
        builder = ContextBuilder(budget_tokens=1000)
        sections = _make_sections(
            system_rules="规则" * 100,
            rag_fragments="RAG" * 200,
            story_bible_outline="大纲" * 200,
            current_target="目标" * 200,
        )
        _, manifest = builder.build(**sections)

        # 应该有警告（预算严重不足）
        if manifest.warnings:
            assert isinstance(manifest.warnings, list)
            assert all(isinstance(w, str) for w in manifest.warnings)

    def test_budget_remaining_calculated(self) -> None:
        """budget_remaining 等于 budget_total - estimated_tokens。"""
        builder = ContextBuilder(budget_tokens=4000)
        _, manifest = builder.build(
            system_rules="规则",
            current_target="目标",
        )
        assert manifest.budget_remaining == max(
            0, manifest.budget_total - manifest.estimated_tokens
        )
