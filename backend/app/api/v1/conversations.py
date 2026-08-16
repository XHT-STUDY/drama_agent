"""Conversation / Message API 路由。

端点：
- POST   /projects/{project_id}/conversations           创建会话
- GET    /projects/{project_id}/conversations           按项目分页查询会话
- POST   /conversations/{conversation_id}/messages      追加消息
- GET    /conversations/{conversation_id}/messages      分页查询消息
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.application.conversation_service import ConversationService, MessageService
from app.domain.conversation import (
    ConversationCreate,
    ConversationListResponse,
    ConversationResponse,
    MessageCreate,
    MessageListResponse,
    MessageResponse,
)

router = APIRouter(tags=["conversations"])
_conv_service = ConversationService()


# ---- G-01 记忆挂载（惰性构建，避免 import 期副作用） ----


def _build_msg_service() -> MessageService:
    """构建带记忆挂载的 MessageService（生产路径）。

    惰性构建：首次追加消息时才创建短期记忆存储与会话摘要管理器。
    设计要点（G-01 验收）：
    - Redis 不可用时短期记忆自动降级（回退 DB 恢复，消息不丢失）；
    - 摘要生成失败只 log，绝不阻断消息保存。
    """
    from app.agents.base import BaseAgent
    from app.application.artifact_service import ArtifactService
    from app.core.config import Settings
    from app.domain.summary import ConversationSummaryBody
    from app.llm.fake import FakeLLM
    from app.llm.openai_compatible import OpenAICompatibleLLM
    from app.memory.short_term import RedisShortTermStore
    from app.memory.summary import ConversationSummaryManager
    from app.prompts.loader import PromptLoader

    settings = Settings()
    settings.apply_env_overrides()

    store = RedisShortTermStore(
        keep_count=settings.short_term_message_count,
        ttl_seconds=settings.short_term_ttl_seconds,
    )

    if settings.app_env == "test":
        fake = FakeLLM(seed=42)
        fake.register(
            "conversation_summary",
            ConversationSummaryBody(summary="测试会话摘要", topics=["测试"]),
        )
        llm: Any = fake
    else:
        llm = OpenAICompatibleLLM(settings)

    agent = BaseAgent(name="summarizer", llm=llm)
    manager = ConversationSummaryManager(
        agent,
        PromptLoader(),
        ArtifactService(),
        threshold=settings.conversation_summary_threshold,
        window=settings.short_term_message_count,
    )
    return MessageService(short_term_store=store, summary_manager=manager)


_msg_service: MessageService | None = None


def _get_msg_service() -> MessageService:
    """惰性获取（进程内单例）带记忆挂载的 MessageService。"""
    global _msg_service
    if _msg_service is None:
        _msg_service = _build_msg_service()
    return _msg_service


# ---- Conversation 端点 ----


@router.post(
    "/projects/{project_id}/conversations",
    response_model=ConversationResponse,
    status_code=201,
)
async def create_conversation(
    project_id: uuid.UUID,
    body: ConversationCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ConversationResponse:
    """在指定项目下创建会话。"""
    return await _conv_service.create(db, project_id, body)


@router.get(
    "/projects/{project_id}/conversations",
    response_model=ConversationListResponse,
)
async def list_conversations(
    project_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ConversationListResponse:
    """按项目分页查询会话列表。"""
    return await _conv_service.list_by_project(db, project_id, offset=offset, limit=limit)


# ---- Message 端点 ----


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=201,
)
async def append_message(
    conversation_id: uuid.UUID,
    body: MessageCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """向会话追加一条消息（含短期记忆写入与会话摘要触发）。"""
    return await _get_msg_service().append(db, conversation_id, body)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=MessageListResponse,
)
async def list_messages(
    conversation_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> MessageListResponse:
    """按会话分页查询消息（按时间 + ID 稳定排序）。"""
    return await _get_msg_service().list_by_conversation(
        db, conversation_id, offset=offset, limit=limit
    )
