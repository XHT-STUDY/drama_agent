"""D-03 Embedder 单元测试。

覆盖：
- FakeEmbedder：确定性 / 缓存 / 维度 / 归一化 / 零网络；
- 维度解析与一致性校验；
- load_embedder 工厂（test/fake → FakeEmbedder）；
- OpenAICompatibleEmbedder：响应解析 / 批处理 / 缓存 / 重试 / 维度校验（httpx MockTransport，无真实网络）。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.core.config import Settings
from app.rag.embedder import (
    DB_EMBEDDING_DIMENSION,
    EmbeddingDimensionError,
    EmbeddingError,
    FakeEmbedder,
    OpenAICompatibleEmbedder,
    load_embedder,
    resolve_embedding_dimension,
    validate_embedding_dimension,
)


class TestFakeEmbedder:
    """确定性伪向量行为。"""

    @pytest.mark.asyncio
    async def test_deterministic_same_text(self) -> None:
        """相同文本两次编码得到相同向量。"""
        emb = FakeEmbedder()
        v1 = await emb.embed_one("战神逆袭")
        v2 = await emb.embed_one("战神逆袭")
        assert v1 == v2
        assert len(v1) == DB_EMBEDDING_DIMENSION

    @pytest.mark.asyncio
    async def test_different_text_different_vector(self) -> None:
        """不同文本编码得到不同向量。"""
        emb = FakeEmbedder(dimension=64)
        assert await emb.embed_one("都市") != await emb.embed_one("战神")

    @pytest.mark.asyncio
    async def test_cache_reuses_computation(self) -> None:
        """缓存命中：重复文本不再生成新向量。"""
        emb = FakeEmbedder()
        texts = ["A", "A", "B"]
        result = await emb.embed(texts)
        assert result.cached_count == 1  # 第二个 A 命中缓存
        assert emb.call_count == 1
        assert emb.cached_count == 1

    @pytest.mark.asyncio
    async def test_vectors_normalized(self) -> None:
        """向量归一化到单位长度（保证 cosine 排名有意义）。"""
        emb = FakeEmbedder(dimension=16)
        vec = await emb.embed_one("归一化测试")
        norm = sum(v * v for v in vec) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_no_network(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FakeEmbedder 不发起任何网络请求。"""
        async def boom(*args: object, **kwargs: object) -> None:
            raise AssertionError("FakeEmbedder 不应访问网络")

        monkeypatch.setattr(httpx, "AsyncClient", boom)
        emb = FakeEmbedder()
        result = await emb.embed(["无网络", "纯本地"])
        assert len(result.vectors) == 2


class TestDimensionResolution:
    """维度解析与一致性校验。"""

    def test_resolve_uses_config_when_set(self) -> None:
        settings = Settings(app_env="test", embedding_dimension=128)
        assert resolve_embedding_dimension(settings) == 128

    def test_resolve_falls_back_to_db_dimension(self) -> None:
        settings = Settings(app_env="test", embedding_dimension=0)
        assert resolve_embedding_dimension(settings) == DB_EMBEDDING_DIMENSION

    def test_validate_matches(self) -> None:
        validate_embedding_dimension(1536, 1536)  # 不抛错

    def test_validate_mismatch_raises(self) -> None:
        with pytest.raises(EmbeddingDimensionError):
            validate_embedding_dimension(128, DB_EMBEDDING_DIMENSION)


class TestLoadEmbedder:
    """工厂分发。"""

    def test_test_env_returns_fake(self) -> None:
        emb = load_embedder(Settings(app_env="test"))
        assert isinstance(emb, FakeEmbedder)

    def test_fake_provider_returns_fake(self) -> None:
        emb = load_embedder(
            Settings(app_env="local", embedding_provider="fake")
        )
        assert isinstance(emb, FakeEmbedder)

    def test_fake_dimension_from_settings(self) -> None:
        emb = load_embedder(
            Settings(app_env="test", embedding_dimension=64)
        )
        assert emb.dimension == 64

    def test_openai_compatible_provider(self) -> None:
        emb = load_embedder(
            Settings(
                app_env="local",
                embedding_provider="openai_compatible",
                llm_api_base="http://localhost:8000",
            )
        )
        assert isinstance(emb, OpenAICompatibleEmbedder)


