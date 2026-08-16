"""向量化（Embedder）模块（D-03）。

镜像 LLM 层模式（app/llm/）：
- Embedder(ABC)：协议抽象；
- OpenAICompatibleEmbedder：真实实现，HTTP 调 OpenAI 兼容 /embeddings 端点，
  支持批处理 / 重试 / 同文本缓存，零新增依赖（复用 httpx）；
- FakeEmbedder：确定性伪向量（同文本 hash 映射同一归一化向量），测试专用，零网络；
- load_embedder(settings)：工厂，test 环境强制返回 FakeEmbedder。

设计要点：
- 维度一致性校验在写入前失败（pgvector 模型固定 Vector(1536)，维度不符会在 insert 时报错，
  提前校验给出明确错误）；
- FakeEmbedder 向量归一化到单位长度，保证 cosine 相似度（pgvector <=>）排名有意义。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
from abc import ABC, abstractmethod

import httpx

from app.core.config import Settings
from app.rag.models import EmbeddingResult

logger = logging.getLogger(__name__)

# pgvector 模型固定维度（0001 迁移 Vector(1536)）
DB_EMBEDDING_DIMENSION = 1536
# 单次请求最大文本批大小
DEFAULT_BATCH_SIZE = 32


class EmbeddingError(Exception):
    """向量化调用失败（网络 / 响应解析 / 维度不符等）。"""


class EmbeddingDimensionError(ValueError):
    """向量维度与预期不一致（写入前失败）。"""


def resolve_embedding_dimension(settings: Settings) -> int:
    """解析期望向量维度：配置 >0 时用配置，否则回退 DB 固定维度 1536。"""
    if settings.embedding_dimension and settings.embedding_dimension > 0:
        return settings.embedding_dimension
    return DB_EMBEDDING_DIMENSION


def validate_embedding_dimension(actual: int, expected: int) -> None:
    """维度一致性校验：不一致立即失败，避免插入 pgvector 时报晦涩错误。"""
    if actual != expected:
        raise EmbeddingDimensionError(
            f"向量维度不一致: 实际 {actual}，期望 {expected}"
        )


class Embedder(ABC):
    """向量化协议：把一批文本编码为向量。"""

    # 期望向量维度（pgvector 模型固定维度或配置值）
    dimension: int

    @abstractmethod
    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """把一批文本编码为向量（顺序与输入一致）。"""

    async def embed_one(self, text: str) -> list[float]:
        """编码单条文本。"""
        result = await self.embed([text])
        if not result.vectors:
            raise EmbeddingError("未返回任何向量")
        return result.vectors[0]

    @abstractmethod
    async def close(self) -> None:
        """释放资源。"""


class FakeEmbedder(Embedder):
    """确定性伪向量：同文本 hash → 同一归一化向量，零网络。"""

    def __init__(self, dimension: int = DB_EMBEDDING_DIMENSION) -> None:
        """初始化 FakeEmbedder。

        Args:
            dimension: 向量维度，默认与 pgvector 模型一致（1536）。
        """
        self.dimension = dimension
        self._cache: dict[str, list[float]] = {}
        self._call_count = 0
        self._cached_count = 0

    @property
    def call_count(self) -> int:
        """embed() 调用次数。"""
        return self._call_count

    async def close(self) -> None:
        """FakeEmbedder 无外部资源，无需释放。"""

    @property
    def cached_count(self) -> int:
        """累计缓存命中次数。"""
        return self._cached_count

    def _vector_for(self, text: str) -> list[float]:
        """按文本 hash 生成确定性归一化向量（命中缓存则复用）。"""
        cached = self._cache.get(text)
        if cached is not None:
            self._cached_count += 1
            return cached
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)
        rng = random.Random(seed)
        vec = [rng.uniform(-1.0, 1.0) for _ in range(self.dimension)]
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        normalized = [v / norm for v in vec]
        self._cache[text] = normalized
        return normalized

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """返回确定性伪向量（纯本地计算，不访问网络）。"""
        start = time.monotonic()
        self._call_count += 1
        before = self._cached_count
        vectors = [self._vector_for(t) for t in texts]
        return EmbeddingResult(
            vectors=vectors,
            model="fake-embedding",
            dimension=self.dimension,
            duration_ms=int((time.monotonic() - start) * 1000),
            calls=1,
            cached_count=self._cached_count - before,
        )


class OpenAICompatibleEmbedder(Embedder):
    """OpenAI 兼容 /embeddings 端点客户端（镜像 OpenAICompatibleLLM 的 HTTP 方式）。

    复用 LLM 的 base_url / api_key / 超时 / 重试配置：
    - 批处理：超过 batch_size 自动分批调用；
    - 重试：连接失败 / 超时 / 非 200 重试（llm_max_retries 次）；
    - 缓存：同文本 hash 复用向量，避免重复计费。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        dimension: int = DB_EMBEDDING_DIMENSION,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        """初始化 OpenAI 兼容 Embedder。

        Args:
            settings: DramaAgent Settings 实例（复用 LLM 网络配置）。
            dimension: 期望向量维度（默认与 pgvector 模型一致）。
            batch_size: 单次请求最大文本数。
        """
        self.settings = settings
        self.dimension = dimension
        self.batch_size = batch_size
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, list[float]] = {}
        self._request_count = 0
        self._cached_count = 0

    @property
    def call_count(self) -> int:
        """累计实际 HTTP 请求次数（含重试内失败重发前的最终成功请求）。"""
        return self._request_count

    @property
    def cached_count(self) -> int:
        """累计缓存命中次数。"""
        return self._cached_count

    @property
    def client(self) -> httpx.AsyncClient:
        """惰性创建 httpx 客户端（复用 LLM base_url / api_key）。"""
        if self._client is None:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self.settings.llm_api_key:
                headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
            timeout = httpx.Timeout(
                connect=30.0,
                read=max(60.0, self.settings.llm_timeout_seconds),
                write=30.0,
                pool=10.0,
            )
            self._client = httpx.AsyncClient(
                base_url=self.settings.llm_api_base,
                headers=headers,
                timeout=timeout,
            )
        return self._client

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """把一批文本编码为向量（顺序与输入一致，命中缓存不重复请求）。"""
        start = time.monotonic()

        vectors: list[list[float] | None] = [None] * len(texts)
        to_embed: list[tuple[int, str]] = []
        for i, text in enumerate(texts):
            cached = self._cache.get(text)
            if cached is not None:
                vectors[i] = cached
            else:
                to_embed.append((i, text))
        cached_hits = len(texts) - len(to_embed)
        self._cached_count += cached_hits

        requests_made = 0
        for offset in range(0, len(to_embed), self.batch_size):
            batch = to_embed[offset : offset + self.batch_size]
            response = await self._request([text for _, text in batch])
            requests_made += 1
            batch_vectors = self._handle_success_response(response, len(batch))
            for (index, text), vec in zip(batch, batch_vectors, strict=True):
                validate_embedding_dimension(len(vec), self.dimension)
                self._cache[text] = vec
                vectors[index] = vec
        self._request_count += requests_made

        if any(v is None for v in vectors):
            raise EmbeddingError("部分文本未获得向量")
        typed = [v for v in vectors if v is not None]

        return EmbeddingResult(
            vectors=typed,
            model=self.settings.embedding_model or "text-embedding-3-small",
            dimension=self.dimension,
            duration_ms=int((time.monotonic() - start) * 1000),
            calls=requests_made,
            cached_count=cached_hits,
        )

    async def _request(self, texts: list[str]) -> httpx.Response:
        """带重试的 /embeddings 请求。失败重试后仍失败则抛 EmbeddingError。"""
        model = self.settings.embedding_model or "text-embedding-3-small"
        payload = {"model": model, "input": texts}
        attempts = 1 + max(0, self.settings.llm_max_retries)
        last_error: str = "未知错误"
        for attempt in range(attempts):
            try:
                response = await self.client.post("/embeddings", json=payload)
                if response.status_code == 200:
                    return response
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                logger.error("Embedding API 错误: %s", last_error)
            except httpx.TimeoutException as e:
                last_error = f"请求超时: {e}"
            except httpx.ConnectError as e:
                last_error = f"连接失败: {e}"
            if attempt < attempts - 1:
                await asyncio.sleep(0.3 * (attempt + 1))
        raise EmbeddingError(f"Embedding 请求失败（已重试 {attempts - 1} 次）: {last_error}")

    def _handle_success_response(
        self, response: httpx.Response, expected: int
    ) -> list[list[float]]:
        """解析成功响应：按 index 排序返回向量列表。"""
        data = response.json()
        items = data.get("data", [])
        items_sorted = sorted(items, key=lambda d: d.get("index", 0))
        vectors = [d.get("embedding") for d in items_sorted]
        if len(vectors) != expected:
            raise EmbeddingError(
                f"返回向量数 {len(vectors)} 与请求数 {expected} 不一致"
            )
        return vectors

    async def close(self) -> None:
        """关闭 httpx 客户端。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def load_embedder(settings: Settings | None = None) -> Embedder:
    """便捷工厂：从 Settings 创建 Embedder。

    - test 环境或 embedding_provider=fake → FakeEmbedder（零网络，测试/CI 默认）；
    - 否则 → OpenAICompatibleEmbedder。
    """
    if settings is None:
        settings = Settings()
        settings.apply_env_overrides()
    dimension = resolve_embedding_dimension(settings)
    if settings.app_env == "test" or settings.embedding_provider == "fake":
        return FakeEmbedder(dimension=dimension)
    return OpenAICompatibleEmbedder(settings, dimension=dimension)
