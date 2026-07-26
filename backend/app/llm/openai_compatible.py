"""OpenAICompatibleLLM — OpenAI 兼容 API 的真实 LLM 客户端。

用途：
- 实现 LLMClient 协议，调用真实 LLM API
- 支持所有 OpenAI 兼容格式的 API（如 Anthropic 代理、vLLM、Ollama 等）
- 将 Pydantic Schema 注入 System Prompt 实现结构化输出
- 记录每次调用的 token 用量和耗时

配置（见 Settings / .env）：
- LLM_API_BASE：API 地址（如 https://api.openai.com）
- LLM_API_KEY：API 密钥
- LLM_*_MODEL：各角色的模型名
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from pydantic import BaseModel

from app.core.config import Settings
from app.llm.models import LLMCallResult, LLMErrorCode, LLMUsage
from app.llm.protocol import LLMClient

logger = logging.getLogger(__name__)

# 默认角色 → 模型名映射（可在 Settings 中覆盖）
_ROLE_MODEL_ATTRS = {
    "normalizer": "llm_normalizer_model",
    "planner": "llm_planner_model",
    "writer": "llm_writer_model",
    "evaluator": "llm_evaluator_model",
    "summarizer": "llm_summarizer_model",
}


class OpenAICompatibleLLM(LLMClient):
    """OpenAI 兼容 API 客户端。

    调用 /v1/chat/completions 端点，支持 OpenAI 和所有兼容服务。

    使用方式：
        settings = Settings()
        llm = OpenAICompatibleLLM(settings)
        result = await llm.generate_structured(ScriptDraft, messages, model="gpt-4o")
    """

    def __init__(
        self,
        settings: Settings,
        *,
        default_model: str = "gpt-4o",
    ) -> None:
        """初始化 OpenAI 兼容客户端。

        Args:
            settings: DramaAgent Settings 实例
            default_model: 未指定模型时的默认模型名
        """
        self.settings = settings
        self.default_model = default_model

        # httpx 客户端（惰性创建）
        self._client: httpx.AsyncClient | None = None
        self._call_history: list[LLMCallResult] = []

    @property
    def client(self) -> httpx.AsyncClient:
        """惰性创建 httpx 客户端。"""
        if self._client is None:
            headers: dict[str, str] = {
                "Content-Type": "application/json",
            }
            if self.settings.llm_api_key:
                headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"

            timeout = httpx.Timeout(
                connect=30.0,
                read=self.settings.llm_timeout_seconds,
                write=30.0,
                pool=10.0,
            )

            self._client = httpx.AsyncClient(
                base_url=self.settings.llm_api_base,
                headers=headers,
                timeout=timeout,
            )
        return self._client

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
        """调用 OpenAI 兼容 API 并返回结构化结果。

        Schema 信息会被注入到 system prompt 中，
        要求 LLM 输出纯 JSON（不借助 tool calling）。
        """
        start = time.monotonic()

        # 解析模型名
        resolved_model = model or self._resolve_model(kwargs.get("prompt_name", ""))
        if not resolved_model:
            resolved_model = self.default_model

        # 注入 Schema 到 messages
        schema_json = json.dumps(
            schema.model_json_schema(), ensure_ascii=False, indent=2
        )
        augmented_messages = self._inject_schema(messages, schema, schema_json)

        # 构建请求体
        payload = {
            "model": resolved_model,
            "messages": augmented_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        # 尝试调用 API
        try:
            response = await self.client.post(
                "/chat/completions",
                json=payload,
                timeout=httpx.Timeout(
                    connect=30.0,
                    read=timeout_seconds,
                    write=30.0,
                    pool=10.0,
                ),
            )

            duration_ms = int((time.monotonic() - start) * 1000)

            if response.status_code == 200:
                return self._handle_success_response(
                    response, schema, duration_ms, resolved_model
                )
            else:
                return self._handle_error_response(
                    response, duration_ms, resolved_model
                )

        except httpx.TimeoutException:
            duration_ms = int((time.monotonic() - start) * 1000)
            result = LLMCallResult(
                model=resolved_model,
                duration_ms=duration_ms,
                error_code=LLMErrorCode.LLM_TIMEOUT,
                error_detail=f"请求超时（{timeout_seconds}s）",
            )
            self._call_history.append(result)
            return result

        except httpx.ConnectError as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            result = LLMCallResult(
                model=resolved_model,
                duration_ms=duration_ms,
                error_code=LLMErrorCode.PROVIDER_ERROR,
                error_detail=f"连接失败: {e}",
            )
            self._call_history.append(result)
            return result

        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            result = LLMCallResult(
                model=resolved_model,
                duration_ms=duration_ms,
                error_code=LLMErrorCode.PROVIDER_ERROR,
                error_detail=f"未知错误: {type(e).__name__}: {e}",
            )
            self._call_history.append(result)
            return result

    # ---- 内部方法 ----

    def _handle_success_response(
        self,
        response: httpx.Response,
        schema: type[BaseModel],
        duration_ms: int,
        model: str,
    ) -> LLMCallResult:
        """解析成功响应。"""
        data = response.json()

        # 提取 content 文本
        choices = data.get("choices", [])
        if not choices:
            result = LLMCallResult(
                model=model,
                duration_ms=duration_ms,
                error_code=LLMErrorCode.INVALID_OUTPUT,
                error_detail="API 返回空 choices",
            )
            self._call_history.append(result)
            return result

        content = choices[0].get("message", {}).get("content", "")

        # 提取 token 用量
        usage_data = data.get("usage", {})
        usage = LLMUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )

        # 提取 JSON 文本（处理 ```json 代码块包裹的情况）
        json_text = _extract_json(content)

        # 尝试验证（失败时让 StructuredOutputParser 处理重试）
        try:
            parsed = schema.model_validate_json(json_text)
            result = LLMCallResult(
                content=json_text,
                parsed=parsed,
                usage=usage,
                model=model,
                duration_ms=duration_ms,
                attempt=1,
            )
        except Exception:
            # 不在这里重试——返回 raw content，由 StructuredOutputParser 处理
            result = LLMCallResult(
                content=json_text,
                usage=usage,
                model=model,
                duration_ms=duration_ms,
                attempt=1,
            )

        self._call_history.append(result)
        return result

    def _handle_error_response(
        self,
        response: httpx.Response,
        duration_ms: int,
        model: str,
    ) -> LLMCallResult:
        """映射 API 错误。"""
        status = response.status_code
        try:
            error_detail = response.json()
        except Exception:
            error_detail = response.text[:500]

        if status == 429:
            error_code = LLMErrorCode.RATE_LIMITED
            error_msg = "请求频率超限"
        elif status == 401 or status == 403:
            error_code = LLMErrorCode.PROVIDER_ERROR
            error_msg = f"认证失败 (HTTP {status})"
        elif status == 404:
            error_code = LLMErrorCode.PROVIDER_ERROR
            error_msg = "端点或模型不存在 (HTTP 404)"
        elif 400 <= status < 500:
            error_code = LLMErrorCode.INVALID_OUTPUT
            error_msg = f"请求参数错误 (HTTP {status})"
        else:
            error_code = LLMErrorCode.PROVIDER_ERROR
            error_msg = f"服务端错误 (HTTP {status})"

        logger.error(
            "LLM API 错误: status=%d code=%s detail=%s", status, error_code, error_detail
        )

        result = LLMCallResult(
            model=model,
            duration_ms=duration_ms,
            error_code=error_code,
            error_detail=f"{error_msg}: {error_detail}",
        )
        self._call_history.append(result)
        return result

    def _inject_schema(
        self,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
        schema_json: str,
    ) -> list[dict[str, str]]:
        """将 Pydantic Schema 注入到消息列表中。

        在第一条 system 消息后追加 Schema 说明，
        要求 LLM 输出符合 Schema 的 JSON。
        """
        schema_instruction = {
            "role": "system",
            "content": (
                "你必须严格按照以下 JSON Schema 输出。"
                "只输出 JSON，不要包含解释、markdown 标记或其他文本。\n\n"
                f"```json\n{schema_json}\n```"
            ),
        }

        augmented = list(messages)

        # 找到最后一条 system 消息，在其后插入
        insert_at = 0
        for i, msg in enumerate(augmented):
            if msg.get("role") == "system":
                insert_at = i + 1

        augmented.insert(insert_at, schema_instruction)
        return augmented

    def _resolve_model(self, prompt_name: str) -> str:
        """根据 prompt_name 解析应该使用的模型。

        通过 prompt_name 推断角色，再从 Settings 读取对应模型名。
        """
        # prompt_name → 角色映射
        name_to_role = {
            "normalize_requirement": "normalizer",
            "story_bible": "planner",
            "outline": "planner",
            "write_episode": "writer",
            "evaluate_episode": "evaluator",
            "summarize_episode": "summarizer",
        }

        role = name_to_role.get(prompt_name, "writer")
        attr = _ROLE_MODEL_ATTRS.get(role, "llm_writer_model")
        model = getattr(self.settings, attr, "")
        return model or self.default_model

    # ---- 工具方法 ----

    def get_call_history(self) -> list[LLMCallResult]:
        """获取调用历史记录。"""
        return list(self._call_history)

    def reset(self) -> None:
        """重置调用历史和客户端。"""
        self._call_history.clear()

    async def close(self) -> None:
        """关闭 httpx 客户端。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ========================================================================
