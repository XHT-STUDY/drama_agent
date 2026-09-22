"""Conversation / Message API 路由。

端点：
- POST   /projects/{project_id}/conversations           创建会话
- GET    /projects/{project_id}/conversations           按项目分页查询会话
- POST   /conversations/{conversation_id}/messages      追加消息
- GET    /conversations/{conversation_id}/messages      分页查询消息
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

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
from app.memory.wiring import get_message_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conversations"])
_conv_service = ConversationService()


def _get_msg_service() -> MessageService:
    """带记忆挂载的 MessageService(M-02 统一工厂,进程级单例)。

    Agent Turn 与 Action 生命周期共用 app.memory.wiring.get_message_service()
    的同一构造——任何真实消息入口都写入短期记忆并调度累计摘要。
    """
    svc = get_message_service()
    assert isinstance(svc, MessageService)
    return svc


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
    conversation = await _conv_service.create(db, project_id, body)
    logger.info("创建会话: conversation=%s project=%s", conversation.id, project_id)
    return conversation


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
    message = await _get_msg_service().append(db, conversation_id, body)
    logger.info(
        "追加消息: conversation=%s role=%s message=%s 内容=%.60s",
        conversation_id,
        body.role,
        message.id,
        body.content,
    )
    return message


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
    """按会话分页查询消息（按 sequence + ID 权威排序）。"""
    return await _get_msg_service().list_by_conversation(
        db, conversation_id, offset=offset, limit=limit
    )
