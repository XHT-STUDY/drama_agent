"""F-04 Diff 纯函数单元测试。

对照 F-04 验收条件：
① 中文文本 Diff 不乱码
② 可识别新增/删除/修改场景
③ A/B 颠倒时方向正确
④ 跨项目查询拒绝（由集成测试覆盖）
⑤ change_ratio 被 Revision Gate 使用（check_change_ratio 判定）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.diff import DiffLineStats, SceneDiffSummary, ScriptDiff
from app.domain.script import DialogueLine, Scene, ScriptDraft
from app.tools.diff import (
    MAX_DIFF_LINE_CHANGES,
    ScriptDiffTool,
    check_change_ratio,
    diff_lines,
    diff_script_drafts,
    diff_texts,
)

_GOLDEN = Path(__file__).resolve().parents[2] / "golden"


def _load_golden(name: str) -> dict[str, Any]:
    with open(_GOLDEN / name, encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


# ---- 测试数据构造 ----

def _script_a() -> ScriptDraft:
    """原稿（ep1，2 场：青训营更衣室 / 城市公园足球场）。"""
    return ScriptDraft.model_validate(_load_golden("script_draft_valid.json"))


def _script_b() -> ScriptDraft:
    """修订稿（F-02 golden 的 script_draft，同集同场数）。"""
    revised = _load_golden("revised_episode_football.json")
    return ScriptDraft.model_validate(revised["script_draft"])


def _renumber(scenes: list[Scene]) -> list[Scene]:
    """按出现顺序重排场景号（满足 ScriptDraft 连续编号约束）。"""
    return [s.model_copy(update={"scene_number": i + 1}) for i, s in enumerate(scenes)]


def _with_inserted_scene(draft: ScriptDraft) -> ScriptDraft:
    """在原稿末尾追加一场全新场景（原场景编号保持不变）。"""
    new_scene = Scene(
        scene_number=len(draft.scenes) + 1,
        location="新增训练场",
        time_of_day="日",
        characters=["新角色"],
        action="这是插入的全新场景。教练带着林峰来到新场地。",
        dialogue=[DialogueLine(speaker="新角色", text="这里以后就是你的主场。")],
    )
    scenes = [*draft.scenes, new_scene]
    return draft.model_copy(deep=True, update={"scenes": scenes})


def _with_inserted_scene_front(draft: ScriptDraft) -> ScriptDraft:
    """在原稿开头插入一场全新场景（触发后续编号位移）。"""
    new_scene = Scene(
        scene_number=1,
        location="新增训练场",
        time_of_day="日",
        characters=["新角色"],
        action="这是插入的全新场景。教练带着林峰来到新场地。",
        dialogue=[DialogueLine(speaker="新角色", text="这里以后就是你的主场。")],
    )
    scenes = _renumber([new_scene, *draft.scenes])
    return draft.model_copy(deep=True, update={"scenes": scenes})


def _without_scene(draft: ScriptDraft, location: str) -> ScriptDraft:
    """删除指定地点的场景，并重排编号。"""
    scenes = _renumber([s for s in draft.scenes if s.location != location])
    return draft.model_copy(deep=True, update={"scenes": scenes})


def _with_modified_scene(draft: ScriptDraft, scene_number: int) -> ScriptDraft:
    """修改指定场景的 action 文本（局部改动）。"""
    scenes = [
        s.model_copy(update={"action": s.action + " 林峰攥紧了拳头。"})
        if s.scene_number == scene_number
        else s
        for s in draft.scenes
    ]
    return draft.model_copy(deep=True, update={"scenes": scenes})


def _changed_line_count(result: ScriptDiff) -> int:
    """变更行总数（added+removed+modified）。"""
    return result.stats.added_lines + result.stats.removed_lines + result.stats.modified_lines


# ---- 测试 ----

class TestChineseNoGarble:
    """验收①：中文文本 Diff 不乱码。"""

    def test_modified_lines_preserve_chinese(self) -> None:
        result = diff_script_drafts(_script_a(), _script_b())
        scene = next(s for s in result.scene_changes if s.change_type == "modified")
        assert scene.line_changes, "修改场景应有行级明细"
        texts = [
            c.old_text or c.new_text
            for c in scene.line_changes
            if c.change_type in ("added", "removed", "modified")
        ]
        assert all(t and any("一" <= ch <= "鿿" for ch in t) for t in texts)
        # 修订稿新增了对白（含中文）
        assert any("陈浩" in (t or "") for t in texts) or any(
            (c.new_text or "").strip() for c in scene.line_changes if c.change_type == "added"
        )

    def test_json_round_trip_no_unicode_escape(self) -> None:
        result = diff_script_drafts(_script_a(), _script_b())
        dumped = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
        assert "\\u" not in dumped, "中文不应被转义为 \\uXXXX"
        assert "青训营更衣室" in dumped
        # 纯函数输出不携带 Artifact 元数据（由 diff_service 填充）
        assert result.episode_number is None
        assert result.from_artifact_id is None
        # 往返无损
        restored = json.loads(dumped)
        assert restored == result.model_dump(mode="json")

    def test_line_mode_chinese_round_trip(self) -> None:
        result = diff_texts(_script_a().plain_text, _script_b().plain_text)
        dumped = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
        assert "\\u" not in dumped
        assert any(c.new_text and "球" in c.new_text for c in result.line_changes)


class TestIdenticalVersions:
    """相同版本 → 全 unchanged、change_ratio=0。"""

    def test_identical_script_diff(self) -> None:
        result = diff_script_drafts(_script_a(), _script_a())
        assert result.mode == "scene"
        assert result.change_ratio == 0.0
        assert result.scene_summary.unchanged == 2
        assert result.scene_summary.added == 0
        assert result.scene_summary.removed == 0
        assert result.scene_summary.modified == 0
        assert all(s.change_type == "unchanged" for s in result.scene_changes)
        assert result.stats.added_lines == result.stats.removed_lines == 0
        assert result.stats.modified_lines == 0


class TestSceneChanges:
    """验收②：可识别新增/删除/修改场景。"""

    def test_added_scene_detected(self) -> None:
        a = _script_a()
        b = _with_inserted_scene(a)
        result = diff_script_drafts(a, b)
        assert result.scene_summary.added == 1
        assert result.scene_summary.removed == 0, "编号位移不应误判原场景为删除"
        added = [s for s in result.scene_changes if s.change_type == "added"]
        assert len(added) == 1
        assert added[0].location == "新增训练场"
        # 原两场应被对齐为 unchanged（插入未修改原场景）
        assert result.scene_summary.unchanged == 2

    def test_removed_scene_detected(self) -> None:
        base = _with_inserted_scene(_script_a())  # 3 场：更衣室(1)/公园(2)/新增训练场(3)
        removed = _without_scene(base, "城市公园足球场")
        result = diff_script_drafts(base, removed)
        assert result.scene_summary.removed == 1
        assert result.scene_summary.added == 0
        removed_scenes = [s for s in result.scene_changes if s.change_type == "removed"]
        assert len(removed_scenes) == 1
        assert removed_scenes[0].location == "城市公园足球场"
        # 幸存场景仍匹配（更衣室未重编号 → unchanged；新增训练场重编号 → 仅 header modified）
        unchanged = [s for s in result.scene_changes if s.change_type == "unchanged"]
        assert any(s.location == "青训营更衣室" for s in unchanged)
        assert result.scene_summary.removed + result.scene_summary.unchanged == 2

    def test_modified_scene_detected(self) -> None:
        a = _script_a()
        b = _with_modified_scene(a, 1)
        result = diff_script_drafts(a, b)
        assert result.scene_summary.modified == 1
        assert result.scene_summary.unchanged == 1
        modified = next(s for s in result.scene_changes if s.change_type == "modified")
        assert modified.old_scene_number == 1
        assert modified.new_scene_number == 1
        assert modified.modified_lines > 0
        # 未改动的场景不带行明细变化
        unchanged = next(s for s in result.scene_changes if s.change_type == "unchanged")
        assert unchanged.added_lines == unchanged.removed_lines == unchanged.modified_lines == 0

    def test_scene_renumbering_no_false_add_remove(self) -> None:
        """中间插入导致后续场景编号位移，仍应对齐而非误判 removed+added。

        场景号是剧本内容的一部分（plain_text 含【第N场】），
        重编号场景仅因头部行变化而标记 modified，其余行不变。
        """
        a = _script_a()
        b = _with_inserted_scene_front(a)
        result = diff_script_drafts(a, b)
        locations = [s.location for s in result.scene_changes]
        assert locations == ["新增训练场", "青训营更衣室", "城市公园足球场"]
        assert [s.change_type for s in result.scene_changes] == [
            "added", "modified", "modified",
        ]
        assert result.scene_summary.removed == 0
        # 原场景仅头部场景号位移：恰好 1 行 modified，且无增删行
        for s in result.scene_changes[1:]:
            assert s.modified_lines == 1
            assert s.added_lines == 0 and s.removed_lines == 0
            header = next(
                c for c in s.line_changes if c.change_type == "modified"
            )
            assert header.old_text and header.new_text
            assert "场" in header.old_text and "场" in header.new_text


class TestLineLevelCounting:
    """行级 added/removed/modified 三计数。"""

    def test_replace_block_pairing(self) -> None:
        changes, stats = diff_lines(["甲", "乙"], ["丙"])
        # 唯一 replace 块：m=2 旧 / n=1 新 → 1 行 modified + 1 行 removed
        assert stats.modified_lines == 1
        assert stats.removed_lines == 1
        assert stats.added_lines == 0
        kinds = [c.change_type for c in changes]
        assert kinds.count("modified") == 1
        assert kinds.count("removed") == 1
        modified = next(c for c in changes if c.change_type == "modified")
        assert modified.old_text == "甲" and modified.new_text == "丙"

    def test_pure_insert_delete(self) -> None:
        _, stats = diff_lines(["a", "b"], ["a", "b", "c"])
        assert stats.added_lines == 1
        assert stats.removed_lines == 0
        _, stats2 = diff_lines(["a", "b", "c"], ["a", "b"])
        assert stats2.removed_lines == 1
        assert stats2.added_lines == 0

    def test_equal_lines_count_as_unchanged(self) -> None:
        changes, stats = diff_lines(["同"], ["同"])
        assert stats.added_lines == stats.removed_lines == stats.modified_lines == 0
        assert all(c.change_type == "unchanged" for c in changes)


class TestDirectionSymmetry:
    """验收③：A/B 颠倒时方向正确、change_ratio 不变。"""

    def test_change_ratio_symmetric_with_added_scene(self) -> None:
        a = _script_a()
        b = _with_inserted_scene(a)
        r_ab = diff_script_drafts(a, b)
        r_ba = diff_script_drafts(b, a)
        assert r_ab.change_ratio == r_ba.change_ratio
        assert r_ab.scene_summary.added == r_ba.scene_summary.removed == 1
        assert r_ab.scene_summary.removed == r_ba.scene_summary.added == 0
        # 行统计方向互换
        assert r_ab.stats.added_lines == r_ba.stats.removed_lines
        assert r_ab.stats.removed_lines == r_ba.stats.added_lines

    def test_line_change_direction_swaps(self) -> None:
        fwd, _ = diff_lines(["原文甲"], ["新文乙"])
        rev, _ = diff_lines(["新文乙"], ["原文甲"])
        mod_fwd = next(c for c in fwd if c.change_type in ("modified", "removed"))
        mod_rev = next(c for c in rev if c.change_type in ("modified", "removed"))
        assert (mod_fwd.old_text, mod_fwd.new_text) == (mod_rev.new_text, mod_rev.old_text)


class TestChangeRatio:
    """change_ratio 边界与语义。"""

    def test_identical_zero(self) -> None:
        assert diff_script_drafts(_script_a(), _script_a()).change_ratio == 0.0

    def test_completely_different_one(self) -> None:
        result = diff_texts("abcdef", "123456")
        assert result.change_ratio == 1.0

    def test_empty_texts_zero(self) -> None:
        assert diff_texts("", "").change_ratio == 0.0

    def test_range_bounds(self) -> None:
        a = _script_a()
        b = _with_inserted_scene(a)
        ratio = diff_script_drafts(a, b).change_ratio
        assert 0.0 <= ratio <= 1.0

    def test_check_change_ratio(self) -> None:
        """验收⑤：Revision Gate 判定（<= max 不超限）。"""
        assert check_change_ratio(0.3, 0.35) is True
        assert check_change_ratio(0.4, 0.35) is False
        assert check_change_ratio(0.35, 0.35) is True


class TestTruncation:
    """验收⑤：超大 diff 限制响应体。"""

    def test_oversized_diff_truncated(self) -> None:
        a = _script_a()
        b = _script_b()
        result = diff_script_drafts(a, b, max_line_changes=3)
        assert _changed_line_count(result) > 3
        assert result.truncated is True
        assert all(s.line_changes == [] for s in result.scene_changes)
        assert all(s.line_changes_truncated for s in result.scene_changes)
        # 统计与摘要保留
        assert result.stats.from_chars > 0
        assert result.scene_summary.from_scene_count == 2
        assert 0.0 < result.change_ratio <= 1.0

    def test_within_limit_not_truncated(self) -> None:
        result = diff_script_drafts(_script_a(), _script_b(), max_line_changes=1000)
        assert result.truncated is False
        assert any(s.line_changes for s in result.scene_changes)


class TestLineModeFallback:
    """无法解析 ScriptDraft 时回退全文行 diff。"""

    def test_line_mode(self) -> None:
        result = diff_texts(_script_a().plain_text, _script_b().plain_text)
        assert result.mode == "line"
        assert result.line_changes
        assert result.scene_summary.from_scene_count == 0
        assert result.stats.from_chars > 0
        assert result.scene_changes == []

    def test_truncation_in_line_mode(self) -> None:
        result = diff_texts(_script_a().plain_text, _script_b().plain_text, max_line_changes=1)
        assert result.truncated is True
        assert result.line_changes == []


class TestSchemaConstraints:
    """Pydantic 守卫负值 / 越界。"""

    def test_diff_line_stats_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            DiffLineStats(
                added_lines=-1, removed_lines=0, modified_lines=0,
                added_chars=0, removed_chars=0, changed_chars=0,
                from_chars=0, to_chars=0,
            )

    def test_change_ratio_rejects_overflow(self) -> None:
        stats = DiffLineStats(
            added_lines=0, removed_lines=0, modified_lines=0,
            added_chars=0, removed_chars=0, changed_chars=0,
            from_chars=10, to_chars=10,
        )
        with pytest.raises(ValidationError):
            ScriptDiff(
                mode="scene",
                change_ratio=1.5,
                scene_summary=SceneDiffSummary(
                    from_scene_count=1,
                    to_scene_count=1,
                    added=0,
                    removed=0,
                    modified=0,
                    unchanged=1,
                ),
                stats=stats,
            )

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            DiffLineStats(
                added_lines=0, removed_lines=0, modified_lines=0,
                added_chars=0, removed_chars=0, changed_chars=0,
                from_chars=0, to_chars=0, unexpected=1,  # type: ignore[call-arg]
            )


class TestScriptDiffTool:
    """Tool 包装冒烟。"""

    @pytest.mark.asyncio
    async def test_execute_returns_serializable_dict(self) -> None:
        tool = ScriptDiffTool()
        result = await tool.execute(
            from_plain_text="第一行\n第二行",
            to_plain_text="第一行\n第二行改动",
        )
        assert isinstance(result, dict)
        assert result["mode"] == "line"
        assert result["truncated"] is False
        assert "stats" in result and "change_ratio" in result

    def test_metadata(self) -> None:
        assert ScriptDiffTool.metadata.name == "compute_script_diff"
        assert MAX_DIFF_LINE_CHANGES == 2000
