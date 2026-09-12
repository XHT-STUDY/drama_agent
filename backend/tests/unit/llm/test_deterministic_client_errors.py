"""确定性客户端错误快速失败测试（P0-1 修订链路瘫痪回归）。

事故链（2026-09-11 实测）：.env 缺 LLM_REVISER_MODEL → _resolve_model
兜底到硬编码 gpt-4o → deepseek 兼容端点 404 Model not exist → 404 被归为
可重试 provider_error → 确定性配置错误白烧 3 次重试后 Run 失败，
revise_script 意图 100% 不可用。

修复契约：
1. 401/403/404 及其他 4xx → INVALID_REQUEST（不可重试，不走指数退避）；
2. 角色模型与全局 LLM_MODEL 均未配置时不发 HTTP 请求，直接 INVALID_REQUEST；
3. 废除硬编码 gpt-4o 兜底，回退链 = 角色模型 → LLM_MODEL → 快速失败；
4. invalid_request 文本可被 classify_error_code 映射为 run 层 LLM_INVALID_REQUEST。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.llm.models import LLMCallResult, LLMErrorCode
from app.llm.openai_compatible import OpenAICompatibleLLM
from app.llm.retry import is_retryable
from app.workflows.checkpoint import classify_error_code


class _Out(BaseModel):
    ok: bool


def _settings(**overrides: Any) -> Settings:
    params: dict[str, Any] = {
        "app_env": "local",
        "llm_provider": "openai_compatible",
        "llm_api_key": "test-key",
        "llm_api_base": "http://test",
        "llm_retry_base_delay_seconds": 0.0,
    }
    params.update(overrides)
    return Settings(**params)


def _error_response(status: int) -> httpx.Response:
    """构造对应状态码的错误响应。"""
    request = httpx.Request("POST", "http://test/chat/completions")
    return httpx.Response(
        status,
        request=request,
        json={"error": {"message": "mock", "code": "mock_code"}},
    )


def _patch_post(
    llm: OpenAICompatibleLLM,
    responder: Any,
    calls: list[dict[str, Any]],
) -> None:
    """替换底层 httpx post，记录每次调用的 payload。"""

    async def _post(url: str, json: dict[str, Any] | None = None, **kw: Any) -> Any:
        calls.append({"url": url, "payload": json})
        return responder()

    assert llm.client is not None, "client 应已惰性构造"
    llm.client.post = _post  # type: ignore[assignment,method-assign]


@pytest.mark.unit
class TestClientErrorMapping:
    """4xx 确定性错误 → INVALID_REQUEST（不可重试）。"""

    def test_404_maps_to_invalid_request(self) -> None:
        """404（模型/端点不存在）→ INVALID_REQUEST，detail 指引模型配置。"""
        llm = OpenAICompatibleLLM(_settings())
        result = llm._handle_error_response(_error_response(404), 100, "m")
        assert result.error_code == LLMErrorCode.INVALID_REQUEST
        assert "模型" in result.error_detail

    def test_401_maps_to_invalid_request(self) -> None:
        """401（认证失败）→ INVALID_REQUEST：确定性配置错误不该重试。"""
        llm = OpenAICompatibleLLM(_settings())
        result = llm._handle_error_response(_error_response(401), 100, "m")
        assert result.error_code == LLMErrorCode.INVALID_REQUEST

    def test_generic_4xx_maps_to_invalid_request(self) -> None:
        """400（如 max_tokens 超模型上限）→ INVALID_REQUEST 而非 INVALID_OUTPUT。"""
        llm = OpenAICompatibleLLM(_settings())
        result = llm._handle_error_response(_error_response(400), 100, "m")
        assert result.error_code == LLMErrorCode.INVALID_REQUEST

    def test_invalid_request_not_retryable(self) -> None:
        """INVALID_REQUEST 不在可重试集合（区别于 5xx/连接失败）。"""
        assert is_retryable(
            LLMCallResult(error_code=LLMErrorCode.INVALID_REQUEST)
        ) is False

    def test_50x_still_retryable_provider_error(self) -> None:
        """5xx 仍是可重试的 PROVIDER_ERROR（回归保护）。"""
        llm = OpenAICompatibleLLM(_settings())
        result = llm._handle_error_response(_error_response(502), 100, "m")
        assert result.error_code == LLMErrorCode.PROVIDER_ERROR
        assert is_retryable(result) is True


@pytest.mark.unit
@pytest.mark.asyncio
class TestNoRetryOnDeterministicError:
    async def test_404_single_attempt(self) -> None:
        """404 只尝试 1 次，不进入指数退避重试（旧代码 3 次）。"""
        llm = OpenAICompatibleLLM(_settings(llm_writer_model="w"))
        calls: list[dict[str, Any]] = []
        _patch_post(llm, lambda: _error_response(404), calls)

        result = await llm.generate_structured(
            _Out, [{"role": "user", "content": "x"}]
        )
        assert result.error_code == LLMErrorCode.INVALID_REQUEST
        assert len(calls) == 1


@pytest.mark.unit
@pytest.mark.asyncio
class TestModelResolutionFallback:
    async def test_unresolved_model_fails_fast_without_http(self) -> None:
        """角色与全局模型均未配置 → 不发请求，INVALID_REQUEST + 配置指引。"""
        llm = OpenAICompatibleLLM(_settings())
        calls: list[dict[str, Any]] = []

        async def _boom(**kw: Any) -> Any:
            raise AssertionError("模型未配置时不应发起 HTTP 请求")

        assert llm.client is not None
        llm.client.post = _boom  # type: ignore[assignment,method-assign]

        result = await llm.generate_structured(
            _Out,
            [{"role": "user", "content": "x"}],
            prompt_name="revision_plan",
        )
        assert result.error_code == LLMErrorCode.INVALID_REQUEST
        assert "LLM_REVISER_MODEL" in result.error_detail
        assert calls == []

    async def test_role_model_falls_back_to_global_llm_model(self) -> None:
        """角色模型为空时回退全局 LLM_MODEL（不再是 gpt-4o）。"""
        llm = OpenAICompatibleLLM(_settings(llm_model="global-model"))
        calls: list[dict[str, Any]] = []

        def _ok() -> Any:
            class _Resp:
                status_code = 200
                headers: dict[str, str] = {}

                def json(self) -> dict[str, Any]:
                    return {
                        "choices": [{"message": {"content": '{"ok": true}'}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    }

            return _Resp()

        _patch_post(llm, _ok, calls)

        result = await llm.generate_structured(
            _Out,
            [{"role": "user", "content": "x"}],
            prompt_name="write_episode",
        )
        assert result.error_code is None
        assert calls[0]["payload"]["model"] == "global-model"


@pytest.mark.unit
class TestModelResolutionPure:
    def test_no_hardcoded_gpt4o_fallback(self) -> None:
        """全空配置下 _resolve_model 返回空串，绝不返回 gpt-4o。"""
        llm = OpenAICompatibleLLM(_settings())
        assert llm._resolve_model("revision_plan") == ""
        assert llm._resolve_model("write_episode") == ""


@pytest.mark.unit
class TestRunErrorCodeClassification:
    def test_invalid_request_maps_to_run_code(self) -> None:
        """skill 异常文本中的 invalid_request → run 层 LLM_INVALID_REQUEST。"""
        exc = RuntimeError(
            "RevisionPlan Skill LLM 调用失败: invalid_request - 端点或模型不存在"
        )
        assert classify_error_code(exc) == "LLM_INVALID_REQUEST"
