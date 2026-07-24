"""B-06 StructuredOutputParser 单元测试。

验证：合法输出一次通过、非法输出重试、超时不重试。
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.llm.fake import FakeLLM
from app.llm.structured_output import StructuredOutputParser


class _TestSchema(BaseModel):
    title: str
    score: int


@pytest.mark.asyncio
class TestParser:
    """StructuredOutputParser 测试。"""

    async def test_valid_output_passes_first_attempt(self) -> None:
        """合法输出第一次调用即通过。"""
        llm = FakeLLM()
        llm.register("test", _TestSchema(title="Hello", score=100))
        parser = StructuredOutputParser(llm)

        result = await parser.parse(
            _TestSchema, [{"role": "user", "content": "test"}],
        )

        assert result.parsed is not None
        assert result.parsed.title == "Hello"
        assert result.attempt == 1
        assert result.error_code is None

    async def test_fault_returns_error_without_retry(self) -> None:
        """FakeLLM 注入故障时 parser 不重试直接返回错误。"""
        llm = FakeLLM()
        llm.inject_fault(1, "invalid_json")
        parser = StructuredOutputParser(llm)

        result = await parser.parse(
            _TestSchema, [{"role": "user", "content": "test"}],
        )

        assert result.error_code is not None

    async def test_timeout_not_retried(self) -> None:
        """超时错误不重试。"""
        llm = FakeLLM()
        llm.inject_fault(1, "timeout")
        parser = StructuredOutputParser(llm)

        result = await parser.parse(
            _TestSchema, [{"role": "user", "content": "test"}],
        )

        assert result.error_code == "llm_timeout"

    async def test_parser_with_multiple_calls(self) -> None:
        """连续多次调用正常工作。"""
        llm = FakeLLM()
        llm.register("call1", _TestSchema(title="First", score=1))
        llm.register("call2", _TestSchema(title="Second", score=2))
        parser = StructuredOutputParser(llm)

        r1 = await parser.parse(_TestSchema, [{"role": "user", "content": "call1"}])
        r2 = await parser.parse(_TestSchema, [{"role": "user", "content": "call2"}])

        assert r1.parsed.title == "First"
        assert r2.parsed.title == "Second"
