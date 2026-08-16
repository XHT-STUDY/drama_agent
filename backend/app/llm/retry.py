"""LLM 调用重试层 — 指数退避 + Retry-After 尊重 (I-01)。

将"可重试错误 → 退避重试"从具体客户端中抽出为统一机制：
- RetryPolicy：退避策略（base / factor / max_retries / max_delay）
- is_retryable：按 error_code 判定（429/timeout/5xx/连接失败 → 可重试；
  invalid output → 不可重试，由 StructuredOutputParser 负责带反馈重试）
- parse_retry_after：解析 429/503 的 Retry-After 头（秒或 HTTP-date）
- execute_with_retry：驱动重试循环；每次尝试由 attempt_fn 执行

与 StructuredOutputParser 的分工：
- 本层处理 HTTP 层错误（限流 / 超时 / 5xx / 连接失败）；
- Parser 处理"输出合法 JSON 但 Schema 校验失败"（带错误反馈重试）；
- 两者互补，互不重复计费（预算按每次真实 LLM 尝试自增）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from app.llm.models import LLMCallResult, LLMErrorCode
from app.observability.metrics import llm_retry_total

logger = logging.getLogger(__name__)

# 可在客户端重试的错误码集合。
# - RATE_LIMITED（429）：限流，退避后重试
# - LLM_TIMEOUT：超时，退避后重试
# - PROVIDER_ERROR：5xx / 连接失败，退避后重试
# INVALID_OUTPUT 不在其中：交给 StructuredOutputParser 带反馈重试。
RETRYABLE_CODES: frozenset[LLMErrorCode] = frozenset(
    {
        LLMErrorCode.RATE_LIMITED,
        LLMErrorCode.LLM_TIMEOUT,
        LLMErrorCode.PROVIDER_ERROR,
    }
)

def error_code_label(code: str | None) -> str:
    """把错误码转成指标标签文本（I-02）。

    LLMCallResult.error_code 声明为 str 但实际常存 LLMErrorCode 枚举：
    枚举取其 .value（如 rate_limited），纯 str 原样返回，None → "unknown"。
    """
    if code is None:
        return "unknown"
    if isinstance(code, LLMErrorCode):
        return code.value
    return code


# LLMErrorCode 值文本 → run 层错误码（节点失败时用于落库）
LLM_ERROR_RUN_CODES: dict[str, str] = {
    LLMErrorCode.LLM_TIMEOUT.value: "LLM_TIMEOUT",
    LLMErrorCode.RATE_LIMITED.value: "LLM_RATE_LIMITED",
    LLMErrorCode.PROVIDER_ERROR.value: "LLM_PROVIDER_ERROR",
    LLMErrorCode.INVALID_OUTPUT.value: "LLM_INVALID_OUTPUT",
}


class RetryPolicy:
    """指数退避重试策略。

    退避公式：base_delay * factor^(attempt-1)，上限 max_delay；
    服务端显式给出 Retry-After 时优先尊重（不超 max_delay）。
    """

    def __init__(
        self,
        *,
        base_delay: float = 0.5,
        factor: float = 2.0,
        max_retries: int = 2,
        max_delay: float = 30.0,
    ) -> None:
        self.base_delay = base_delay
        self.factor = factor
        self.max_retries = max_retries
        self.max_delay = max_delay

    @property
    def max_attempts(self) -> int:
        """总尝试次数 = 1 次原始调用 + max_retries 次重试。"""
        return self.max_retries + 1

    def compute_delay(self, attempt: int, retry_after: float | None = None) -> float:
        """计算第 attempt 次失败后、下一次重试前的等待秒数。

        attempt 从 1 开始（第一次尝试后的重试等待）。
        """
        if retry_after is not None:
            return max(0.0, min(retry_after, self.max_delay))
        return min(
            self.base_delay * (self.factor ** max(0, attempt - 1)),
            self.max_delay,
        )


def is_retryable(result: LLMCallResult) -> bool:
    """该调用结果是否需要重试。"""
    return result.error_code in RETRYABLE_CODES


def parse_retry_after(header: str | None) -> float | None:
    """解析 Retry-After 头。

    支持两种格式：
    - 秒数：`Retry-After: 120`
    - HTTP-date：`Retry-After: Fri, 31 Dec 1999 23:59:59 GMT`
    解析失败返回 None（调用方回退指数退避）。
    """
    if not header or not header.strip():
        return None
    header = header.strip()
    try:
        return max(0.0, float(header))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(header)
        return max(0.0, (dt.astimezone(UTC) - datetime.now(UTC)).total_seconds())
    except Exception:
        return None


async def execute_with_retry(
    attempt_fn: Callable[[int], Awaitable[LLMCallResult]],
    policy: RetryPolicy,
    *,
    retry_after_fn: Callable[[LLMCallResult], float | None] = lambda _result: None,
) -> LLMCallResult:
    """执行带退避重试的单次 LLM 调用。

    Args:
        attempt_fn: 执行一次真实 LLM 尝试，返回 LLMCallResult
            （不得抛异常；所有错误写入 result.error_code）。
            预算的 check/record 由 attempt_fn 内部完成。
        policy: 退避策略
        retry_after_fn: 从最近一次结果提取 Retry-After 秒数（真实 HTTP 客户端提供）

    Returns:
        最后一次尝试的结果；非可重试错误立即返回。
    """
    last: LLMCallResult | None = None
    for attempt in range(1, policy.max_attempts + 1):
        result = await attempt_fn(attempt)
        result.attempt = attempt
        last = result
        if not is_retryable(result):
            return result
        if attempt < policy.max_attempts:
            delay = policy.compute_delay(attempt, retry_after_fn(result))
            # I-02：重试计数（reason=错误码，低基数）
            llm_retry_total.inc(reason=error_code_label(result.error_code))
            logger.warning(
                "LLM 调用可重试错误（%s），第 %d 次尝试，%.2fs 后重试",
                result.error_code,
                attempt + 1,
                delay,
            )
            await asyncio.sleep(delay)
    if last is not None:
        return last
    raise RuntimeError("execute_with_retry 未执行任何尝试（不应到达）")