# 辅助函数
# ========================================================================


def _extract_json(text: str) -> str:
    """从 LLM 输出中提取 JSON 文本。

    处理 LLM 常见的包装方式：
    - ```json ... ```
    - ``` ... ```
    - 纯 JSON 文本
    """
    text = text.strip()

    # 尝试提取 ```json 代码块
    if text.startswith("```"):
        # 找到第一个换行后的内容（跳过 ```json 或 ``` 行）
        first_newline = text.find("\n")
        if first_newline > 0:
            text = text[first_newline + 1:]
        # 找到结尾的 ``` 并截断（只取代码块内容，丢弃后续文本）
        last_triple = text.rfind("\n```")
        if last_triple >= 0:
            text = text[:last_triple]
        elif text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]

    return text.strip()


def load_llm_client(settings: Settings | None = None) -> OpenAICompatibleLLM:
    """便捷工厂：从 Settings 创建 OpenAICompatibleLLM。

    如果 llm_provider 不是 openai_compatible，返回 None 表示需要其他实现。

    Args:
        settings: Settings 实例，为 None 时自动加载

    Returns:
        OpenAICompatibleLLM 实例
    """
    if settings is None:
        settings = Settings()

    if settings.app_env == "test" or settings.llm_provider == "fake":
        raise ValueError(
            f"当前环境/Provider ({settings.app_env}/{settings.llm_provider}) "
            f"不应使用真实 LLM。请用 FakeLLM。"
        )

    return OpenAICompatibleLLM(settings)
