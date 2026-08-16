"""日志脱敏单元测试（I-02）。

验证 mask_secret 与 RedactFilter：
- sk-* API Key / api_key 字段 / Bearer / Authorization 头被掩蔽；
- 超长内容截断；
- 通过 JsonFormatter 输出的日志不含明文密钥。
不依赖 DB / Redis / LLM。
"""

from __future__ import annotations

import json
import logging

from app.core.logging import JsonFormatter, RedactFilter, mask_secret


class TestMaskSecret:
    def test_sk_api_key(self) -> None:
        assert mask_secret("sk-proj-abc123XYZ456") == "sk-***"

    def test_api_key_field(self) -> None:
        assert mask_secret("api_key=sk-verysecretvalue") == "api_key=***"
        assert mask_secret("apikey: tok-abcdef") == "apikey: ***"
        # 引号包裹的 sk-* 由 sk- 模式捕获（值字符类不含引号）
        assert mask_secret('api_key="sk-verysecret"') == 'api_key="sk-***"'

    def test_bearer_token(self) -> None:
        assert mask_secret("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc") == (
            "Authorization: ***"
        )
        assert mask_secret("bearer abcdefghijklmno") == "bearer ***"

    def test_access_token_field(self) -> None:
        assert mask_secret("access_token=xyz123456") == "access_token=***"

    def test_truncation(self) -> None:
        long = "x" * 5000
        out = mask_secret(long, max_len=100)
        assert len(out) == 100 + len("…（已截断）")
        assert "已截断" in out

    def test_plain_text_unchanged(self) -> None:
        assert mask_secret("普通日志消息") == "普通日志消息"
        assert mask_secret("") == ""
        assert mask_secret("sk-") == "sk-"  # 值过短不匹配


class TestRedactFilter:
    def _render(self, msg: str) -> dict[str, object]:
        """构造 LogRecord → 过 RedactFilter → JsonFormatter 渲染并解析。"""
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg=msg, args=(), exc_info=None,
        )
        redact = RedactFilter()
        assert redact.filter(record) is True
        output = JsonFormatter().format(record)
        return json.loads(output)  # type: ignore[no-any-return]

    def test_message_redacted_in_output(self) -> None:
        rendered = self._render("请求密钥: api_key=sk-abcdef123456xyz")
        assert "sk-abcdef123456xyz" not in rendered["message"]  # type: ignore[operator]
        assert "api_key=***" in rendered["message"]  # type: ignore[operator]

    def test_rendered_with_args_redacted(self) -> None:
        """msg 带 %s 占位符时，先渲染再脱敏（args 不泄漏原文）。"""
        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname=__file__, lineno=1,
            msg="登录令牌: %s", args=("Bearer abcdefgh12345678",), exc_info=None,
        )
        assert RedactFilter().filter(record) is True
        # 已渲染并脱敏，args 被清空
        assert record.args == ()
        assert "abcdefgh12345678" not in record.getMessage()
        assert "***" in record.getMessage()

    def test_long_message_truncated_in_output(self) -> None:
        long = "A" * 3000
        rendered = self._render(long)
        assert len(rendered["message"]) < len(long)  # type: ignore[arg-type]
        assert "已截断" in rendered["message"]  # type: ignore[operator]

    def test_filter_never_blocks(self) -> None:
        record = logging.LogRecord(
            name="t", level=logging.INFO, pathname=__file__, lineno=1,
            msg="正常消息", args=(), exc_info=None,
        )
        assert RedactFilter().filter(record) is True
