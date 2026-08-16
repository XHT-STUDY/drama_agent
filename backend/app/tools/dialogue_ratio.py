"""DialogueRatioTool — 对白占比计算工具 (C-05).

确定性计算剧本中对白字符数占总字符数的比例。
不可隐式调用 LLM——纯 Python 实现。
"""

from __future__ import annotations

from typing import Any

from app.tools.protocol import Tool, ToolMetadata
from app.tools.word_count import count_total_chars


def count_dialogue_chars(scenes: list[dict[str, Any]]) -> int:
    """统计所有场景中对白文本的总字符数 (去除空白)。

    Args:
        scenes: Scene dict 列表, 每项含 dialogue 列表

    Returns:
        对白总字符数
    """
    total = 0
    for scene in scenes:
        for line in scene.get("dialogue", []):
            text = line.get("text", "")
            total += count_total_chars(text)
    return total


def compute_dialogue_ratio(scenes: list[dict[str, Any]], plain_text: str) -> float:
    """计算对白占比。

    dialogue_ratio = 对白字符数 / 全文总字符数

    Args:
        scenes: Scene dict 列表
        plain_text: 纯文本全文

    Returns:
        对白占比 (0.0 ~ 1.0)
    """
    total_chars = count_total_chars(plain_text)
    if total_chars == 0:
        return 0.0

    dialogue_chars = count_dialogue_chars(scenes)
    return round(dialogue_chars / total_chars, 4)


# ========================================================================
# DialogueRatioTool
# ========================================================================


class DialogueRatioTool(Tool):
    """对白占比计算工具。

    输入剧本的 scenes 和 plain_text，
    输出对白占比 (用于覆盖 LLM 自报值)。
    """

    metadata = ToolMetadata(
        name="compute_dialogue_ratio",
        version="1.0",
        description="计算剧本中对白字符占总字符的比例——覆盖 LLM 自报值",
        input_schema={
            "type": "object",
            "properties": {
                "scenes": {
                    "type": "array",
                    "description": "剧本场次列表，每项含 dialogue 数组",
                },
                "plain_text": {"type": "string"},
            },
            "required": ["scenes", "plain_text"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "dialogue_ratio": {"type": "number"},
                "dialogue_chars": {"type": "integer"},
                "total_chars": {"type": "integer"},
            },
            "required": ["dialogue_ratio", "dialogue_chars", "total_chars"],
        },
    )

    async def execute(self, **kwargs: Any) -> dict[str, float]:
        """计算对白占比。

        Args:
            scenes: list[dict[str, Any]] — 剧本场次列表
            plain_text: str — 纯文本全文

        Returns:
            {"dialogue_ratio": float, "dialogue_chars": int, "total_chars": int}
        """
        scenes: list[dict[str, Any]] = kwargs.get("scenes", [])
        plain_text: str = kwargs.get("plain_text", "")

        total_chars = count_total_chars(plain_text)
        dialogue_chars = count_dialogue_chars(scenes)
        ratio = compute_dialogue_ratio(scenes, plain_text)

        return {
            "dialogue_ratio": ratio,
            "dialogue_chars": dialogue_chars,
            "total_chars": total_chars,
        }
