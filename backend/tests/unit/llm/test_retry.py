"""I-01 LLM 重试层单元测试。

覆盖:
- RetryPolicy 指数退避计算（base*factor^(attempt-1)、Retry-After 优先、max_delay 封顶）
- Retry-After 头解析（秒 / HTTP-date / 非法）
- is_retryable 错误分类（429/timeout/5xx 可重试；invalid output 不可重试）
- execute_with_retry 驱动（重试到成功 / 耗尽 / 非可重试立即返回）
- FakeLLM 配置 retry_policy 后的重试语义（故障注入按尝试序号）

不依赖真实 LLM，全部确定性。
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.llm.fake import FakeLLM
from app.llm.models import LLMCallResult, LLMErrorCode
from app.llm.retry import (
    RETRYABLE_CODES,
    RetryPolicy,
    execute_with_retry,
    is_retryable,
    parse_retry_after,
)


class _Schema(BaseModel):
    name: str = ""
    value: int = 0


def _ok() -> LLMCallResult:
    return LLMCallResult(
        content="{}",
        parsed=_Schema(name="ok", value=1),
    )


def _err(code: LLMErrorCode, detail: str = "模拟错误") -> LLMCallResult:
    return LLMCallResult(error_code=code, error_detail=detail)


# ========================================================================
# RetryPolicy 退避计算
# ========================================================================


class TestRetryPolicy:
    """退避策略计算。"""

    def test_exponential_backoff(self) -> None:
        """base*factor^(attempt-1)。"""
        policy = RetryPolicy(base_delay=0.5, factor=2.0)
        assert policy.compute_delay(1) == pytest.approx(0.5)
        assert policy.compute_delay(2) == pytest.approx(1.0)
        assert policy.compute_delay(3) == pytest.approx(2.0)

    def test_max_delay_caps_exponential(self) -> None:
        """退避不超过 max_delay。"""
        policy = RetryPolicy(base_delay=1.0, factor=10.0, max_delay=5.0)
        assert policy.compute_delay(10) == pytest.approx(5.0)

    def test_retry_after_priority(self) -> None:
        """服务端 Retry-After 优先于指数退避，且不超 max_delay。"""
        policy = RetryPolicy(base_delay=0.5, factor=2.0, max_delay=30.0)
        assert policy.compute_delay(1, retry_after=0.1) == pytest.approx(0.1)
        # Retry-After 超过 max_delay → 按 max_delay 封顶
        assert policy.compute_delay(1, retry_after=999) == pytest.approx(30.0)

    def test_max_attempts(self) -> None:
        """总尝试次数 = 1 + max_retries。"""
        assert RetryPolicy(max_retries=2).max_attempts == 3
        assert RetryPolicy(max_retries=0).max_attempts == 1


class TestParseRetryAfter:
    """Retry-After 头解析。"""

    def test_seconds(self) -> None:
        assert parse_retry_after("120") == pytest.approx(120.0)
        assert parse_retry_after("  5 ") == pytest.approx(5.0)

    def test_http_date_past(self) -> None:
        """过去的 HTTP-date → 立即重试（0 秒）。"""
        assert parse_retry_after("Fri, 31 Dec 1999 23:59:59 GMT") == pytest.approx(0.0)

    def test_invalid_returns_none(self) -> None:
        """非法输入 → None（调用方回退指数退避）。"""
        assert parse_retry_after("not-a-date") is None
        assert parse_retry_after("") is None
        assert parse_retry_after(None) is None


class TestIsRetryable:
    """错误分类。"""

    def test_retryable_codes(self) -> None:
        assert {
            LLMErrorCode.RATE_LIMITED,
            LLMErrorCode.LLM_TIMEOUT,
            LLMErrorCode.PROVIDER_ERROR,
        } == RETRYABLE_CODES

    def test_rate_limited_retryable(self) -> None:
        assert is_retryable(_err(LLMErrorCode.RATE_LIMITED)) is True

    def test_timeout_retryable(self) -> None:
        assert is_retryable(_err(LLMErrorCode.LLM_TIMEOUT)) is True

    def test_provider_error_retryable(self) -> None:
        assert is_retryable(_err(LLMErrorCode.PROVIDER_ERROR)) is True

    def test_invalid_output_not_retryable(self) -> None:
        """invalid output 交给 Parser 带反馈重试，不在 HTTP 层重试。"""
        assert is_retryable(_err(LLMErrorCode.INVALID_OUTPUT)) is False

    def test_success_not_retryable(self) -> None:
        assert is_retryable(_ok()) is False


# ========================================================================
# execute_with_retry 驱动
# ========================================================================


@pytest.mark.asyncio
class TestExecuteWithRetry:
    """重试循环驱动。"""

    async def test_succeeds_after_retries(self) -> None:
        """前 2 次 429、第 3 次成功 → 重试后返回成功。"""
        recorded: list[int] = []
        policy = RetryPolicy(base_delay=0.01, max_retries=2)

        async def attempt_fn(attempt: int) -> LLMCallResult:
            recorded.append(attempt)
            if len(recorded) <= 2:
                return _err(LLMErrorCode.RATE_LIMITED)
            return _ok()

        result = await execute_with_retry(attempt_fn, policy)
        assert result.parsed is not None
        assert result.error_code is None
        assert recorded == [1, 2, 3], f"应尝试 3 次，实际 {recorded}"

    async def test_exhausts_retries_returns_last(self) -> None:
        """持续 429 → 达到 max_attempts 后返回最后一次结果。"""
        recorded: list[int] = []
        policy = RetryPolicy(base_delay=0.01, max_retries=2)

        async def attempt_fn(attempt: int) -> LLMCallResult:
            recorded.append(attempt)
            return _err(LLMErrorCode.RATE_LIMITED)

        result = await execute_with_retry(attempt_fn, policy)
        assert result.error_code == LLMErrorCode.RATE_LIMITED
        assert recorded == [1, 2, 3]
        assert result.attempt == 3

    async def test_non_retryable_returns_immediately(self) -> None:
        """invalid output 不可重试 → 只尝试一次。"""
        recorded: list[int] = []
        policy = RetryPolicy(base_delay=0.01, max_retries=2)

        async def attempt_fn(attempt: int) -> LLMCallResult:
            recorded.append(attempt)
            return _err(LLMErrorCode.INVALID_OUTPUT)

        result = await execute_with_retry(attempt_fn, policy)
        assert result.error_code == LLMErrorCode.INVALID_OUTPUT
        assert recorded == [1]

    async def test_retry_after_fn_used(self) -> None:
        """retry_after_fn 从结果提取 Retry-After 并影响等待（尝试次数不变）。"""
        recorded: list[int] = []
        policy = RetryPolicy(base_delay=0.01, max_retries=1)

        async def attempt_fn(attempt: int) -> LLMCallResult:
            recorded.append(attempt)
            if len(recorded) == 1:
                return _err(LLMErrorCode.RATE_LIMITED, detail="429")
            return _ok()

        # 每次结果都带 Retry-After（即使成功也无害）
        await execute_with_retry(
            attempt_fn, policy, retry_after_fn=lambda _r: 0.01
        )
        assert recorded == [1, 2]


# ========================================================================
# FakeLLM + retry_policy 集成
# ========================================================================


@pytest.mark.asyncio
class TestFakeLLMWithRetryPolicy:
    """FakeLLM 配置 retry_policy 后的重试语义。"""

    def _make(self, *, max_retries: int = 2) -> FakeLLM:
        llm = FakeLLM(
            seed=42,
            retry_policy=RetryPolicy(base_delay=0.01, max_retries=max_retries),
        )
        llm.register("test", _Schema(name="ok", value=1))
        return llm

    async def test_rate_limited_then_recovers(self) -> None:
        """第 1 次 429 → 重试第 2 次成功。"""
        llm = self._make()
        llm.inject_fault(1, "rate_limited")

        result = await llm.generate_structured(
            _Schema, [{"role": "user", "content": "test"}],
        )
        assert result.parsed is not None
        assert result.parsed.name == "ok"
        assert result.error_code is None
        # 尝试序号含重试：1(429) + 1(成功)
        assert len(llm.get_call_history()) == 2

    async def test_timeout_exhausts(self) -> None:
        """timeout 全部尝试耗尽 → 返回 LLM_TIMEOUT。"""
        llm = self._make(max_retries=2)  # 共 3 次尝试
        llm.inject_fault(1, "timeout")
        llm.inject_fault(2, "timeout")
        llm.inject_fault(3, "timeout")

        result = await llm.generate_structured(
            _Schema, [{"role": "user", "content": "test"}],
        )
        assert result.error_code == LLMErrorCode.LLM_TIMEOUT
        assert result.attempt == 3
        assert len(llm.get_call_history()) == 3

    async def test_provider_error_retried(self) -> None:
        """provider_error（5xx/连接失败）可重试。"""
        llm = self._make()
        llm.inject_fault(1, "provider_error")

        result = await llm.generate_structured(
            _Schema, [{"role": "user", "content": "test"}],
        )
        assert result.parsed is not None
        assert len(llm.get_call_history()) == 2

    async def test_invalid_json_not_retried_by_http_layer(self) -> None:
        """invalid_json（INVALID_OUTPUT）在 HTTP 层不重试——交给 Parser。"""
        llm = self._make()
        llm.inject_fault(1, "invalid_json")

        result = await llm.generate_structured(
            _Schema, [{"role": "user", "content": "test"}],
        )
        assert result.error_code == LLMErrorCode.INVALID_OUTPUT
        assert len(llm.get_call_history()) == 1
