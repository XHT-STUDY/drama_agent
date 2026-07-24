"""Checkpoint 基础 — Workflow 状态检查点。

MVP 阶段提供最简实现：
- save_checkpoint: 将 LangGraph State 摘要写入 workflow_run.state_summary
- load_checkpoint: 从 state_summary 恢复
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.workflow_run import WorkflowRun


async def save_checkpoint(
    db: AsyncSession,
    run_id: uuid.UUID,
    state_summary: dict[str, Any],
) -> None:
    """保存 Workflow 状态摘要到 workflow_run。

    State 只存 ID 和轻量字段（DEV_PLAN §2.2），
    大文本存 Artifact。
    """
    result = await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is not None:
        run.state_summary = state_summary
        await db.flush()


async def load_checkpoint(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> dict[str, Any] | None:
    """加载 Workflow 状态摘要。"""
    result = await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        return None
    return run.state_summary
