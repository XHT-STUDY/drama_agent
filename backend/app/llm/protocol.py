"""LLMClient Protocol — 统一 LLM 调用接口。

所有 LLM 实现（真实 API、FakeLLM）必须实现此协议。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from app.llm.models import LLMCallResult


class LLMClient(ABC):
    """LLM 客户端抽象协议。

    真实 LLM 和 FakeLLM 均实现此接口，
    确保业务代码不依赖具体实现。
    """

    @abstractmethod
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
        """调用 LLM 并返回经 Pydantic Schema 校验的结构化结果。

        Args:
            schema: 期望输出的 Pydantic v2 模型类
            messages: 对话消息列表 [{"role": "user", "content": "..."}, ...]
            model: 模型名（空则用默认）
            temperature: 生成温度
            max_tokens: 最大输出 token
            timeout_seconds: 超时时间

        Returns:
            LLMCallResult（含原始文本和校验后的结构化对象）

        Raises:
            不抛异常——所有错误写入 result.error_code。
        """
        ...

    @abstractmethod
    def get_call_history(self) -> list[LLMCallResult]:
        """获取调用历史记录（用于测试断言）。"""
        ...
