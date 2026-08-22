"""Agent 对话 API(J-04)。

五个端点:
- POST /projects/{project_id}/agent/turns —— 三段式执行一次对话 Turn
- GET  /agent/turns/{turn_id}            —— 查询 Turn 持久化快照
- GET  /agent/actions/{action_id}        —— 查询 Action 持久化快照
- POST /agent/actions/{action_id}/confirm —— 确认计划并创建 Run
- POST /agent/actions/{action_id}/reject  —— 拒绝计划(仅 proposed)

确认接口不接受客户端回传的 Plan 内容,只使用服务端持久化计划。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_agent_command_service, get_db
from app.application.agent_command_service import AgentCommandService
from app.domain.agent_command import (
    ActiveArtifactContext,
    AgentActionResponse,
    AgentTurnResponse,
)

router = APIRouter(tags=["agent"])


# ---- 请求 / 响应模型 ----


class AgentTurnCreateRequest(BaseModel):
    """创建 Agent Turn 请求体。"""

    model_config = {"extra": "forbid"}

    conversation_id: uuid.UUID | None = Field(
        default=None, description="会话 ID;为空时自动创建会话"
    )
    content: str = Field(..., min_length=1, max_length=4000, description="用户消息内容")
    active_context: ActiveArtifactContext | None = Field(
        default=None, description="用户显式选中的页面 Artifact 上下文"
    )
    idempotency_key: str = Field(
        ..., min_length=1, max_length=128, description="客户端生成的请求幂等键"
    )


class AgentRunSnapshot(BaseModel):
    """确认响应中的 WorkflowRun 摘要。"""

    model_config = {"extra": "forbid"}

    run_id: str = Field(..., description="Run UUID")
    project_id: str = Field(..., description="所属项目 UUID")
    action: str = Field(..., description="Run 动作")
    status: str = Field(..., description="Run 状态")
    created_at: str = Field(..., description="创建时间")

    @classmethod
    def from_orm(cls, run: Any) -> AgentRunSnapshot:
        return cls(
            run_id=str(run.id),
            project_id=str(run.project_id),
            action=run.action,
            status=run.status,
            created_at=run.created_at.isoformat() if run.created_at else "",
        )


class AgentConfirmResponse(BaseModel):
    """确认 Action 的响应体。"""

    model_config = {"extra": "forbid"}

    action: AgentActionResponse
    run: AgentRunSnapshot


# ---- 端点 ----


@router.post(
    "/projects/{project_id}/agent/turns",
    response_model=AgentTurnResponse,
    responses={
        200: {"description": "Turn 已完成(clarification/answer/plan/failed)"},
        202: {"description": "Turn 正在他人有效租约下规划中"},
        404: {"description": "项目或会话不存在"},
        409: {"description": "幂等键冲突 / 活动上下文非法"},
    },
)
async def create_agent_turn(
    project_id: uuid.UUID,
    body: AgentTurnCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Annotated[AgentCommandService, Depends(get_agent_command_service)],
    response: Response,
) -> AgentTurnResponse:
    """执行一次对话 Turn。

    三段式执行:短事务 A 落 Turn 收据与 user 消息 → 事务外调用 Planner →
    短事务 B 写入 clarification/answer/plan 并终结 Turn。
    重复请求命中终态返回原响应(200),命中有效租约返回 202。
    """
    result, status_code = await service.create_turn(
        db,
        project_id=project_id,
        content=body.content,
        conversation_id=body.conversation_id,
        active_context=body.active_context,
        idempotency_key=body.idempotency_key,
    )
    response.status_code = status_code
    return result


@router.get(
    "/agent/turns/{turn_id}",
    response_model=AgentTurnResponse,
    responses={404: {"description": "Turn 不存在"}},
)
async def get_agent_turn(
    turn_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Annotated[AgentCommandService, Depends(get_agent_command_service)],
) -> AgentTurnResponse:
    """查询 Turn 的持久化快照(含关联 Action)。"""
    return await service.get_turn(db, turn_id)


@router.get(
    "/agent/actions/{action_id}",
    response_model=AgentActionResponse,
    responses={404: {"description": "Action 不存在"}},
)
async def get_agent_action(
    action_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Annotated[AgentCommandService, Depends(get_agent_command_service)],
) -> AgentActionResponse:
    """查询 Action 的持久化快照(计划、来源快照、Run 引用)。"""
    return await service.get_action(db, action_id)


@router.post(
    "/agent/actions/{action_id}/confirm",
    response_model=AgentConfirmResponse,
    status_code=202,
    responses={
        202: {"description": "已创建(或复用)Run 并进入队列"},
        400: {"description": "intent 不支持确认执行"},
        404: {"description": "Action 不存在"},
        409: {"description": "状态不可确认 / 来源已更新(stale) / 项目已有活跃 Run"},
    },
)
async def confirm_agent_action(
    action_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Annotated[AgentCommandService, Depends(get_agent_command_service)],
) -> AgentConfirmResponse:
    """确认 proposed Action:过期检测后创建或复用 WorkflowRun。

    只使用服务端持久化的 Plan;重复确认返回原 Run;
    来源 Artifact 已更新时 Action→stale 并返回 409 ACTION_STALE。
    """
    action, run = await service.confirm_action(db, action_id)
    return AgentConfirmResponse(
        action=action,
        run=AgentRunSnapshot.from_orm(run),
    )


@router.post(
    "/agent/actions/{action_id}/reject",
    response_model=AgentActionResponse,
    responses={
        200: {"description": "Action 已被拒绝"},
        404: {"description": "Action 不存在"},
        409: {"description": "仅允许 proposed → rejected"},
    },
)
async def reject_agent_action(
    action_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Annotated[AgentCommandService, Depends(get_agent_command_service)],
) -> AgentActionResponse:
    """拒绝 proposed Action(仅 proposed → rejected)。"""
    return await service.reject_action(db, action_id)
