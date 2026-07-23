"""Project API Schema — 项目请求/响应模型。

API 层使用独立的 Pydantic 模型，不直接暴露 ORM 对象。
所有模型设置 extra="forbid"，遵循 DEV_PLAN §5.1 规范。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    """创建项目请求体。"""

    model_config = {"extra": "forbid"}

    title: str = Field(
        default="",
        max_length=200,
        description="项目标题",
    )
    target_episode_count: int = Field(
        default=10,
        ge=1,
        le=100,
        description="目标总集数（MVP 默认 10）",
    )


class ProjectUpdate(BaseModel):
    """更新项目请求体——所有字段可选，只更新传入的字段。"""

    model_config = {"extra": "forbid"}

    title: str | None = Field(
        default=None,
        max_length=200,
        description="项目标题",
    )
    target_episode_count: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="目标总集数",
    )
    status: str | None = Field(
        default=None,
        max_length=20,
        description="项目状态",
    )


class ProjectResponse(BaseModel):
    """项目响应体（不含 ORM 内部字段如 deleted_at）。"""

    model_config = {"extra": "forbid"}

    id: uuid.UUID = Field(..., description="项目 UUID")
    title: str = Field(..., description="项目标题")
    status: str = Field(..., description="项目状态")
    target_episode_count: int = Field(..., description="目标总集数")
    current_episode_count: int = Field(..., description="当前已完成集数")
    created_at: datetime = Field(..., description="创建时间（UTC）")
    updated_at: datetime = Field(..., description="最后更新时间（UTC）")


class ProjectListResponse(BaseModel):
    """项目分页列表响应。"""

    model_config = {"extra": "forbid"}

    items: list[ProjectResponse] = Field(default_factory=list, description="项目列表")
    total: int = Field(..., description="符合条件的总数")
    offset: int = Field(..., description="当前偏移量")
    limit: int = Field(..., description="每页条数")
