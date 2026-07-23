"""ConversationService / MessageService — 会话与消息用例编排。

负责会话和消息的业务校验、跨项目保护和事务边界。
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.db.models.project import Project
from app.db.repositories.base import BaseRepository
from app.domain.conversation import (
    ConversationCreate,
    ConversationListResponse,
    ConversationResponse,
    MessageCreate,
    MessageListResponse,
    MessageResponse,
)

# ---- 转换函数 ----


def _conv_to_response(c: Conversation) -> ConversationResponse:
    """将 ORM 会话模型转换为 API 响应。"""
    return ConversationResponse(
        id=c.id,
        project_id=c.project_id,
        title=c.title,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _msg_to_response(m: Message) -> MessageResponse:
    """将 ORM 消息模型转换为 API 响应。"""
    return MessageResponse(
        id=m.id,
        conversation_id=m.conversation_id,
        role=m.role,
        content=m.content,
        sequence=m.sequence,
        created_at=m.created_at,
    )


# ---- ConversationService ----


class ConversationService:
    """会话应用服务。

    提供会话的创建和按项目列表查询。
    """

    async def create(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        data: ConversationCreate,
    ) -> ConversationResponse:
        """在指定项目下创建会话。

        先校验项目存在且未被软删除，
        然后创建会话并返回。
        """
        # 校验项目存在
        project_repo = BaseRepository(db, Project)
        project = await project_repo.get(project_id)
        if project is None or project.deleted_at is not None:
            raise NotFoundError(detail=f"项目不存在: {project_id}", code="PROJECT_NOT_FOUND")

        conversation = Conversation(
            project_id=project_id,
            title=data.title,
        )
        repo = BaseRepository(db, Conversation)
        saved = await repo.add(conversation)
        return _conv_to_response(saved)

    async def list_by_project(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> ConversationListResponse:
        """按项目分页查询会话列表（不含已软删除）。"""
        # 先校验项目存在
        project_repo = BaseRepository(db, Project)
        project = await project_repo.get(project_id)
        if project is None or project.deleted_at is not None:
            raise NotFoundError(detail=f"项目不存在: {project_id}", code="PROJECT_NOT_FOUND")

        repo = BaseRepository(db, Conversation)
        items = await repo.list(project_id=project_id, offset=offset, limit=limit)
        # 过滤软删除
        active = [c for c in items if c.deleted_at is None]
        return ConversationListResponse(
            items=[_conv_to_response(c) for c in active],
            total=len(active),
            offset=offset,
            limit=limit,
        )


# ---- MessageService ----


class MessageService:
    """消息应用服务。

    提供消息的追加和按会话分页查询。
    追加消息时自动分配递增 sequence，并校验跨项目写入保护。
    """

    async def append(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        data: MessageCreate,
    ) -> MessageResponse:
        """向会话追加一条消息。

        1. 校验会话存在
        2. 自动计算下一个 sequence（当前最大值 + 1）
        3. 创建并返回消息

        跨项目保护由数据库外键约束自然保证
        （conversation → project 是 FK，消息无法跨项目关联）。
        """
        conv_repo = BaseRepository(db, Conversation)
        conversation = await conv_repo.get(conversation_id)
        if conversation is None or conversation.deleted_at is not None:
            raise NotFoundError(
                detail=f"会话不存在: {conversation_id}",
                code="CONVERSATION_NOT_FOUND",
            )

        # 自动分配 sequence：查询当前会话最大 sequence + 1
        max_seq_result = await db.execute(
            select(func.coalesce(func.max(Message.sequence), 0)).where(
                Message.conversation_id == conversation_id
            )
        )
        next_sequence: int = max_seq_result.scalar_one() + 1

        message = Message(
            conversation_id=conversation_id,
            role=data.role,
            content=data.content,
            sequence=next_sequence,
        )
        msg_repo = BaseRepository(db, Message)
        saved = await msg_repo.add(message)
        return _msg_to_response(saved)

    async def list_by_conversation(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> MessageListResponse:
        """按会话分页查询消息列表（按 created_at + id 稳定排序）。"""
        conv_repo = BaseRepository(db, Conversation)
        conversation = await conv_repo.get(conversation_id)
        if conversation is None or conversation.deleted_at is not None:
            raise NotFoundError(
                detail=f"会话不存在: {conversation_id}",
                code="CONVERSATION_NOT_FOUND",
            )

        # 按创建时间 + ID 排序（稳定排序 — DEV_PLAN §B-03 验收要求）
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(stmt)
        items = list(result.scalars().all())

        # 总数
        count_stmt = (
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )
        count_result = await db.execute(count_stmt)
        total: int = count_result.scalar_one()

        return MessageListResponse(
            items=[_msg_to_response(m) for m in items],
            total=total,
            offset=offset,
            limit=limit,
        )
