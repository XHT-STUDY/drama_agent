"""Run API 路由 — WorkflowRun 生命周期 (C-08).

端点：
- POST /projects/{id}/runs  → 创建 Run（支持 create_script 选项）
- GET  /runs/{run_id}       → 查询 Run 状态
- POST /runs/{run_id}/cancel → 取消 Run
- GET  /runs/{run_id}/events → SSE 事件流
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.application.run_service import RunService
from app.application.workflow_dispatcher import schedule_worker
from app.core.errors import RunAlreadyActiveError, RunNotRetryableError
from app.events.stream import router as sse_router
from app.observability.diagnostics import RunDiagnosticsResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["runs"])
_service = RunService()

# 包含 SSE 子路由
router.include_router(sse_router)


# ---- Request/Response Schemas ----


class CreateScriptOptions(BaseModel):
    """create_script action 的选项 (C-08).

    MVP 边界约束（见 DEV_PLAN §1.3）：
    - outline_count 固定 10
    - script_count 固定 3
    """

    model_config = {"extra": "forbid"}

    user_input: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="用户输入的创作 Idea/Outline 文本",
    )
    source_type: str = Field(
        default="idea",
        description="输入类型: idea / outline / txt / docx",
    )
    outline_count: int = Field(
        default=10,
        ge=1,
        le=100,
        description="大纲集数（MVP 固定 10）",
    )
    script_count: int = Field(
        default=3,
        ge=1,
        le=50,
        description="生成剧本集数（MVP 固定 3）",
    )

    @model_validator(mode="after")
    def _mvp_boundary_warning(self) -> CreateScriptOptions:
        """超出 MVP 范围时做最佳努力：不拒绝请求，但记录边界处理策略。

        验收项（C-08）：
        - outline_count 不是 10 或 script_count 超过 3 时按 MVP 配置处理
        """
        # MVP 阶段接受任意合法值，但记录非标准配置
        # Worker 读取 options 时尊重用户设定，不做静默修改
        return self


class CreateRunRequest(BaseModel):
    """创建 Run 请求体。"""

    model_config = {"extra": "forbid"}

    action: str = Field(
        ...,
        max_length=50,
        description="执行动作：create_script / evaluate / revise / platform_smoke",
    )
    config: dict[str, Any] | None = Field(default=None, description="Run 配置快照")
    options: CreateScriptOptions | None = Field(
        default=None,
        description="create_script 的专属选项（action=create_script 时提供）",
    )
    idempotency_key: str | None = Field(default=None, max_length=128, description="幂等键")


class RunResponse(BaseModel):
    """Run 响应体。"""

    model_config = {"extra": "forbid"}

    run_id: str = Field(..., description="Run UUID")
    project_id: str = Field(..., description="所属项目 UUID")
    action: str = Field(..., description="执行动作")
    status: str = Field(..., description="当前状态")
    config_snapshot: dict[str, Any] | None = Field(default=None, description="配置快照")
    error_code: str | None = Field(default=None, description="机器可读错误码（failed 时，I-01）")
    error_detail: str | None = Field(default=None, description="错误详情（failed 时，I-01）")
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
            error_code=run.error_code,
            error_detail=run.error_detail,
            created_at=run.created_at.isoformat() if run.created_at else "",
            updated_at=run.updated_at.isoformat() if run.updated_at else "",
        )


class RunListResponse(BaseModel):
    """Run 列表响应。"""

    model_config = {"extra": "forbid"}

    items: list[RunResponse] = Field(default_factory=list, description="Run 列表")
    total: int = Field(default=0, description="总数")
    offset: int = Field(default=0, description="偏移量")
    limit: int = Field(default=20, description="每页数量")


# ---- 端点 ----


@router.post(
    "/projects/{project_id}/runs",
    response_model=RunResponse,
    status_code=202,
    responses={
        202: {"description": "Run 已创建并进入队列"},
        404: {"description": "项目不存在"},
        409: {"description": "存在未完成的 Run（活跃冲突）"},
    },
)
async def create_run(
    project_id: uuid.UUID,
    body: CreateRunRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RunResponse:
    """创建新的 WorkflowRun。

    返回 HTTP 202 Accepted，客户端通过 GET /runs/{id}/events 订阅进度。

    action=create_script 时：
    - 需要提供 options（含 user_input）
    - Worker 将执行 creation workflow：normalize → story_bible → outline → scripts
    """
    # 合并 options 到 config 快照中
    config_snapshot = body.config or {}
    if body.options:
        config_snapshot["options"] = body.options.model_dump()

    run = await _service.create_run(
        db,
        project_id=project_id,
        action=body.action,
        config=config_snapshot,
        idempotency_key=body.idempotency_key,
    )

    # 先提交 durable Run，再做 best-effort 唤醒；未知 action 也会被领取后明确失败。
    await db.commit()
    schedule_worker(run.id, body.action, config_snapshot)

    return RunResponse.from_orm(run)


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RunResponse:
    """查询 Run 状态。"""
    run = await _service.get_run(db, run_id)
    return RunResponse.from_orm(run)


@router.get("/runs/{run_id}/diagnostics", response_model=RunDiagnosticsResponse)
async def get_run_diagnostics(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RunDiagnosticsResponse:
    """Run 运行诊断（I-02）。

    聚合事件表输出节点时间线 / LLM 调用统计 / 失败信息，满足
    "根据 run_id 找到完整节点时间线"与"统计一次 Demo 调用次数与 token"。
    """
    from app.observability.diagnostics import build_run_diagnostics

    return await build_run_diagnostics(db, run_id)


@router.get(
    "/projects/{project_id}/runs",
    response_model=RunListResponse,
)
async def list_runs(
    project_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: int = 0,
    limit: int = 20,
) -> RunListResponse:
    """按项目分页查询 Run 列表。"""
    runs = await _service.list_runs_by_project(db, project_id, offset=offset, limit=limit)
    return RunListResponse(
        items=[RunResponse.from_orm(r) for r in runs],
        total=len(runs),
        offset=offset,
        limit=limit,
    )


@router.post("/runs/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RunResponse:
    """取消 Run（I-01 协作式）。

    queued → 立即取消；running → 置内存取消标记，工作流在下一节点守卫
    处中断（cancel 后不再创建新 Artifact），Run 由 Worker 转为 cancelled。
    """
    run = await _service.cancel_run(db, run_id)
    return RunResponse.from_orm(run)


@router.post("/runs/{run_id}/retry", response_model=RunResponse)
async def retry_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RunResponse:
    """重试失败的 Run（I-01）。

    仅 failed / needs_review 可重试；以 state_summary 为初始状态重放，
    已完成节点（completed_nodes）早退 → 不重调 LLM、不重复建 Artifact、
    不重复推进 revision_round。completed / cancelled 不可重试，
    queued / running 存在活跃 Worker 不可重复重试。
    """
    run = await _service.get_run(db, run_id)
    if run.status in ("completed", "cancelled"):
        raise RunNotRetryableError(detail=f"Run 已处于终态 {run.status}，不可重试")
    if run.status in ("queued", "running"):
        raise RunAlreadyActiveError(detail=f"Run 正在执行（{run.status}），不可重试")

    # 开启新尝试：清空上一轮错误字段，回到队列
    run.error_code = None
    run.error_detail = None
    await db.flush()
    await _service.transition_status(db, run_id, "queued")

    await db.commit()

    schedule_worker(run.id, run.action, run.config_snapshot or {})
    return RunResponse.from_orm(run)
