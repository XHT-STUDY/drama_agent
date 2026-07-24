"""StructuredOutputParser — LLM 输出 → Pydantic Schema 校验。

实现：
- 调用 LLM → Pydantic model_validate 校验
- 校验失败自动重试（最多 2 次）
- 第一次：重试原始 Prompt
- 第二次：重试时附带校验错误信息
- 仍失败 → 返回 error_code=INVALID_OUTPUT
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from app.llm.models import LLMCallResult, LLMErrorCode
from app.llm.protocol import LLMClient

# DEV_PLAN §2.3：结构化输出最多重试 2 次
MAX_RETRIES = 2


class StructuredOutputParser:
    """LLM 结构化输出解析器。

    封装了 LLM 调用 → Pydantic 校验 → 重试 的完整流程。
    """

    def __init__(self, client: LLMClient, max_retries: int = MAX_RETRIES) -> None:
        self.client = client
        self.max_retries = max_retries

    async def parse(
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
        """调用 LLM 并校验输出。

        流程：
        1. 调用 LLMClient.generate_structured
        2. 如果 client 已返回 parsed（如 FakeLLM），直接返回
        3. 如果有 error_code，直接返回
        4. 否则尝试 Pydantic 校验——失败则重试

        Args:
            schema: 期望输出的 Pydantic 模型类
            messages: 对话消息列表
            **kwargs: 传递给 LLMClient 的额外参数

        Returns:
            LLMCallResult（parsed 为校验成功的结构化对象或 None）
        """
        # 尝试 1 + retries 次
        for attempt in range(1, self.max_retries + 2):
            # 构造重试消息
            retry_messages = list(messages)
            if attempt > 1:
                retry_messages.append({
                    "role": "system",
                    "content": "前一次输出校验失败，请严格按照 Schema 重新生成。",
                })

            result = await self.client.generate_structured(
                schema,
                retry_messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
                attempt=attempt,
                **kwargs,
            )
            result.attempt = attempt

            # client 已经返回了错误
            if result.error_code:
                return result

            # client 已经校验过了（如 FakeLLM）
            if result.parsed is not None:
                return result

            # 尝试校验原始文本
            try:
                parsed = schema.model_validate_json(result.content)
                result.parsed = parsed
                return result
            except (ValidationError, ValueError) as e:
                if attempt > self.max_retries:
                    result.error_code = LLMErrorCode.INVALID_OUTPUT
                    result.error_detail = f"校验失败（已重试 {self.max_retries} 次）: {e}"
                    return result
                # 继续重试

        # 不应到达这里（循环覆盖了所有情况）
        return result
