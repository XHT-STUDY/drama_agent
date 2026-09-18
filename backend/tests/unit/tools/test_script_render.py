"""script_render 单元测试 — 剧本正文确定性渲染（中文短剧剧本格式）。"""

from __future__ import annotations

from typing import Any

import pytest

from app.tools.script_render import (
    dialogue_line,
    scene_heading,
    scene_lines,
    script_plain_text,
)


def make_scene(**overrides) -> dict[str, Any]:
    scene = {
        "scene_number": 1,
        "location": "冰封荒原-风雪坡",
        "time_of_day": "日",
        "int_ext": "外",
        "characters": ["苏翊"],
        "action": "狂风裹挟着冰屑横扫荒原。\n风雪中，苏翊裹紧领口。",
        "dialogue": [
            {
                "speaker": "苏翊",
                "text": "雪质干硬。",
                "parenthetical": "冷静",
                "line_type": "os",
            },
            {"speaker": "林晓", "text": "试炼开启。", "line_type": "vo"},
            {"speaker": "苏翊", "text": "是极地冰熊。", "parenthetical": "凝重"},
        ],
    }
    scene.update(overrides)
    return scene


class TestSceneHeading:
    def test_standard_heading(self) -> None:
        line = scene_heading(1, make_scene())
        assert line == "1-1 日 外 冰封荒原-风雪坡"

    def test_int_ext_default(self) -> None:
        scene = make_scene(int_ext=None)
        assert scene_heading(2, scene) == "2-1 日 外 冰封荒原-风雪坡"

    def test_int_ext_mixed(self) -> None:
        scene = make_scene(int_ext="内/外", time_of_day="夜", scene_number=2)
        assert scene_heading(3, scene) == "3-2 夜 内/外 冰封荒原-风雪坡"


class TestDialogueLine:
    def test_plain_dialogue(self) -> None:
        assert dialogue_line({"speaker": "苏翊", "text": "走。"}) == "苏翊：走。"

    def test_parenthetical(self) -> None:
        line = dialogue_line({"speaker": "苏翊", "text": "走。", "parenthetical": "凝重"})
        assert line == "苏翊（凝重）：走。"

    def test_vo(self) -> None:
        line = dialogue_line(
            {"speaker": "林晓", "text": "试炼开启。", "line_type": "vo", "parenthetical": "高亢"}
        )
        assert line == "林晓VO（高亢）：试炼开启。"

    def test_os(self) -> None:
        line = dialogue_line(
            {"speaker": "苏翊", "text": "雪质干硬。", "line_type": "os"}
        )
        assert line == "苏翊OS：雪质干硬。"


class TestSceneLines:
    def test_full_scene(self) -> None:
        lines = scene_lines(1, make_scene())
        assert lines == [
            "1-1 日 外 冰封荒原-风雪坡",
            "人物：苏翊",
            "△狂风裹挟着冰屑横扫荒原。",
            "△风雪中，苏翊裹紧领口。",
            "苏翊OS（冷静）：雪质干硬。",
            "林晓VO：试炼开启。",
            "苏翊（凝重）：是极地冰熊。",
        ]

    def test_without_heading(self) -> None:
        lines = scene_lines(1, make_scene(), with_heading=False)
        assert lines[0] == "人物：苏翊"

    def test_empty_characters_omitted(self) -> None:
        lines = scene_lines(1, make_scene(characters=[]))
        assert "人物：苏翊" not in lines

    def test_multi_line_action_each_prefixed(self) -> None:
        lines = scene_lines(1, make_scene(dialogue=[]))
        assert "△狂风裹挟着冰屑横扫荒原。" in lines
        assert "△风雪中，苏翊裹紧领口。" in lines


class TestScriptPlainText:
    def test_scenes_separated_by_blank_line(self) -> None:
        scenes = [
            make_scene(),
            make_scene(scene_number=2, location="岩坡山洞", characters=["苏翊"]),
        ]
        text = script_plain_text(1, scenes)
        blocks = text.split("\n\n")
        assert len(blocks) == 2
        assert blocks[0].startswith("1-1 日 外 冰封荒原-风雪坡")
        assert blocks[1].startswith("1-2 日 外 岩坡山洞")

    def test_render_is_deterministic(self) -> None:
        scenes = [make_scene(), make_scene(scene_number=2)]
        assert script_plain_text(1, scenes) == script_plain_text(1, scenes)


@pytest.mark.parametrize(
    ("line_type", "expected_prefix"),
    [("dialogue", "苏翊："), ("vo", "苏翊VO："), ("os", "苏翊OS：")],
)
def test_line_type_suffixes(line_type: str, expected_prefix: str) -> None:
    line = dialogue_line({"speaker": "苏翊", "text": "文本", "line_type": line_type})
    assert line.startswith(expected_prefix)
