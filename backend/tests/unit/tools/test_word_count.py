"""WordCountTool 单元测试 (C-05)."""

from app.tools.word_count import (
    WordCountTool,
    count_chinese_chars,
    count_chinese_chars_with_punct,
    count_total_chars,
)


class TestCountFunctions:
    """字数统计函数测试."""

    def test_count_chinese_chars_pure(self) -> None:
        """纯中文文本."""
        assert count_chinese_chars("你好世界") == 4
        assert count_chinese_chars("测试") == 2
        assert count_chinese_chars("这是一段测试文本") >= 7

    def test_count_chinese_chars_mixed(self) -> None:
        """中英混合——只统计中文."""
        text = "Hello 你好 World 世界"
        assert count_chinese_chars(text) == 4

    def test_count_chinese_chars_empty(self) -> None:
        """空字符串."""
        assert count_chinese_chars("") == 0

    def test_count_chinese_chars_no_cjk(self) -> None:
        """全英文无中文."""
        assert count_chinese_chars("Hello World 123 !@#") == 0

    def test_count_chinese_chars_with_punct(self) -> None:
        """含中文标点."""
        result = count_chinese_chars_with_punct("你好。世界！")
        # "你好世界" 4 个汉字 + "。" 和 "！" 两个中文标点
        assert result >= 4

    def test_count_total_chars(self) -> None:
        """去除空白后的总字符数."""
        assert count_total_chars("a b c") == 3
        assert count_total_chars("你好 世界") == 4
        assert count_total_chars("a\nb\tc") == 3


class TestWordCountTool:
    """WordCountTool 异步接口测试."""

    async def test_execute_returns_dict(self) -> None:
        """execute 返回正确结构的 dict."""
        tool = WordCountTool()
        result = await tool.execute(plain_text="你好世界，Hello World！")
        assert "chinese_chars" in result
        assert "chinese_chars_with_punct" in result
        assert "total_chars" in result
        assert result["chinese_chars"] == 4  # 你好世界
        assert result["total_chars"] > 0

    async def test_execute_empty(self) -> None:
        """空文本全为 0."""
        tool = WordCountTool()
        result = await tool.execute(plain_text="")
        assert result["chinese_chars"] == 0
        assert result["chinese_chars_with_punct"] == 0
        assert result["total_chars"] == 0
