"""LLM 调用追踪模型。

定义每次 LLM 调用的结构：
- LLMUsage：token 用量
- LLMCallResult：调用结果（含校验状态）
- LLMErrorCode：错误分类枚举
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class LLMErrorCode(StrEnum):
    """LLM 调用错误码分类。"""

    LLM_TIMEOUT = "llm_timeout"
    INVALID_OUTPUT = "invalid_output"
    RATE_LIMITED = "rate_limited"
    PROVIDER_ERROR = "provider_error"


class LLMUsage(BaseModel):
    """Token 用量统计。"""

    model_config = {"extra": "forbid"}

    prompt_tokens: int = Field(default=0, ge=0, description="输入 token 数")
    completion_tokens: int = Field(default=0, ge=0, description="输出 token 数")
    total_tokens: int = Field(default=0, ge=0, description="总计 token 数")


class LLMCallResult(BaseModel):
    """单次 LLM 调用结果。

    包含原始输出、校验后的结构化对象、用量和耗时。
    """

    model_config = {"extra": "forbid"}

    content: str = Field(default="", description="LLM 原始文本输出")
    parsed: Any = Field(default=None, description="Pydantic Schema 校验后的结构化对象")
    usage: LLMUsage = Field(default_factory=LLMUsage, description="Token 用量")
    model: str = Field(default="", description="实际使用的模型名")
    duration_ms: int = Field(default=0, description="调用耗时（毫秒）")
    attempt: int = Field(default=1, ge=1, description="第几次尝试")
    error_code: str | None = Field(default=None, description="失败时的错误码")
    error_detail: str = Field(default="", description="失败时的错误详情")
