"""B-06 FakeLLM 单元测试。

验证：注册 fixture 返回、故障注入、调用历史。
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.llm.fake import FakeLLM
from app.llm.models import LLMErrorCode


class _Schema(BaseModel):
    name: str = ""
    value: int = 0


@pytest.mark.asyncio
class TestFakeLLMBasic:
    """FakeLLM 基本功能。"""

    async def test_returns_registered_fixture(self) -> None:
        """注册 fixture 后返回对应的 Schema。"""
        llm = FakeLLM()
        expected = _Schema(name="test", value=42)
        llm.register("test_prompt", expected)

        result = await llm.generate_structured(
            _Schema, [{"role": "user", "content": "test_prompt"}],
        )

        assert result.parsed is not None
        assert result.parsed.name == "test"
        assert result.parsed.value == 42
        assert result.error_code is None

    async def test_returns_default_when_no_match(self) -> None:
        """未匹配 prompt_name 时返回默认值。"""
        llm = FakeLLM()
        default = _Schema(name="default", value=1)
        llm.set_default(default)

        result = await llm.generate_structured(
            _Schema, [{"role": "user", "content": "unknown"}],
        )

        assert result.parsed is not None
        assert result.parsed.name == "default"

    async def test_error_when_no_match_and_no_default(self) -> None:
        """无匹配且无默认值时返回错误。"""
        llm = FakeLLM()
        result = await llm.generate_structured(
            _Schema, [{"role": "user", "content": "nobody_registered_this"}],
        )

        assert result.error_code == LLMErrorCode.INVALID_OUTPUT


@pytest.mark.asyncio
class TestFakeLLMFaultInjection:
    """FakeLLM 故障注入。"""

    async def test_timeout_fault(self) -> None:
        """注入 timeout 故障返回 LLM_TIMEOUT。"""
        llm = FakeLLM()
        llm.register("test", _Schema(name="x", value=1))
        llm.inject_fault(1, "timeout")

        result = await llm.generate_structured(
            _Schema, [{"role": "user", "content": "test"}],
        )
        assert result.error_code == LLMErrorCode.LLM_TIMEOUT

    async def test_rate_limited_fault(self) -> None:
        """注入 rate_limited 故障。"""
        llm = FakeLLM()
        llm.inject_fault(1, "rate_limited")

        result = await llm.generate_structured(
            _Schema, [{"role": "user", "content": "test"}],
        )
        assert result.error_code == LLMErrorCode.RATE_LIMITED

    async def test_invalid_json_fault(self) -> None:
        """注入 invalid_json 故障。"""
        llm = FakeLLM()
        llm.inject_fault(1, "invalid_json")

        result = await llm.generate_structured(
            _Schema, [{"role": "user", "content": "test"}],
        )
        assert result.error_code == LLMErrorCode.INVALID_OUTPUT


@pytest.mark.asyncio
class TestFakeLLMCallHistory:
    """FakeLLM 调用历史。"""

    async def test_call_history_recorded(self) -> None:
        """调用历史被正确记录。"""
        llm = FakeLLM()
        llm.register("a", _Schema(name="a", value=1))
        llm.register("b", _Schema(name="b", value=2))

        await llm.generate_structured(_Schema, [{"role": "user", "content": "a"}])
        await llm.generate_structured(_Schema, [{"role": "user", "content": "b"}])

        assert len(llm.get_call_history()) == 2

    async def test_reset_clears_history(self) -> None:
        """reset 清空调用历史。"""
        llm = FakeLLM()
        llm.register("test", _Schema(name="t", value=1))

        await llm.generate_structured(_Schema, [{"role": "user", "content": "test"}])
        llm.reset()

        assert len(llm.get_call_history()) == 0
