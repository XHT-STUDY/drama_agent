"""Conversation / Message API Schema — 会话与消息请求/响应模型。

API 层使用独立的 Pydantic 模型，不直接暴露 ORM 对象。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---- Conversation ----


class ConversationCreate(BaseModel):
    """创建会话请求体。"""

    model_config = {"extra": "forbid"}

    title: str = Field(
        default="",
        max_length=200,
        description="会话标题（可由首条消息自动生成）",
    )


class ConversationResponse(BaseModel):
    """会话响应体。"""

    model_config = {"extra": "forbid"}

    id: uuid.UUID = Field(..., description="会话 UUID")
    project_id: uuid.UUID = Field(..., description="所属项目 UUID")
    title: str = Field(..., description="会话标题")
    created_at: datetime = Field(..., description="创建时间（UTC）")
    updated_at: datetime = Field(..., description="最后更新时间（UTC）")


class ConversationListResponse(BaseModel):
    """会话分页列表响应。"""

    model_config = {"extra": "forbid"}

    items: list[ConversationResponse] = Field(default_factory=list, description="会话列表")
    total: int = Field(..., description="符合条件的总数")
    offset: int = Field(..., description="当前偏移量")
    limit: int = Field(..., description="每页条数")


# ---- Message ----


class MessageCreate(BaseModel):
    """追加消息请求体。"""

    model_config = {"extra": "forbid"}

    role: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="消息角色：user / assistant / system",
    )
    content: str = Field(
        ...,
        min_length=1,
        description="消息正文（Markdown）",
    )
    kind: Literal["text", "clarification", "action_plan", "action_result", "error"] = Field(
        default="text",
        description="消息类型",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="AgentTurn、Action、Run 与 Artifact 展示引用",
    )


class MessageResponse(BaseModel):
    """消息响应体。"""

    model_config = {"extra": "forbid"}

    id: uuid.UUID = Field(..., description="消息 UUID")
    conversation_id: uuid.UUID = Field(..., description="所属会话 UUID")
    role: str = Field(..., description="消息角色")
    content: str = Field(..., description="消息正文")
    kind: str = Field(..., description="消息类型")
    metadata: dict[str, Any] = Field(default_factory=dict, description="展示引用")
    sequence: int = Field(..., description="消息在会话内的序号")
    created_at: datetime = Field(..., description="创建时间（UTC）")


class MessageListResponse(BaseModel):
    """消息分页列表响应。"""

    model_config = {"extra": "forbid"}

    items: list[MessageResponse] = Field(default_factory=list, description="消息列表")
    total: int = Field(..., description="符合条件的总数")
    offset: int = Field(..., description="当前偏移量")
    limit: int = Field(..., description="每页条数")
