"""Run API 路由 — WorkflowRun 生命周期。

端点：
- POST /projects/{id}/runs  → 创建 Run
- GET  /runs/{run_id}       → 查询 Run 状态
- POST /runs/{run_id}/cancel → 取消 Run
- GET  /runs/{run_id}/events → SSE 事件流
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.application.run_service import RunService
from app.events.stream import router as sse_router

router = APIRouter(tags=["runs"])
_service = RunService()

# 包含 SSE 子路由
router.include_router(sse_router)


# ---- Request/Response Schemas ----


class CreateRunRequest(BaseModel):
    """创建 Run 请求体。"""

    model_config = {"extra": "forbid"}

    action: str = Field(..., max_length=50, description="执行动作：create_script / evaluate / revise")
    config: dict[str, Any] | None = Field(default=None, description="Run 配置")
    idempotency_key: str | None = Field(default=None, max_length=128, description="幂等键")


class RunResponse(BaseModel):
    """Run 响应体。"""

    model_config = {"extra": "forbid"}

    run_id: str = Field(..., description="Run UUID")
    project_id: str = Field(..., description="所属项目 UUID")
    action: str = Field(..., description="执行动作")
    status: str = Field(..., description="当前状态")
    config_snapshot: dict[str, Any] | None = Field(default=None, description="配置快照")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")

    @classmethod
    def from_orm(cls, run: Any) -> RunResponse:
        return cls(
            run_id=str(run.id),
            project_id=str(run.project_id),
            action=run.action,
            status=run.status,
            config_snapshot=run.config_snapshot,
            created_at=run.created_at.isoformat() if run.created_at else "",
            updated_at=run.updated_at.isoformat() if run.updated_at else "",
        )


# ---- 端点 ----


@router.post("/projects/{project_id}/runs", response_model=RunResponse, status_code=202)
async def create_run(
    project_id: uuid.UUID,
    body: CreateRunRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RunResponse:
    """创建新的 WorkflowRun。

    返回 HTTP 202 Accepted，客户端通过 GET /runs/{id}/events 订阅进度。
    """
    run = await _service.create_run(
        db,
        project_id=project_id,
        action=body.action,
        config=body.config,
        idempotency_key=body.idempotency_key,
    )
    return RunResponse.from_orm(run)


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RunResponse:
    """查询 Run 状态。"""
    run = await _service.get_run(db, run_id)
    return RunResponse.from_orm(run)


@router.post("/runs/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RunResponse:
    """取消 Run（仅 queued 状态可取消）。"""
    run = await _service.cancel_run(db, run_id)
    return RunResponse.from_orm(run)
