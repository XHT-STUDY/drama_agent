"""LLM max_tokens 配置回退与输出截断快速失败测试（story_bible 实测截断事故）。

- generate_structured 未传 max_tokens → 使用 settings.llm_max_tokens
  （此前硬编码 4096，大 JSON 被静默截断 → Schema 校验重试确定性失败）；
- 显式传参仍优先生效（planner 1600 / outcome 1200 等护栏不受影响）；
- finish_reason=length → 立即返回 OUTPUT_TRUNCATED，且不进入 parse 重试；
- timeout 的 Settings 回退在 BaseAgent → parser → client 全链路生效
  （此前 base/parser 硬编码 180s 把 .env 配置挡住）。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from app.agents.base import BaseAgent
from app.core.config import Settings
from app.llm.models import LLMErrorCode
from app.llm.openai_compatible import OpenAICompatibleLLM
from app.llm.retry import LLM_ERROR_RUN_CODES


class _Out(BaseModel):
    ok: bool


def _client(**kw: Any) -> OpenAICompatibleLLM:
    defaults: dict[str, Any] = {
        "app_env": "local",
        "llm_provider": "openai_compatible",
        "llm_api_key": "test-key",
        "llm_api_base": "http://test",
        "llm_max_tokens": 8192,
        "llm_timeout_seconds": 360,
    }
    defaults.update(kw)
    return OpenAICompatibleLLM(Settings(**defaults))


def _patch_post(
    llm: OpenAICompatibleLLM,
    captured: dict[str, Any],
    *,
    response: dict[str, Any] | None = None,
) -> None:
    """替换底层 httpx post，捕获请求 payload 并返回 200 响应。"""

    class _Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return response or {
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    async def _post(url: str, json: dict[str, Any] | None = None,
                    timeout: Any = None, **kw: Any) -> _Resp:
        captured["payload"] = json
        captured["read_timeout"] = timeout.read
        captured["calls"] = captured.get("calls", 0) + 1
        return _Resp()

    assert llm.client is not None, "client 应已惰性构造"
    llm.client.post = _post  # type: ignore[assignment,method-assign]


_TRUNCATED_RESPONSE = {
    "choices": [{
        "message": {"content": '{"title": "青训弃子绿茵逆袭", "logline": "被青训'},
        "finish_reason": "length",
    }],
    "usage": {"prompt_tokens": 2498, "completion_tokens": 4096,
              "total_tokens": 6594},
}


@pytest.mark.unit
@pytest.mark.asyncio
class TestMaxTokensFallback:
    async def test_settings_max_tokens_used_when_param_omitted(self) -> None:
        """未传参 → settings.llm_max_tokens（8192）生效，不再是硬编码 4096。"""
        llm = _client()
        captured: dict[str, Any] = {}
        _patch_post(llm, captured)

        result = await llm.generate_structured(_Out, [{"role": "user", "content": "x"}])
        assert result.error_code is None
        assert captured["payload"]["max_tokens"] == 8192

    async def test_explicit_max_tokens_still_wins(self) -> None:
        """显式传 1600 → 1600 优先于 settings（planner 等护栏不变）。"""
        llm = _client()
        captured: dict[str, Any] = {}
        _patch_post(llm, captured)

        await llm.generate_structured(
            _Out, [{"role": "user", "content": "x"}], max_tokens=1600
        )
        assert captured["payload"]["max_tokens"] == 1600

    async def test_finish_reason_length_returns_output_truncated(self) -> None:
        """finish_reason=length → OUTPUT_TRUNCATED，usage 保留（预算照常计数）。"""
        llm = _client()
        captured: dict[str, Any] = {}
        _patch_post(llm, captured, response=_TRUNCATED_RESPONSE)

        result = await llm.generate_structured(_Out, [{"role": "user", "content": "x"}])
        assert result.error_code == LLMErrorCode.OUTPUT_TRUNCATED
        assert result.parsed is None
        assert result.usage.completion_tokens == 4096
        assert "max_tokens" in result.error_detail

    async def test_truncated_output_not_retried_by_parser(self) -> None:
        """截断是确定性失败：BaseAgent → parser 全链路只发 1 次请求，不烧重试。"""
        llm = _client()
        captured: dict[str, Any] = {}
        _patch_post(llm, captured, response=_TRUNCATED_RESPONSE)
        agent = BaseAgent(name="story_bible", llm=llm)

        result = await agent.generate_structured(_Out, [{"role": "user", "content": "x"}])
        assert result.error_code == LLMErrorCode.OUTPUT_TRUNCATED
        assert captured["calls"] == 1

    async def test_truncated_code_maps_to_run_error_code(self) -> None:
        """截断错误码能映射为 run 层机器可读错误码（落库 / GET /runs）。"""
        assert (
            LLM_ERROR_RUN_CODES[LLMErrorCode.OUTPUT_TRUNCATED.value]
            == "LLM_OUTPUT_TRUNCATED"
        )

    async def test_settings_timeout_reaches_client_through_agent_chain(self) -> None:
        """BaseAgent → parser → client 全链路未传 timeout → settings（360）生效。

        此前 base/parser 硬编码 180s 默认值，把 .env 的 LLM_TIMEOUT_SECONDS 挡住。
        """
        llm = _client()
        captured: dict[str, Any] = {}
        _patch_post(llm, captured)
        agent = BaseAgent(name="story_bible", llm=llm)

        result = await agent.generate_structured(_Out, [{"role": "user", "content": "x"}])
        assert result.error_code is None
        assert captured["read_timeout"] == 360
