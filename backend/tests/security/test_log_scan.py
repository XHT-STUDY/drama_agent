"""日志扫描回归测试（I-03）。

用生产日志管线（RedactFilter + JsonFormatter）捕获真实 LLM 错误日志与
超长内容日志，断言输出 JSON 中不含明文密钥、不含完整超长文本。
通过 monkeypatch 替换 openai_compatible 模块 logger，不触碰全局日志配置。

不依赖 DB / Redis / 真实 LLM。
"""

from __future__ import annotations

import io
import json
import logging
from typing import cast

import httpx
import pytest

from app.core.config import Settings
from app.core.logging import JsonFormatter, RedactFilter
from app.llm.openai_compatible import OpenAICompatibleLLM


class TestLogScan:
    """日志扫描：捕获后逐行解析 JSON，断言无明文密钥 / 无超长全文。"""

    @pytest.fixture
    def captured(self, monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
        buf = io.StringIO()
        logger = logging.getLogger("test.log_scan")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.handlers.clear()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(JsonFormatter())
        handler.addFilter(RedactFilter())
        logger.addHandler(handler)

        import app.llm.openai_compatible as oc_module

        monkeypatch.setattr(oc_module, "logger", logger)
        return buf

    @staticmethod
    def _last_entry(buf: io.StringIO) -> dict[str, object]:
        raw = buf.getvalue()
        assert raw, "应捕获到日志输出"
        line = raw.strip().splitlines()[-1]
        return cast(dict[str, object], json.loads(line))

    def test_llm_error_log_has_no_plaintext_key(self, captured: io.StringIO) -> None:
        """真实 LLM 错误日志：响应体中的 sk- 密钥被掩蔽。"""
        llm = OpenAICompatibleLLM(Settings())
        response = httpx.Response(
            401,
            json={
                "error": {
                    "message": "invalid api key: sk-abcdef123456xyz",
                    "code": "invalid_api_key",
                }
            },
            request=httpx.Request("POST", "http://test/v1/chat/completions"),
        )
        result = llm._handle_error_response(
            response, duration_ms=120, model="gpt-4o"
        )
        # 错误码正常返回（协议契约：不抛异常、错误进 result）
        assert result.error_code is not None
        assert result.error_detail

        entry = self._last_entry(captured)
        message = str(entry["message"])
        assert "sk-abcdef123456xyz" not in message
        assert "sk-***" in message

    def test_long_content_truncated_in_log(self, captured: io.StringIO) -> None:
        """超长内容（如完整 Prompt）在日志中被截断，不全文落盘。"""
        logger = logging.getLogger("test.log_scan")
        long_prompt = "完整提示词内容 " + "A" * 5000
        logger.error("prompt=%s", long_prompt)
        entry = self._last_entry(captured)
        message = str(entry["message"])
        assert "已截断" in message
        assert len(message) < len(long_prompt)
        assert "A" * 4000 not in message
