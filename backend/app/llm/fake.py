"""FakeLLM — 确定性假 LLM 客户端。

用途：
- 所有自动化测试的默认 LLM 实现
- 按 prompt_name 路由返回预设 fixture
- 支持故障注入：超时、非法 JSON、限流
- 记录调用序列供测试断言

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

from app.llm.models import LLMCallResult, LLMErrorCode, LLMUsage
from app.llm.protocol import LLMClient


class FakeFault:
    """FakeLLM 故障注入描述。"""

    def __init__(self, fault_type: str) -> None:
        self.fault_type = fault_type


class FakeLLM(LLMClient):
    """确定性假 LLM。

    不访问网络，不调用真实模型。
    按 prompt_name 从注册表返回预设 Pydantic 对象。
    """

    def __init__(self, seed: int = 42) -> None:
        """初始化 FakeLLM。

        Args:
            seed: 确定性种子（预留，当前版本未使用）
        """
        self.seed = seed
        self._registry: dict[str, BaseModel] = {}
        self._faults: dict[int, FakeFault] = {}
        self._call_history: list[LLMCallResult] = []
        self._call_count = 0
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
        """在第 call_index 次调用时注入故障。

        Args:
            call_index: 触发故障的调用序号（从 1 开始）
            fault_type: "timeout" | "invalid_json" | "rate_limited"
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
        """
        self._call_count += 1
        start = time.monotonic()

        # 故障注入检查
        fault = self._faults.get(self._call_count)
        if fault:
            duration_ms = int((time.monotonic() - start) * 1000)
            if fault.fault_type == "timeout":
                result = LLMCallResult(
                    attempt=self._call_count,
                    duration_ms=duration_ms,
                    error_code=LLMErrorCode.LLM_TIMEOUT,
                    error_detail="模拟超时",
                )
                self._call_history.append(result)
                return result
            elif fault.fault_type == "invalid_json":
                result = LLMCallResult(
                    content='{"invalid": "json"',
                    attempt=self._call_count,
                    duration_ms=duration_ms,
                    error_code=LLMErrorCode.INVALID_OUTPUT,
                    error_detail="模拟非法 JSON 输出",
                )
                self._call_history.append(result)
                return result
            elif fault.fault_type == "rate_limited":
                result = LLMCallResult(
                    attempt=self._call_count,
                    duration_ms=duration_ms,
                    error_code=LLMErrorCode.RATE_LIMITED,
                    error_detail="模拟限流",
                )
                self._call_history.append(result)
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
                attempt=self._call_count,
                error_code=LLMErrorCode.INVALID_OUTPUT,
                error_detail=f"未注册 prompt_name: {prompt_name}",
            )

        self._call_history.append(result)
        return result

    def get_call_history(self) -> list[LLMCallResult]:
        """获取调用历史记录。"""
        return list(self._call_history)

    def reset(self) -> None:
        """重置调用计数和历史（用于测试隔离）。"""
        self._call_count = 0
        self._call_history.clear()
        self._faults.clear()
