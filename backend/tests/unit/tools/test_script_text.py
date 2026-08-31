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


class TestShortDramaFormatParsing:
    """中文短剧剧本格式（`1-1 日 外 地点` / 人物：/ △ / VO / OS）解析。"""

    def test_scene_heading_new_format(self) -> None:
        """新场景头 `集-场 时间 内/外 地点` 可识别，场景号取连字符后的场号。"""
        text = (
            "1-1 日 外 冰封荒原-风雪坡\n"
            "人物：苏翊\n"
            "△狂风裹挟着冰屑横扫荒原。\n"
            "苏翊（凝重）：是极地冰熊。\n"
            "\n"
            "1-2 夜 内/外 岩坡山洞\n"
            "人物：苏翊\n"
            "苏翊：这里安全。\n"
        )
        content = full_script_to_script_draft(text, title="格式")
        assert content is not None
        scenes = content["scenes"]
        assert len(scenes) == 2
        assert scenes[0]["scene_number"] == 1
        assert scenes[0]["location"] == "冰封荒原-风雪坡"
        assert scenes[0]["time_of_day"] == "日"
        assert scenes[0]["int_ext"] == "外"
        assert scenes[1]["scene_number"] == 2
        assert scenes[1]["time_of_day"] == "夜"
        assert scenes[1]["int_ext"] == "内/外"
        ScriptDraft.model_validate(content)

    def test_characters_line(self) -> None:
        """`人物：A、B` 行解析为 characters，不当作对白。"""
        text = (
            "1-1 日 外 荒原\n"
            "人物：苏翊、陆铁峥\n"
            "苏翊：走。\n"
            "\n"
            "1-2 日 外 山洞\n"
            "苏翊：到了。\n"
        )
        content = full_script_to_script_draft(text, title="人物")
        assert content is not None
        assert content["scenes"][0]["characters"] == ["苏翊", "陆铁峥"]
        # 人物行未被误认为 speaker="人物" 的对白
        assert all(d["speaker"] != "人物" for d in content["scenes"][0]["dialogue"])

    def test_action_lines_kept_separate(self) -> None:
        """△ 动作行逐行保留在 action 中（换行分隔），不与对白混淆。"""
        text = (
            "1-1 日 外 荒原\n"
            "△狂风裹挟着冰屑横扫荒原。\n"
            "△苏翊裹紧领口。\n"
            "苏翊：冷。\n"
            "\n"
            "1-2 日 外 山洞\n"
            "苏翊：到了。\n"
        )
        content = full_script_to_script_draft(text, title="动作")
        assert content is not None
        assert content["scenes"][0]["action"] == "狂风裹挟着冰屑横扫荒原。\n苏翊裹紧领口。"

    def test_dialogue_vo_os_types(self) -> None:
        """`角色VO（情绪）：` 与 `角色OS（情绪）：` 解析出 line_type 与括注。"""
        text = (
            "1-1 日 外 荒原\n"
            "林晓VO（高亢）：试炼开启。\n"
            "苏翊OS（冷静）：雪质干硬。\n"
            "苏翊（凝重）：是极地冰熊。\n"
            "\n"
            "1-2 日 外 山洞\n"
            "苏翊：到了。\n"
        )
        content = full_script_to_script_draft(text, title="台词类型")
        assert content is not None
        dialogue = content["scenes"][0]["dialogue"]
        assert dialogue[0] == {
            "speaker": "林晓",
            "text": "试炼开启。",
            "parenthetical": "高亢",
            "line_type": "vo",
        }
        assert dialogue[1]["line_type"] == "os"
        assert dialogue[1]["parenthetical"] == "冷静"
        # 无前缀的默认为对白，不写 line_type
        assert dialogue[2] == {
            "speaker": "苏翊",
            "text": "是极地冰熊。",
            "parenthetical": "凝重",
        }

    def test_legacy_format_still_supported(self) -> None:
        """旧格式 `第X场 地点（时间）` 仍可解析。"""
        text = (
            "第1场 训练场（日）\n"
            "林峰：我来了。\n"
            "\n"
            "第2场 宿舍（夜）\n"
            "林峰：我走了。\n"
        )
        content = full_script_to_script_draft(text, title="旧格式")
        assert content is not None
        assert content["scenes"][0]["location"] == "训练场"
        assert content["scenes"][1]["time_of_day"] == "夜"
