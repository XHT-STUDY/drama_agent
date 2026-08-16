"""FakeLLM — 确定性假 LLM 客户端。

用途：
- 所有自动化测试的默认 LLM 实现
- 按 prompt_name 路由返回预设 fixture
- 支持故障注入：超时、非法 JSON、Schema 校验失败、限流
- 记录调用序列供测试断言

I-01：与真实客户端一致接入重试层与 per-run 预算——
- 可选 retry_policy：构造时传入则按 RetryPolicy 对可重试错误重试
  （默认不启用，保持存量测试语义；Worker 在测试环境也可按需启用）；
- 每次真实尝试前 check_budget()、尝试后 record_call(usage)；
- 故障注入按"尝试序号"（含重试内的每次尝试）计数，从 1 开始。

使用方式：
    llm = FakeLLM()
    llm.register("story_bible", mock_story_bible_instance)
    result = await llm.generate_structured(StoryBible, messages)
    assert result.parsed == mock_story_bible
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel

from app.llm.budget import check_budget, record_call
from app.llm.models import LLMCallResult, LLMErrorCode, LLMUsage
from app.llm.protocol import LLMClient
from app.llm.retry import RetryPolicy, execute_with_retry


class FakeFault:
    """FakeLLM 故障注入描述。"""

    def __init__(self, fault_type: str) -> None:
        self.fault_type = fault_type


class FakeLLM(LLMClient):
    """确定性假 LLM。

    不访问网络，不调用真实模型。
    按 prompt_name 从注册表返回预设 Pydantic 对象。
    """

    def __init__(self, seed: int = 42, *, retry_policy: RetryPolicy | None = None) -> None:
        """初始化 FakeLLM。

        Args:
            seed: 确定性种子（预留，当前版本未使用）
            retry_policy: 可选重试策略（I-01）。传入后对可重试错误
                执行与真实客户端一致的退避重试；默认 None 不重试，
                保持存量测试对故障注入的断言语义。
        """
        self.seed = seed
        self.retry_policy = retry_policy
        self._registry: dict[str, BaseModel] = {}
        self._faults: dict[int, FakeFault] = {}
        self._call_history: list[LLMCallResult] = []
        self._attempt_count = 0
        self._default_result: BaseModel | None = None

    # ---- 配置 ----

    def register(self, prompt_name: str, result: BaseModel) -> None:
        """注册 prompt_name 对应的预设返回值。

        Args:
            prompt_name: LLM 调用时的标识（如 "story_bible"、"evaluate_episode"）
            result: 要返回的 Pydantic 对象实例
        """
        self._registry[prompt_name] = result

    def set_default(self, result: BaseModel) -> None:
        """设置未匹配 prompt_name 时的默认返回值。"""
        self._default_result = result

    def inject_fault(self, call_index: int, fault_type: str) -> None:
        """在第 call_index 次尝试时注入故障。

        Args:
            call_index: 触发故障的尝试序号（从 1 开始；启用重试时，
                同一次 generate_structured 的重试会占用连续序号）
            fault_type: "timeout" | "invalid_json" | "invalid_schema" | "rate_limited"
                - invalid_json：返回非法 JSON + error_code=INVALID_OUTPUT
                  （StructuredOutputParser 不重试，直接返回错误）；
                - invalid_schema：返回合法 JSON 但 Schema 校验失败、无 error_code
                  （StructuredOutputParser 带反馈重试，用于验证 Parser 重试路径）。
        """
        self._faults[call_index] = FakeFault(fault_type)

    # ---- LLMClient 实现 ----

    async def generate_structured(
        self,
        schema: type[BaseModel],
        messages: list[dict[str, str]],
        *,
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout_seconds: int = 180,
        **kwargs: Any,
    ) -> LLMCallResult:
        """模拟 LLM 调用——返回注册的 fixture 或注入故障。

        prompt_name 从 messages 最后一条的 content 前 50 字符提取，
        或通过 kwargs["prompt_name"] 传入。

        配置了 retry_policy 时，对可重试错误执行与真实客户端一致的
        指数退避重试（I-01）。
        """
        if self.retry_policy is not None:
            return await execute_with_retry(
                lambda attempt: self._single_attempt(
                    schema, messages, model=model, **kwargs
                ),
                self.retry_policy,
            )
        return await self._single_attempt(schema, messages, model=model, **kwargs)

    async def _single_attempt(
        self,
        schema: type[BaseModel],
        messages: list[dict[str, str]],
        *,
        model: str = "",
        **kwargs: Any,
    ) -> LLMCallResult:
        """执行一次真实 LLM 尝试（含预算检查 / 计数与故障注入）。"""
        check_budget()
        self._attempt_count += 1
        start = time.monotonic()

        # 故障注入检查
        fault = self._faults.get(self._attempt_count)
        if fault:
            duration_ms = int((time.monotonic() - start) * 1000)
            if fault.fault_type == "timeout":
                result = LLMCallResult(
                    attempt=self._attempt_count,
                    duration_ms=duration_ms,
                    error_code=LLMErrorCode.LLM_TIMEOUT,
                    error_detail="模拟超时",
                )
            elif fault.fault_type == "invalid_json":
                result = LLMCallResult(
                    content='{"invalid": "json"',
                    attempt=self._attempt_count,
                    duration_ms=duration_ms,
                    error_code=LLMErrorCode.INVALID_OUTPUT,
                    error_detail="模拟非法 JSON 输出",
                )
            elif fault.fault_type == "invalid_schema":
                # 合法 JSON 但 Schema 校验失败、无 error_code → Parser 带反馈重试
                result = LLMCallResult(
                    content="{}",
                    attempt=self._attempt_count,
                    duration_ms=duration_ms,
                    error_detail="模拟 Schema 校验失败输出",
                )
            elif fault.fault_type == "rate_limited":
                result = LLMCallResult(
                    attempt=self._attempt_count,
                    duration_ms=duration_ms,
                    error_code=LLMErrorCode.RATE_LIMITED,
                    error_detail="模拟限流",
                )
            else:
                result = LLMCallResult(
                    attempt=self._attempt_count,
                    duration_ms=duration_ms,
                    error_code=LLMErrorCode.PROVIDER_ERROR,
                    error_detail=f"未知故障类型: {fault.fault_type}",
                )
            self._call_history.append(result)
            record_call()
            return result

        # 查找 fixture
        prompt_name = kwargs.get("prompt_name", "")
        if not prompt_name and messages:
            last_content = messages[-1].get("content", "")
            prompt_name = last_content[:50]

        fixture = self._registry.get(prompt_name, self._default_result)

        duration_ms = int((time.monotonic() - start) * 1000)

        if fixture is not None:
            # 确保返回的是期望的 Schema 类型
            validated = schema.model_validate(fixture.model_dump())
            result = LLMCallResult(
                content=validated.model_dump_json(),
                parsed=validated,
                usage=LLMUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
                model=model or "fake-model",
                duration_ms=duration_ms,
                attempt=1,
            )
        else:
            result = LLMCallResult(
                content="{}",
                parsed=None,
                model=model or "fake-model",
                duration_ms=duration_ms,
                attempt=self._attempt_count,
                error_code=LLMErrorCode.INVALID_OUTPUT,
                error_detail=f"未注册 prompt_name: {prompt_name}",
            )

        self._call_history.append(result)
        record_call(prompt_tokens=100, completion_tokens=50)
        return result

    def get_call_history(self) -> list[LLMCallResult]:
        """获取调用历史记录。"""
        return list(self._call_history)

    def reset(self) -> None:
        """重置调用计数和历史（用于测试隔离）。"""
        self._attempt_count = 0
        self._call_history.clear()
        self._faults.clear()
