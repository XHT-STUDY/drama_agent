"""LLM 超时配置回退测试（用户实测 outline 超时根因修复）。

- generate_structured 未传 timeout_seconds → 使用 settings.llm_timeout_seconds
  （此前硬编码 180s 覆盖 .env 配置）；
- 显式传参仍优先生效。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.llm.openai_compatible import OpenAICompatibleLLM


class _Out(BaseModel):
    ok: bool


def _client(timeout: int) -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(
        Settings(app_env="local", llm_provider="openai_compatible",
                 llm_api_key="test-key", llm_api_base="http://test",
                 llm_model="test-model",
                 llm_timeout_seconds=timeout)
    )


def _patch_post(llm: OpenAICompatibleLLM, captured: dict[str, Any]) -> None:
    """替换底层 httpx post，捕获 per-request Timeout 并返回 200 响应。"""

    class _Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": '{"ok": true}'}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    async def _post(url: str, json: dict[str, Any] | None = None,
                    timeout: Any = None, **kw: Any) -> _Resp:
        captured["read"] = timeout.read
        return _Resp()

    assert llm.client is not None, "client 应已惰性构造"
    llm.client.post = _post  # type: ignore[assignment,method-assign]


@pytest.mark.unit
@pytest.mark.asyncio
class TestTimeoutFallback:
    async def test_settings_timeout_used_when_param_omitted(self) -> None:
        """未传参 → settings.llm_timeout_seconds（360）生效，不再是硬编码 180。"""
        llm = _client(timeout=360)
        captured: dict[str, Any] = {}
        _patch_post(llm, captured)

        result = await llm.generate_structured(_Out, [{"role": "user", "content": "x"}])
        assert result.error_code is None
        assert captured["read"] == 360

    async def test_explicit_param_still_wins(self) -> None:
        """显式传 60 → 60 优先于 settings。"""
        llm = _client(timeout=360)
        captured: dict[str, Any] = {}
        _patch_post(llm, captured)

        await llm.generate_structured(
            _Out, [{"role": "user", "content": "x"}], timeout_seconds=60
        )
        assert captured["read"] == 60
