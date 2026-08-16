"""Memory 集成测试 fixtures (G-01)。

前置条件：Docker PostgreSQL + Redis 就绪（make up），FakeLLM 驱动。
Redis 隔离：只清理 short_term:* key，不 flushdb，避免影响 db0 上
其他测试（如 SSE pub/sub）的 Redis 状态。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
import redis.asyncio as aioredis

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.core.config import Settings
from app.domain.summary import ConversationSummaryBody
from app.llm.fake import FakeLLM
from app.prompts.loader import PromptLoader


@pytest_asyncio.fixture
async def redis_client() -> AsyncGenerator[Any, None]:
    """真实 Docker Redis 客户端（测试前后清理 short_term:* key）。"""
    settings = Settings()
    client = aioredis.from_url(settings.redis_url, decode_responses=True)

    async def _cleanup() -> None:
        keys = await client.keys("short_term:*")
        if keys:
            await client.delete(*keys)

    await _cleanup()
    yield client
    await _cleanup()
    await client.aclose()


@pytest_asyncio.fixture
async def db_session(test_engine: Any) -> AsyncGenerator[Any, None]:
    """为 memory 测试提供 DB 会话（结束回滚，EventPublisher 场景不用）。"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def test_project(db_session: Any) -> uuid.UUID:
    """在测试 DB 中创建 Project。"""
    from app.db.models.project import Project

    pid = uuid.uuid4()
    db_session.add(Project(id=pid, title="G-01 记忆测试项目", status="draft"))
    await db_session.flush()
    return pid


@pytest_asyncio.fixture
async def test_conversation(db_session: Any, test_project: uuid.UUID) -> uuid.UUID:
    """在测试 DB 中创建 Conversation。"""
    from app.db.models.conversation import Conversation

    cid = uuid.uuid4()
    db_session.add(Conversation(id=cid, project_id=test_project, title="G-01 会话"))
    await db_session.flush()
    return cid


@pytest.fixture
def fake_llm() -> FakeLLM:
    """注册 conversation_summary 的 FakeLLM。"""
    llm = FakeLLM(seed=42)
    llm.register(
        "conversation_summary",
        ConversationSummaryBody(
            summary="用户与助手确认了主角设定与题材方向，期待逆袭爽点。",
            topics=["主角设定", "题材"],
        ),
    )
    return llm


@pytest.fixture
def agent(fake_llm: FakeLLM) -> BaseAgent:
    return BaseAgent(name="summarizer", llm=fake_llm)


@pytest.fixture
def prompt_loader() -> PromptLoader:
    return PromptLoader()


@pytest.fixture
def artifact_service() -> ArtifactService:
    return ArtifactService()
