"""WordCountTool — 中文字数统计工具 (C-05).

确定性计算文本中的中文字符数与总字符数。
不可隐式调用 LLM——纯 Python 实现。
"""

from __future__ import annotations

import re
from typing import Any

from app.tools.protocol import Tool, ToolMetadata

# CJK 统一汉字区间 (基本)
_CJK_RE = re.compile(r"[一-鿿]")

# 中文标点
_CJK_PUNCT_RE = re.compile(r"[　-〿＀-￯]")


def count_chinese_chars(text: str) -> int:
    """统计文本中中文字符的数量 (含 CJK 汉字)。

    Args:
        text: 待统计的文本

    Returns:
        中文字符数
    """
    return len(_CJK_RE.findall(text))


def count_chinese_chars_with_punct(text: str) -> int:
    """统计文本中中文字符 + 中文标点的数量。

    对于短剧剧本统计，标点通常计入总字数。

    Args:
        text: 待统计的文本

    Returns:
        中文字符 + 中文标点数量
    """
    return len(_CJK_RE.findall(text)) + len(_CJK_PUNCT_RE.findall(text))


def count_total_chars(text: str) -> int:
    """统计文本总字符数 (去除空白后)。

    Args:
        text: 待统计的文本

    Returns:
        非空白字符总数
    """
    return len(re.sub(r"\s+", "", text))


# ========================================================================
# WordCountTool
# ========================================================================


class WordCountTool(Tool):
    """中文字数统计工具。

    输入纯文本，输出中文字符数。
    用于覆盖 LLM 自报的 word_count。
    """

    metadata = ToolMetadata(
        name="compute_word_count",
        version="1.0",
        description="统计文本中中文字符 (含标点) 的数量——覆盖 LLM 自报值",
    )

    async def execute(self, **kwargs: Any) -> dict[str, int]:
        """统计中文字数。

        Args:
            plain_text: str — 纯文本全文

        Returns:
            {"chinese_chars": int, "chinese_chars_with_punct": int, "total_chars": int}
        """
        text: str = kwargs.get("plain_text", "")

        return {
            "chinese_chars": count_chinese_chars(text),
            "chinese_chars_with_punct": count_chinese_chars_with_punct(text),
            "total_chars": count_total_chars(text),
        }
