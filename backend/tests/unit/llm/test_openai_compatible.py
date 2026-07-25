"""OpenAICompatibleLLM 单元测试。

测试范围:
- JSON 提取（```json 包裹 / 纯文本）
- Schema 注入到 messages
- API 响应处理（成功/错误）
- 故障映射（超时/连接/认证）
- _resolve_model 角色映射
- 调用历史记录
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.llm.models import LLMErrorCode
from app.llm.openai_compatible import (
    OpenAICompatibleLLM,
    _extract_json,
    load_llm_client,
)

# ========================================================================
# 测试 Schema
# ========================================================================


class _TestOutput(BaseModel):
    title: str
    score: int = Field(default=0, ge=0, le=100)


# ========================================================================
# Fixtures
# ========================================================================


@pytest.fixture
def settings() -> Settings:
    """创建 local 环境 Settings（不会强制 fake）。"""
    s = Settings(
        app_env="local",
        llm_provider="openai_compatible",
        llm_api_base="https://test-api.example.com",
        llm_api_key="sk-test-key",
        llm_writer_model="test-writer-model",
    )
    return s


@pytest.fixture
def llm(settings: Settings) -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(settings)


# ========================================================================
# JSON 提取
# ========================================================================


class TestExtractJson:
    """_extract_json 辅助函数测试。"""

    def test_pure_json_passthrough(self) -> None:
        """纯 JSON 文本原样返回。"""
        text = '{"title": "test", "score": 95}'
        assert _extract_json(text) == text

    def test_json_with_code_block(self) -> None:
        """```json 代码块被剥离。"""
        text = '```json\n{"title": "test", "score": 95}\n```'
        result = _extract_json(text)
        assert result == '{"title": "test", "score": 95}'

    def test_json_with_plain_code_block(self) -> None:
        """``` 代码块（不带语言标记）被剥离。"""
        text = '```\n{"title": "test"}\n```'
        result = _extract_json(text)
        assert result == '{"title": "test"}'

    def test_whitespace_trimmed(self) -> None:
        """前后空白被去除。"""
        text = '\n\n  {"title": "test"}  \n\n'
        result = _extract_json(text)
        assert result == '{"title": "test"}'

    def test_nested_json(self) -> None:
        """嵌套 JSON 正常提取。"""
        inner = json.dumps({"items": [{"a": 1}, {"b": 2}], "count": 2})
        assert _extract_json(inner) == inner

    def test_json_with_extra_text(self) -> None:
        """```json 标记后跟额外文本仍正确提取 JSON。"""
        text = '```json\n{"title": "ok"}\n```\n顺便说一句...'
        result = _extract_json(text)
        assert result == '{"title": "ok"}'


# ========================================================================
# Schema 注入
# ========================================================================


class TestInjectSchema:
    """_inject_schema 方法测试。"""

    def test_injects_after_system_message(self, llm: OpenAICompatibleLLM) -> None:
        """Schema 指令插入到 system 消息之后。"""
        messages: list[dict[str, str]] = [
            {"role": "system", "content": "你是一个写作助手。"},
            {"role": "user", "content": "写剧本"},
        ]
        schema_json = '{"type": "object"}'
        result = llm._inject_schema(messages, _TestOutput, schema_json)

        # 应有 3 条消息
        assert len(result) == 3
        # 第 1 条：原 system
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "你是一个写作助手。"
        # 第 2 条：Schema 指令
        assert result[1]["role"] == "system"
        assert "JSON Schema" in result[1]["content"]
        assert '{"type": "object"}' in result[1]["content"]
        # 第 3 条：原 user
        assert result[2]["role"] == "user"

    def test_injects_at_end_when_no_system(self, llm: OpenAICompatibleLLM) -> None:
        """无 system 消息时，Schema 插入到开头。"""
        messages: list[dict[str, str]] = [
            {"role": "user", "content": "写剧本"},
        ]
        result = llm._inject_schema(messages, _TestOutput, "{}")

        assert len(result) == 2
        assert result[0]["role"] == "system"

    def test_injects_after_last_system(self, llm: OpenAICompatibleLLM) -> None:
        """多条 system 消息时，插入到最后一条 system 之后。"""
        messages: list[dict[str, str]] = [
            {"role": "system", "content": "规则 1"},
            {"role": "system", "content": "规则 2"},
            {"role": "user", "content": "请求"},
        ]
        result = llm._inject_schema(messages, _TestOutput, "{}")

        assert len(result) == 4
        assert result[2]["role"] == "system"
        assert "JSON Schema" in result[2]["content"]
        assert result[3]["role"] == "user"


# ========================================================================
# 模型解析
# ========================================================================


class TestResolveModel:
    """_resolve_model 角色→模型映射测试。"""

    def test_resolves_writer_model(self, llm: OpenAICompatibleLLM) -> None:
        """write_episode → writer 角色 → llm_writer_model。"""
        model = llm._resolve_model("write_episode")
        assert model == "test-writer-model"

    def test_resolves_planner_model(self, llm: OpenAICompatibleLLM) -> None:
        """story_bible → planner 角色。"""
        llm.settings.llm_planner_model = "gpt-4-planner"
        model = llm._resolve_model("story_bible")
        assert model == "gpt-4-planner"

    def test_unknown_prompt_maps_to_writer_role(self, llm: OpenAICompatibleLLM) -> None:
        """未知 prompt_name 默认映射到 writer 角色模型。"""
        model = llm._resolve_model("unknown_prompt")
        assert model == "test-writer-model"

    def test_empty_prompt_name_uses_writer_role(self, llm: OpenAICompatibleLLM) -> None:
        """空 prompt_name 默认映射到 writer 角色模型。"""
        model = llm._resolve_model("")
        assert model == "test-writer-model"

    def test_falls_back_to_default_when_writer_model_empty(self, llm: OpenAICompatibleLLM) -> None:
        """writer 模型未配置时回退到 default_model。"""
        llm.settings.llm_writer_model = ""
        model = llm._resolve_model("unknown_prompt")
        assert model == llm.default_model


# ========================================================================
# 响应处理（mock HTTP）
# ========================================================================


class TestHandleSuccessResponse:
    """_handle_success_response 测试。"""

    @pytest.fixture
    def mock_response(self) -> MagicMock:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        return resp

    def test_parses_content_and_usage(self, llm: OpenAICompatibleLLM, mock_response: MagicMock) -> None:
        """正确解析 API 返回的 content 和 usage。"""
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"title": "Hello", "score": 95}',
                    },
                },
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }

        result = llm._handle_success_response(
            mock_response, _TestOutput, 1234, "test-model"
        )

        assert result.content == '{"title": "Hello", "score": 95}'
        assert result.parsed is not None
        assert result.parsed.title == "Hello"
        assert result.parsed.score == 95
        assert result.usage.prompt_tokens == 100
        assert result.usage.completion_tokens == 50
        assert result.usage.total_tokens == 150
        assert result.model == "test-model"
        assert result.duration_ms == 1234

    def test_strips_code_block(self, llm: OpenAICompatibleLLM, mock_response: MagicMock) -> None:
        """JSON 被代码块包裹时也能正确解析。"""
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '```json\n{"title": "Wrapped", "score": 80}\n```',
                    },
                },
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        result = llm._handle_success_response(
            mock_response, _TestOutput, 100, "test"
        )

        assert result.parsed is not None
        assert result.parsed.title == "Wrapped"
        assert result.parsed.score == 80
    
    def test_invalid_json_returns_content_only(
        self, llm: OpenAICompatibleLLM, mock_response: MagicMock,
    ) -> None:
        """非法 JSON 时 parsed 为 None，保留 content 供重试。"""
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "这不是合法的 JSON",
                    },
                },
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }

        result = llm._handle_success_response(
            mock_response, _TestOutput, 100, "test"
        )

        assert result.parsed is None
        assert result.content == "这不是合法的 JSON"
        assert result.error_code is None  # StructuredOutputParser 会处理

    def test_empty_choices(self, llm: OpenAICompatibleLLM, mock_response: MagicMock) -> None:
        """API 返回空 choices 时映射为 INVALID_OUTPUT。"""
        mock_response.json.return_value = {"choices": []}

        result = llm._handle_success_response(
            mock_response, _TestOutput, 100, "test"
        )

        assert result.error_code == LLMErrorCode.INVALID_OUTPUT


class TestHandleErrorResponse:
    """_handle_error_response 测试。"""

    @pytest.fixture
    def mock_response(self) -> MagicMock:
        resp = MagicMock(spec=httpx.Response)
        return resp

    def test_rate_limited(self, llm: OpenAICompatibleLLM, mock_response: MagicMock) -> None:
        """429 → RATE_LIMITED。"""
        mock_response.status_code = 429
        mock_response.json.return_value = {"error": "too many requests"}

        result = llm._handle_error_response(mock_response, 500, "test")

        assert result.error_code == LLMErrorCode.RATE_LIMITED

    def test_unauthorized(self, llm: OpenAICompatibleLLM, mock_response: MagicMock) -> None:
        """401 → PROVIDER_ERROR。"""
        mock_response.status_code = 401
        mock_response.json.return_value = {"error": "unauthorized"}

        result = llm._handle_error_response(mock_response, 500, "test")

        assert result.error_code == LLMErrorCode.PROVIDER_ERROR
        assert "认证失败" in result.error_detail

    def test_server_error(self, llm: OpenAICompatibleLLM, mock_response: MagicMock) -> None:
        """502 → PROVIDER_ERROR。"""
        mock_response.status_code = 502
        mock_response.json.return_value = {"error": "bad gateway"}

        result = llm._handle_error_response(mock_response, 500, "test")

        assert result.error_code == LLMErrorCode.PROVIDER_ERROR
        assert "服务端错误" in result.error_detail


# ========================================================================
# 调用历史
# ========================================================================


class TestCallHistory:
    """调用历史记录测试。"""

    def test_empty_initially(self, llm: OpenAICompatibleLLM) -> None:
        """初始时历史为空。"""
        assert len(llm.get_call_history()) == 0

    def test_records_after_success_response(self, llm: OpenAICompatibleLLM) -> None:
        """成功响应后记录到历史。"""
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"title": "x", "score": 1}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

        llm._handle_success_response(mock_resp, _TestOutput, 100, "test")
        assert len(llm.get_call_history()) == 1


# ========================================================================
# load_llm_client 工厂
# ========================================================================


class TestLoadLlmClient:
    """load_llm_client 便捷工厂测试。"""

    def test_creates_client_with_local_env(self) -> None:
        """local 环境正常创建 OpenAICompatibleLLM。"""
        client = load_llm_client(
            Settings(
                app_env="local",
                llm_provider="openai_compatible",
                llm_api_base="https://test.example.com",
                llm_api_key="sk-test",
            )
        )
        assert isinstance(client, OpenAICompatibleLLM)

    def test_raises_for_fake_provider(self) -> None:
        """fake provider 抛出 ValueError。"""
        with pytest.raises(ValueError, match="fake"):
            load_llm_client(
                Settings(app_env="local", llm_provider="fake")
            )

    def test_raises_for_test_env(self) -> None:
        """test 环境抛出 ValueError。"""
        with pytest.raises(ValueError):
            load_llm_client(
                Settings(app_env="test")
            )
