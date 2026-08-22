"""RunService — WorkflowRun 状态机与生命周期管理。

负责：
- Run 创建（含 Idempotency-Key 去重）
- 状态机转换（queued→running→completed/failed/cancelled）
- 事件发布
- cancel/retry
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, IdempotencyKeyReusedError, NotFoundError, RunAlreadyActiveError
from app.db.models.project import Project
from app.db.models.workflow_run import WorkflowRun
from app.events.publisher import EventPublisher
from app.observability.metrics import workflow_runs_total
from app.workflows.checkpoint import request_cancel

# 合法状态转换（I-01：running → cancelled 允许协作式取消）
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancelled"},
    "running": {"completed", "failed", "needs_review", "cancelled"},
    "completed": set(),
    "failed": {"queued"},  # retry
    "cancelled": set(),
    "needs_review": {"queued"},  # 人工审查后可重试
}


class RunService:
    """WorkflowRun 应用服务。"""

    def __init__(self) -> None:
        self._publisher = EventPublisher()

    @staticmethod
    def request_hash(action: str, config: dict[str, Any] | None) -> str:
        """生成稳定的请求指纹，键顺序不影响结果。"""
        payload = {"action": action, "config": config or {}}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # ---- 创建 ----

    async def create_run(
        self,
        db: AsyncSession,
        *,
        project_id: uuid.UUID,
        action: str,
        config: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> WorkflowRun:
        """创建新的 WorkflowRun。

        1. 幂等检查：相同 idempotency_key 返回已有 run
        2. 校验 project 存在
        3. INSERT workflow_run (status=queued)
        4. 发布 run.created 事件

        Raises:
            NotFoundError: 项目不存在
        """
        request_hash = self.request_hash(action, config)

        if idempotency_key:
            result = await db.execute(
                select(WorkflowRun).where(
                    WorkflowRun.project_id == project_id,
                    WorkflowRun.action == action,
                    WorkflowRun.idempotency_key == idempotency_key,
                )
            )
            existing = result.scalar_one_or_none()
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise IdempotencyKeyReusedError(detail="幂等键已用于不同请求")
                return existing

        # 校验项目存在
        proj_result = await db.execute(select(Project).where(Project.id == project_id))
        project = proj_result.scalar_one_or_none()
        if project is None or project.deleted_at is not None:
            raise NotFoundError(detail=f"项目不存在: {project_id}", code="PROJECT_NOT_FOUND")

        active_result = await db.execute(
            select(WorkflowRun).where(
                WorkflowRun.project_id == project_id,
                WorkflowRun.status.in_(("queued", "running")),
            )
        )
        active = active_result.scalar_one_or_none()
        if active is not None:
            raise RunAlreadyActiveError(detail=f"项目已有活跃 Run: {active.id}")

        # 创建 Run
        run = WorkflowRun(
            project_id=project_id,
            action=action,
            status="queued",
            config_snapshot=config,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        try:
            async with db.begin_nested():
                db.add(run)
                await db.flush()
        except IntegrityError:
            if idempotency_key is not None:
                result = await db.execute(
                    select(WorkflowRun).where(
                        WorkflowRun.project_id == project_id,
                        WorkflowRun.action == action,
                        WorkflowRun.idempotency_key == idempotency_key,
                    )
                )
                existing = result.scalar_one_or_none()
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise IdempotencyKeyReusedError(detail="幂等键已用于不同请求") from None
                    return existing
            raise RunAlreadyActiveError(detail="项目已有活跃 Run") from None

        # I-02：Run 创建计数（status=queued）
        workflow_runs_total.inc(action=action, status="queued")

        # 发布 run.created 事件
        await self._publisher.publish(
            db,
            run_id=run.id,
            event_type="run.created",
            payload={"action": action, "project_id": str(project_id)},
        )

        return run

    # ---- 状态机 ----

    async def transition_status(
        self,
        db: AsyncSession,
        run_id: uuid.UUID,
        new_status: str,
        *,
        lease_owner: str | None = None,
    ) -> WorkflowRun:
        """执行状态转换。

        校验合法性；如转换到终态则发布对应事件。
        """
        stmt = select(WorkflowRun).where(WorkflowRun.id == run_id)
        if lease_owner is not None:
            stmt = stmt.where(WorkflowRun.lease_owner == lease_owner)
        result = await db.execute(stmt)
        run = result.scalar_one_or_none()
        if run is None:
            if lease_owner is not None:
                raise AppError(
                    detail="Workflow 租约已丢失，拒绝旧 Worker 写入终态",
                    status_code=409,
                    code="WORKFLOW_LEASE_LOST",
                )
            raise NotFoundError(detail=f"Run 不存在: {run_id}", code="RUN_NOT_FOUND")

        current = run.status
        allowed = _VALID_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise AppError(
                detail=f"不允许从 {current} 转换到 {new_status}",
                status_code=409,
                code="INVALID_TRANSITION",
            )

        run.status = new_status
        if new_status not in ("queued", "running"):
            run.lease_owner = None
            run.lease_expires_at = None
        await db.flush()

        # I-02：状态变更计数（action 低基数；run_id 不入标签）
        workflow_runs_total.inc(action=run.action, status=new_status)

        # 发布状态变更事件
        event_type = f"run.{new_status}"
        await self._publisher.publish(db, run_id=run_id, event_type=event_type)

        return run

    async def cancel_run(self, db: AsyncSession, run_id: uuid.UUID) -> WorkflowRun:
        """取消 Run（I-01 协作式）。

        queued → 立即转为 cancelled；running → 置内存取消标记，工作流各节点
        在安全点检查后退出，由 worker 统一把 Run 转为 cancelled（保证 cancel
        后不创建新 Artifact）。已完成/失败/人工审查态不可取消。
        """
        result = await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
        run = result.scalar_one_or_none()
        if run is None:
            raise NotFoundError(detail=f"Run 不存在: {run_id}", code="RUN_NOT_FOUND")

        if run.status == "queued":
            return await self.transition_status(db, run_id, "cancelled")
        if run.status == "running":
            request_cancel(str(run_id))
            return run
        raise AppError(
            detail=f"Run 当前状态 {run.status} 不可取消",
            status_code=409,
            code="INVALID_TRANSITION",
        )

    async def get_run(self, db: AsyncSession, run_id: uuid.UUID) -> WorkflowRun:
        """查询 Run 详情。"""
        result = await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
        run = result.scalar_one_or_none()
        if run is None:
            raise NotFoundError(detail=f"Run 不存在: {run_id}", code="RUN_NOT_FOUND")
        return run

    async def list_runs_by_project(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> list[WorkflowRun]:
        """按项目分页查询 Run 列表。"""
        stmt = (
            select(WorkflowRun)
            .where(WorkflowRun.project_id == project_id)
            .order_by(WorkflowRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())
