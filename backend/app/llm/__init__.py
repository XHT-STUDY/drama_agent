"""DramaAgent LLM 抽象层。

提供：
- LLMClient Protocol：统一的 LLM 调用接口
- FakeLLM：测试用确定性假 LLM（fixture 路由 + 故障注入）
- StructuredOutputParser：Pydantic 校验 + 重试
- LLM 调用追踪模型（用量、耗时、错误码）
"""

from app.llm.fake import FakeLLM
from app.llm.models import LLMCallResult, LLMErrorCode, LLMUsage
from app.llm.protocol import LLMClient
from app.llm.structured_output import StructuredOutputParser

__all__ = [
    "LLMClient",
    "FakeLLM",
    "StructuredOutputParser",
    "LLMCallResult",
    "LLMUsage",
    "LLMErrorCode",
]