def _embeddings_handler(dimension: int):
    """构造返回固定维度向量的 MockTransport handler。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        inputs = body["input"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [0.1] * dimension}
                    for i in range(len(inputs))
                ],
                "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
            },
        )

    return handler


def _make_openai_embedder(
    *,
    dimension: int = 4,
    max_retries: int = 0,
    batch_size: int = 32,
) -> OpenAICompatibleEmbedder:
    settings = Settings(
        app_env="test",
        llm_api_base="http://embed.test.local",
        llm_api_key="test-key",
        embedding_model="test-embedding-model",
        embedding_dimension=dimension,
        llm_max_retries=max_retries,
    )
    return OpenAICompatibleEmbedder(settings, dimension=dimension, batch_size=batch_size)


def _client_with(handler) -> httpx.AsyncClient:
    """构造带 base_url + MockTransport 的 httpx 客户端（模拟 API 服务）。"""
    return httpx.AsyncClient(
        base_url="http://embed.test.local",
        transport=httpx.MockTransport(handler),
    )


class TestOpenAICompatibleEmbedder:
    """真实实现（HTTP mock）。"""

    @pytest.mark.asyncio
    async def test_parse_response_orders_by_index(self) -> None:
        """_handle_success_response 按 index 排序返回向量。"""
        emb = _make_openai_embedder()
        response = httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [2.0, 0.0, 0.0, 0.0]},
                    {"index": 0, "embedding": [1.0, 0.0, 0.0, 0.0]},
                ]
            },
        )
        vectors = emb._handle_success_response(response, 2)
        assert vectors[0][0] == 1.0
        assert vectors[1][0] == 2.0

    @pytest.mark.asyncio
    async def test_parse_response_wrong_count_raises(self) -> None:
        """返回向量数与请求数不一致时抛错。"""
        emb = _make_openai_embedder()
        response = httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0] * 4}]},
        )
        with pytest.raises(EmbeddingError):
            emb._handle_success_response(response, 2)

    @pytest.mark.asyncio
    async def test_embed_calls_endpoint(self) -> None:
        """embed 把全部文本发送到 /embeddings 并返回顺序一致向量。"""
        emb = _make_openai_embedder()
        emb._client = _client_with(_embeddings_handler(dimension=4))
        try:
            result = await emb.embed(["甲", "乙", "丙"])
            assert len(result.vectors) == 3
            assert result.dimension == 4
            assert result.calls == 1
        finally:
            await emb.close()

    @pytest.mark.asyncio
    async def test_cache_avoids_repeat_request(self) -> None:
        """同文本第二次调用命中缓存，不再发请求。"""
        emb = _make_openai_embedder()
        emb._client = _client_with(_embeddings_handler(dimension=4))
        try:
            first = await emb.embed(["重复文本"])
            second = await emb.embed(["重复文本"])
            assert first.cached_count == 0
            assert second.cached_count == 1
            assert emb.call_count == 1  # 仅一次真实请求
            assert first.vectors[0] == second.vectors[0]
        finally:
            await emb.close()

    @pytest.mark.asyncio
    async def test_batching(self) -> None:
        """超过 batch_size 自动分批请求。"""
        emb = _make_openai_embedder(batch_size=2)
        emb._client = _client_with(_embeddings_handler(dimension=4))
        try:
            result = await emb.embed(["1", "2", "3", "4", "5"])
            assert len(result.vectors) == 5
            assert emb.call_count == 3  # 5 个文本按 batch=2 分 3 次
        finally:
            await emb.close()

    @pytest.mark.asyncio
    async def test_dimension_mismatch_raises(self) -> None:
        """返回维度与期望不一致时在写入前失败。"""
        emb = _make_openai_embedder(dimension=4)
        emb._client = _client_with(_embeddings_handler(dimension=8))
        try:
            with pytest.raises(EmbeddingDimensionError):
                await emb.embed(["维度不符"])
        finally:
            await emb.close()

    @pytest.mark.asyncio
    async def test_retry_then_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """首次失败重试后成功。"""
        async def noop(*args: object, **kwargs: object) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", noop)
        emb = _make_openai_embedder(max_retries=1)

        calls = {"n": 0}

        async def flaky_handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(500, json={"error": "boom"})
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": i, "embedding": [0.1] * 4}
                        for i in range(len(body["input"]))
                    ]
                },
            )

        emb._client = _client_with(flaky_handler)
        try:
            result = await emb.embed(["重试成功"])
            assert result.vectors[0][0] == pytest.approx(0.1)
            assert calls["n"] == 2
        finally:
            await emb.close()

    @pytest.mark.asyncio
    async def test_persistent_failure_raises(self) -> None:
        """重试耗尽后抛 EmbeddingError。"""
        emb = _make_openai_embedder(max_retries=0)

        async def fail_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "down"})

        emb._client = _client_with(fail_handler)
        try:
            with pytest.raises(EmbeddingError):
                await emb.embed(["失败"])
        finally:
            await emb.close()
