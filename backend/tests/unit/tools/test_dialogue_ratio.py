"""DialogueRatioTool 单元测试 (C-05)."""


from typing import Any

from app.tools.dialogue_ratio import (
    DialogueRatioTool,
    compute_dialogue_ratio,
    count_dialogue_chars,
)


class TestComputeFunctions:
    """对白统计函数测试."""

    def test_count_dialogue_chars(self) -> None:
        """统计各场景对白字符数."""
        scenes = [
            {
                "scene_number": 1,
                "dialogue": [
                    {"speaker": "A", "text": "你好"},
                    {"speaker": "B", "text": "世界"},
                ],
            },
            {
                "scene_number": 2,
                "dialogue": [
                    {"speaker": "A", "text": "Hello"},
                ],
            },
        ]
        # "你好"=2 + "世界"=2 + "Hello"=5 = 9 非空白字符
        assert count_dialogue_chars(scenes) == 9

    def test_count_dialogue_chars_empty(self) -> None:
        """无对白."""
        scenes: list[dict[str, Any]] = []
        assert count_dialogue_chars(scenes) == 0

    def test_compute_dialogue_ratio(self) -> None:
        """计算对白占比."""
        scenes = [
            {"scene_number": 1, "dialogue": [{"speaker": "A", "text": "你好世界"}]},
        ]
        plain_text = "你好世界，今天天气不错。"
        ratio = compute_dialogue_ratio(scenes, plain_text)
        # "你好世界" = 4 字, 全文约 11 字 → ~0.36
        assert 0.0 < ratio < 1.0

    def test_compute_dialogue_ratio_zero_text(self) -> None:
        """空全文 → 返回 0."""
        ratio = compute_dialogue_ratio([], "")
        assert ratio == 0.0

    def test_compute_dialogue_ratio_all_dialogue(self) -> None:
        """全是对白."""
        scenes = [
            {"scene_number": 1, "dialogue": [{"speaker": "A", "text": "你好世界"}]},
        ]
        ratio = compute_dialogue_ratio(scenes, "你好世界")
        assert ratio == 1.0


class TestDialogueRatioTool:
    """DialogueRatioTool 异步接口测试."""

    async def test_execute_returns_dict(self) -> None:
        """execute 返回正确结构的 dict."""
        tool = DialogueRatioTool()
        scenes = [
            {"scene_number": 1, "dialogue": [{"speaker": "甲", "text": "你好世界"}]},
        ]
        result = await tool.execute(scenes=scenes, plain_text="你好世界，今天天气不错。")
        assert "dialogue_ratio" in result
        assert "dialogue_chars" in result
        assert "total_chars" in result
        assert 0.0 <= result["dialogue_ratio"] <= 1.0

    async def test_execute_empty(self) -> None:
        """空输入 → 全为 0."""
        tool = DialogueRatioTool()
        result = await tool.execute(scenes=[], plain_text="")
        assert result["dialogue_ratio"] == 0.0
