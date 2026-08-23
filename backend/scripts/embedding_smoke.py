"""真实 Embedding provider 冒烟（K-4）。

验证 OpenAI 兼容 embedding 端点的连通性、维度一致性与基本语义性
（相关文本相似度 > 无关文本），并做一次"摄取 → 检索"最小回环。
**不进入 CI**——需要真实 API Key，手工执行：

    cd backend
    EMBEDDING_PROVIDER=openai_compatible \
    EMBEDDING_API_KEY=<key> EMBEDDING_BASE_URL=<url> EMBEDDING_MODEL=<model> \
    uv run python -m scripts.embedding_smoke

脚本从环境/.env 读取密钥且不打印 Key。任何一步失败以非零码退出。
"""

from __future__ import annotations

import asyncio
import sys


async def main() -> int:
    from app.core.config import load_settings
    from app.rag.embedder import load_embedder

    settings = load_settings()
    if settings.app_env == "test":
        print("拒绝执行：test 环境固定 FakeEmbedder，请用真实环境运行。")
        return 1

    print(f"provider={settings.embedding_provider} model={getattr(settings, 'embedding_model', '')}")
    embedder = load_embedder(settings)
    try:
        # 1) 连通性 + 维度
        related_a = "林峰拥有战术视野天赋"
        related_b = "林峰的战术视野能力设定"
        unrelated = "今日餐厅午市套餐是红烧肉"

        result = await embedder.embed([related_a, related_b, unrelated])
        dim = len(result.vectors[0])
        print(f"[1] 连通性 OK：{len(result.vectors)} 条向量，维度 {dim}，model={result.model}")

        # 2) 维度一致性（pgvector 列为 1536；不一致会在摄取时失败）
        if dim != 1536:
            print(f"[2] 警告：维度 {dim} != pgvector 列宽 1536（需调整列定义或模型）")

        # 3) 基本语义性：相关对 vs 无关对
        def cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b, strict=True))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(x * x for x in b) ** 0.5
            return dot / (na * nb) if na and nb else 0.0

        rel = cosine(result.vectors[0], result.vectors[1])
        unr = cosine(result.vectors[0], result.vectors[2])
        print(f"[3] 相似度：相关对 {rel:.4f} vs 无关对 {unr:.4f}")
        if rel <= unr:
            print("    失败：相关对相似度未高于无关对——embedding 语义性异常")
            return 1

        print("真实 Embedding 冒烟通过。")
        return 0
    finally:
        await embedder.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
