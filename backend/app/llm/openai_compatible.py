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
from app.llm.budget import check_budget, record_call
from app.llm.models import LLMCallResult, LLMErrorCode, LLMUsage
from app.llm.protocol import LLMClient
from app.llm.retry import (
    RetryPolicy,
    error_code_label,
    execute_with_retry,
    parse_retry_after,
)
from app.observability.metrics import llm_calls_total, llm_token_usage_total
from app.observability.tracing import get_span

logger = logging.getLogger(__name__)

# 默认角色 → 模型名映射（可在 Settings 中覆盖）
_ROLE_MODEL_ATTRS = {
    "normalizer": "llm_normalizer_model",
    "planner": "llm_planner_model",
    "writer": "llm_writer_model",
    "evaluator": "llm_evaluator_model",
    "reviser": "llm_reviser_model",
    "summarizer": "llm_summarizer_model",
}


class OpenAICompatibleLLM(LLMClient):
    """OpenAI 兼容 API 客户端。

    调用 /v1/chat/completions 端点，支持 OpenAI 和所有兼容服务。

    使用方式：
        settings = Settings()
        llm = OpenAICompatibleLLM(settings)
        result = await llm.generate_structured(ScriptDraft, messages, model="your-model")
    """

    def __init__(
        self,
        settings: Settings,
        *,
        default_model: str = "",
    ) -> None:
        """初始化 OpenAI 兼容客户端。

        Args:
            settings: DramaAgent Settings 实例
            default_model: 未指定模型时的默认模型名；
                未传时回退 Settings.llm_model（全局 LLM_MODEL）。
                解析结果为空时调用前快速失败，不再硬编码第三方模型名——
                非 OpenAI 端点上必然 404（P0-1 修订链路事故根因）。
        """
        self.settings = settings
        self.default_model = default_model or settings.llm_model

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
        max_tokens: int = 0,
        timeout_seconds: int = 0,
        **kwargs: Any,
    ) -> LLMCallResult:
        """调用 OpenAI 兼容 API 并返回结构化结果。

        Schema 信息会被注入到 system prompt 中，
        要求 LLM 输出纯 JSON（不借助 tool calling）。

        I-01：在客户端层实现统一重试——
        - 可重试错误（429 / 超时 / 5xx / 连接失败）按 RetryPolicy 指数退避重试；
        - 尊重 429/503 的 Retry-After 头；
        - 每次真实尝试前检查 per-run 预算，超硬上限抛 BudgetExceededError。
        协议契约保持：不抛普通异常，错误写 result.error_code。
        """
        # 超时解析：未显式传参（0）时回退 Settings.llm_timeout_seconds——
        # 此前硬编码 180s 覆盖了 .env 配置（用户实测 outline 超时根因）
        effective_timeout = timeout_seconds or self.settings.llm_timeout_seconds

        # max_tokens 解析：未显式传参（0）时回退 Settings.llm_max_tokens——
        # 此前默认 4096 静默截断大 JSON（story_bible 实测根因），
        # 链路上的 parser/agent 默认值统一为 0 透传，显式传参仍优先
        effective_max_tokens = max_tokens or self.settings.llm_max_tokens

        # 解析模型名：显式传参 > 角色模型 > default_model（含 LLM_MODEL 兜底）。
        # 全链为空时快速失败——不硬编码第三方模型名（非 OpenAI 端点必然
        # 404 Model not exist，且确定性失败重试只是白烧预算）
        prompt_name = str(kwargs.get("prompt_name") or "-")
        resolved_model = model or self._resolve_model(prompt_name)
        if not resolved_model:
            logger.error(
                "LLM 模型未配置: prompt=%s（请设置对应 LLM_*_MODEL 或全局 LLM_MODEL）",
                prompt_name,
            )
            return LLMCallResult(
                model="",
                error_code=LLMErrorCode.INVALID_REQUEST,
                error_detail=(
                    f"模型未配置（prompt={prompt_name}）："
                    "请设置对应角色的 LLM_*_MODEL（如 LLM_REVISER_MODEL）"
                    "或全局 LLM_MODEL"
                ),
            )

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
            "max_tokens": effective_max_tokens,
            "stream": False,
        }

        policy = RetryPolicy(
            base_delay=self.settings.llm_retry_base_delay_seconds,
            factor=self.settings.llm_retry_factor,
            max_retries=self.settings.llm_max_retries,
            max_delay=self.settings.llm_retry_max_delay_seconds,
        )
        # 最近一次响应的 Retry-After（闭包捕获，重试时读取）
        retry_after: float | None = None

        logger.info(
            "LLM 调用开始: model=%s schema=%s prompt=%s max_tokens=%d timeout=%ds",
            resolved_model,
            schema.__name__,
            prompt_name,
            effective_max_tokens,
            effective_timeout,
        )
        logger.debug(
            "LLM 请求 messages: %d 条 (system=%d) temperature=%.2f",
            len(augmented_messages),
            sum(1 for m in augmented_messages if m.get("role") == "system"),
            temperature,
        )

        async def _attempt(_attempt_no: int) -> LLMCallResult:
            nonlocal retry_after
            # 预算硬上限检查（BudgetExceededError 不被下方 except 吞掉）
            check_budget()
            start = time.monotonic()
            try:
                response = await self.client.post(
                    "/chat/completions",
                    json=payload,
                    timeout=httpx.Timeout(
                        connect=30.0,
                        read=effective_timeout,
                        write=30.0,
                        pool=10.0,
                    ),
                )

                duration_ms = int((time.monotonic() - start) * 1000)

                if response.status_code == 200:
                    result = self._handle_success_response(
                        response, schema, duration_ms, resolved_model
                    )
                else:
                    retry_after = parse_retry_after(
                        response.headers.get("retry-after")
                    )
                    result = self._handle_error_response(
                        response, duration_ms, resolved_model
                    )

            except httpx.TimeoutException:
                duration_ms = int((time.monotonic() - start) * 1000)
                logger.warning(
                    "LLM 调用超时: model=%s prompt=%s attempt=%d timeout=%ds 耗时=%dms",
                    resolved_model,
                    prompt_name,
                    _attempt_no,
                    effective_timeout,
                    duration_ms,
                )
                result = LLMCallResult(
                    model=resolved_model,
                    duration_ms=duration_ms,
                    error_code=LLMErrorCode.LLM_TIMEOUT,
                    error_detail=f"请求超时（{effective_timeout}s）",
                )
                self._call_history.append(result)

            except httpx.ConnectError as e:
                duration_ms = int((time.monotonic() - start) * 1000)
                logger.warning(
                    "LLM 连接失败: model=%s prompt=%s attempt=%d api_base=%s 耗时=%dms",
                    resolved_model,
                    prompt_name,
                    _attempt_no,
                    self.settings.llm_api_base,
                    duration_ms,
                )
                result = LLMCallResult(
                    model=resolved_model,
                    duration_ms=duration_ms,
                    error_code=LLMErrorCode.PROVIDER_ERROR,
                    error_detail=f"连接失败: {e}",
                )
                self._call_history.append(result)

            except Exception as e:
                duration_ms = int((time.monotonic() - start) * 1000)
                logger.error(
                    "LLM 调用未知异常: model=%s prompt=%s attempt=%d 耗时=%dms: %s: %s",
                    resolved_model,
                    prompt_name,
                    _attempt_no,
                    duration_ms,
                    type(e).__name__,
                    e,
                )
                result = LLMCallResult(
                    model=resolved_model,
                    duration_ms=duration_ms,
                    error_code=LLMErrorCode.PROVIDER_ERROR,
                    error_detail=f"未知错误: {type(e).__name__}: {e}",
                )
                self._call_history.append(result)

            # 每次真实尝试的统一结果日志（成功与失败均记录耗时与用量）
            logger.info(
                "LLM 调用结束: model=%s prompt=%s attempt=%d 结果=%s 耗时=%dms "
                "tokens=p%d+c%d(t%d) schema校验=%s",
                resolved_model,
                prompt_name,
                _attempt_no,
                error_code_label(result.error_code) if result.error_code else "ok",
                result.duration_ms,
                result.usage.prompt_tokens,
                result.usage.completion_tokens,
                result.usage.total_tokens,
                "通过" if result.parsed is not None else "未通过",
            )

            # I-02：LLM 调用结果计数（node 取自 tracing 上下文；run_id 不入标签）
            llm_calls_total.inc(
                node=get_span().node_name or "unknown",
                model=resolved_model,
                status="ok" if result.error_code is None else error_code_label(result.error_code),
            )
            # I-02：token 用量（仅成功调用；error 时 usage 默认全 0，不计数）
            if result.usage.total_tokens:
                llm_token_usage_total.inc(
                    kind="prompt", value=result.usage.prompt_tokens
                )
                llm_token_usage_total.inc(
                    kind="completion", value=result.usage.completion_tokens
                )

            # 预算计数（含重试的每次真实尝试）
            record_call(
                prompt_tokens=result.usage.prompt_tokens,
                completion_tokens=result.usage.completion_tokens,
            )
            return result

        return await execute_with_retry(
            _attempt, policy, retry_after_fn=lambda _r: retry_after
        )

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

        # 截断检测：finish_reason=length 说明输出被 max_tokens 硬切，
        # JSON 必然不完整，且重试同样必然截断——快速失败（带 usage 供预算
        # 计数），不进入注定失败的 parse 重试（story_bible 实测白烧 3 次调用）
        if choices[0].get("finish_reason") == "length":
            result = LLMCallResult(
                content=content,
                usage=usage,
                model=model,
                duration_ms=duration_ms,
                error_code=LLMErrorCode.OUTPUT_TRUNCATED,
                error_detail=(
                    "输出在 max_tokens 上限处被截断（finish_reason=length），"
                    "JSON 不完整；可调大 LLM_MAX_TOKENS 或精简输出后重跑"
                ),
            )
            self._call_history.append(result)
            return result

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
            # 认证失败是确定性配置错误：重试必然同样失败（不进退避重试）
            error_code = LLMErrorCode.INVALID_REQUEST
            error_msg = f"认证失败 (HTTP {status})，请检查 LLM_API_KEY / LLM_API_BASE"
        elif status == 404:
            # 端点/模型不存在是确定性配置错误（P0-1 事故：reviser 角色
            # 未配置模型时兜底到不存在的模型名，404 被当可重试错误烧了 3 次）
            error_code = LLMErrorCode.INVALID_REQUEST
            error_msg = "端点或模型不存在 (HTTP 404)，请检查 LLM_*_MODEL / LLM_API_BASE"
        elif 400 <= status < 500:
            # 4xx 均为请求侧确定性错误（如 max_tokens 超模型上限），
            # 不是模型输出问题，重试无意义
            error_code = LLMErrorCode.INVALID_REQUEST
            error_msg = f"请求参数错误 (HTTP {status})"
        else:
            error_code = LLMErrorCode.PROVIDER_ERROR
            error_msg = f"服务端错误 (HTTP {status})"

        logger.error(
            "LLM API 错误: status=%d code=%s model=%s 耗时=%dms detail=%.500s",
            status, error_code, model, duration_ms, error_detail,
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
            "agent_command_planner": "planner",
            "artifact_explainer": "planner",
            "normalize_requirement": "normalizer",
            "story_bible": "planner",
            "outline": "planner",
            "outline_reviser": "planner",
            "write_episode": "writer",
            "evaluate_episode": "evaluator",
            "revision_plan": "reviser",
            "revise_episode": "reviser",
            "continuity_semantic_check": "reviser",
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
