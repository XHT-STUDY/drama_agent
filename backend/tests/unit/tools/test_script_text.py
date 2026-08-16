"""full_script_to_script_draft 单元测试 (G-06)。

覆盖"完整剧本 → 评估"路径的确定性转换：
- `第X场 地点（时间）` + `角色：对白` 格式 → 合法 ScriptDraft;
- 钩子取首 / 末对白;
- 动作行并入当前场; 场景不足 2 场 / 空文本 / 无场景标记 → None。
"""

from __future__ import annotations

import uuid
from typing import Any

from app.domain.script import ScriptDraft
from app.tools.script_text import full_script_to_script_draft

_SCRIPT_TEXT = (
    "第1场 训练场（日）\n"
    "教练：你被开除了。\n"
    "林峰：为什么？\n"
    "教练：因为你不够强。\n"
    "林峰握紧拳头，一言不发。\n"
    "\n"
    "第2场 宿舍（夜）\n"
    "林峰：我绝不放弃。\n"
    "室友：可你已经没有机会了。\n"
    "林峰：那就证明给他们看。\n"
)


def _extract() -> dict[str, Any]:
    result = full_script_to_script_draft(_SCRIPT_TEXT, title="被抛弃")
    assert result is not None, "合法完整剧本应转换成功"
    return result


class TestScriptDraftConversion:
    def test_valid_scriptdraft(self) -> None:
        """转换结果可通过 ScriptDraft 校验（合法结构）。"""
        content = _extract()
        script = ScriptDraft.model_validate(content)
        assert script.episode_number == 1
        assert script.title == "被抛弃"
        assert len(script.scenes) == 2
        assert script.scenes[0].scene_number == 1
        assert script.scenes[1].scene_number == 2

    def test_scene_headers_parsed(self) -> None:
        """场景标记解析出地点与时间。"""
        content = _extract()
        scenes = content["scenes"]
        assert scenes[0]["location"] == "训练场"
        assert scenes[0]["time_of_day"] == "日"
        assert scenes[1]["location"] == "宿舍"
        assert scenes[1]["time_of_day"] == "夜"

    def test_dialogue_extracted(self) -> None:
        """对白行 `角色：对白` 提取为 dialogue，角色进 characters。"""
        content = _extract()
        scenes = content["scenes"]
        assert scenes[0]["dialogue"] == [
            {"speaker": "教练", "text": "你被开除了。"},
            {"speaker": "林峰", "text": "为什么？"},
            {"speaker": "教练", "text": "因为你不够强。"},
        ]
        assert "林峰" in scenes[0]["characters"]
        assert "教练" in scenes[0]["characters"]

    def test_action_line_attached(self) -> None:
        """非场景非对白行并入当前场 action。"""
        content = _extract()
        scenes = content["scenes"]
        assert "林峰握紧拳头" in scenes[0]["action"]

    def test_hooks_from_first_last_dialogue(self) -> None:
        """开头 / 结尾钩子取首 / 末对白文本。"""
        content = _extract()
        assert content["opening_hook"] == "你被开除了。"
        assert content["ending_hook"] == "那就证明给他们看。"

    def test_plain_text_and_counts(self) -> None:
        """plain_text 保留原文，word_count / dialogue_ratio 确定性计算。"""
        content = _extract()
        assert content["plain_text"].startswith("第1场 训练场（日）")
        assert content["word_count"] > 0
        assert 0.0 <= content["dialogue_ratio"] <= 1.0

    def test_referenced_outline_is_uuid(self) -> None:
        """引用大纲 Artifact ID 缺省生成随机 UUID。"""
        content = _extract()
        assert uuid.UUID(str(content["referenced_outline_artifact_id"]))

    def test_empty_text_returns_none(self) -> None:
        """空文本无法构造 → None。"""
        assert full_script_to_script_draft("", title="空") is None

    def test_whitespace_only_returns_none(self) -> None:
        """纯空白文本 → None。"""
        assert full_script_to_script_draft("  \n  \n", title="空白") is None

    def test_single_scene_returns_none(self) -> None:
        """仅 1 场戏不足 ScriptDraft 最少 2 场要求 → None。"""
        text = "第1场 室内（日）\n林峰：你好。\n"
        assert full_script_to_script_draft(text, title="单场") is None

    def test_no_scene_markers_returns_none(self) -> None:
        """无场景标记的纯文本 → None（不强行猜测结构）。"""
        text = "林峰：你好。\n室友：你好。\n林峰：再见。\n"
        assert full_script_to_script_draft(text, title="无标记") is None

    def test_bare_scene_marker_fallback(self) -> None:
        """裸 `第X场`（无地点时间）回退默认地点 / 时间。"""
        text = (
            "第1场\n"
            "教练：滚。\n"
            "\n"
            "第2场\n"
            "林峰：我留下。\n"
        )
        content = full_script_to_script_draft(text, title="裸标记")
        assert content is not None
        scenes = content["scenes"]
        assert scenes[0]["location"] == "室内"
        assert scenes[0]["time_of_day"] == "日"
        assert len(scenes) == 2

    def test_dialogue_with_colon_ascii(self) -> None:
        """ASCII 冒号 `角色:对白` 也能识别。"""
        text = (
            "第1场 训练场（日）\n"
            "林峰:我来了。\n"
            "\n"
            "第2场 宿舍（夜）\n"
            "林峰:我走了。\n"
        )
        content = full_script_to_script_draft(text, title="冒号")
        assert content is not None
        assert content["scenes"][0]["dialogue"][0] == {
            "speaker": "林峰",
            "text": "我来了。",
        }

    def test_scene_without_dialogue_gets_action_fallback(self) -> None:
        """无对白、无动作的场次用占位 action（保证 Scene 校验通过）。"""
        text = (
            "第1场 训练场（日）\n"
            "林峰：加油。\n"
            "\n"
            "第2场 宿舍（夜）\n"
            "\n"
        )
        content = full_script_to_script_draft(text, title="占位")
        assert content is not None
        assert content["scenes"][1]["action"], "缺动作场应补占位 action"
        # 整体仍可通过 ScriptDraft 校验
        ScriptDraft.model_validate(content)


def test_regex_no_crash_on_odd_input() -> None:
    """畸形输入不抛异常。"""
    for text in ["第1场", "场", "：", "abcd：", "第1场（日）", "第1场 地点（日）x"]:
        result = full_script_to_script_draft(text, title="畸形")
        assert result is None or isinstance(result, dict)
