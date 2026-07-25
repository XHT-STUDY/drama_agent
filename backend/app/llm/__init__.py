"""DramaAgent LLM 抽象层。

提供：
- LLMClient Protocol：统一的 LLM 调用接口
- FakeLLM：测试用确定性假 LLM（fixture 路由 + 故障注入）
- OpenAICompatibleLLM：真实 LLM API 客户端
- StructuredOutputParser：Pydantic 校验 + 重试
- LLM 调用追踪模型（用量、耗时、错误码）
"""

from app.llm.fake import FakeLLM
from app.llm.models import LLMCallResult, LLMErrorCode, LLMUsage
from app.llm.openai_compatible import OpenAICompatibleLLM, load_llm_client
from app.llm.protocol import LLMClient
from app.llm.structured_output import StructuredOutputParser

__all__ = [
    "LLMClient",
    "FakeLLM",
    "OpenAICompatibleLLM",
    "load_llm_client",
    "StructuredOutputParser",
    "LLMCallResult",
    "LLMUsage",
    "LLMErrorCode",
]
