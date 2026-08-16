"""Run 运行诊断 — 按 run 聚合事件表时间线（I-02）。

GET /runs/{id}/diagnostics 的数据源：直接聚合 workflow_events 表
（node.started / node.completed / node.failed / run.llm_stats / run.failed），
不新增存储，复用「事件表 = 事实记录」的既有设计。

输出：
- nodes：每个节点的耗时（started → completed/failed 的时间差）与终态
- llm_calls / llm_tokens：Worker 在 finally 发布的 run.llm_stats 事件
- errors：run.failed 事件携带的 error_code + error_node
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.workflow_event import WorkflowEvent


class NodeTiming(BaseModel):
    """单个节点的执行时间线。"""

    node_name: str = Field(..., description="节点名")
    duration_ms: int | None = Field(None, description="节点耗时（毫秒）；无终态事件时为 None")
    status: str = Field("started", description="节点终态：started / completed / failed")


class DiagnosticsError(BaseModel):
    """Run 失败信息。"""

    error_code: str | None = Field(None, description="机器可读错误码")
    node_name: str | None = Field(None, description="失败节点名")


class RunDiagnosticsResponse(BaseModel):
    """GET /runs/{id}/diagnostics 响应体。"""

    run_id: str = Field(..., description="Run UUID")
    status: str = Field(..., description="Run 当前状态")
    total_duration_ms: int | None = Field(None, description="Run 总耗时（updated - created，毫秒）")
    nodes: list[NodeTiming] = Field(default_factory=list, description="节点时间线（按首次出现顺序）")
    llm_calls: int | None = Field(None, description="真实 LLM 调用次数（run.llm_stats，缺省 None）")
    llm_tokens: dict[str, int] | None = Field(None, description="LLM token 用量 {prompt, completion}")
    errors: list[DiagnosticsError] = Field(default_factory=list, description="失败信息（run.failed 事件）")


async def build_run_diagnostics(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> RunDiagnosticsResponse:
    """聚合事件表构建 Run 诊断。

    Args:
        db: 数据库会话
        run_id: 目标 Run

    Returns:
        节点时间线 / LLM 统计 / 错误列表的聚合结果。
    """
    from app.db.models.workflow_run import WorkflowRun

    run_result = await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
    run = run_result.scalar_one_or_none()
    if run is None:
        from app.core.errors import NotFoundError

        raise NotFoundError(detail=f"Run 不存在: {run_id}", code="RUN_NOT_FOUND")

    events_result = await db.execute(
        select(WorkflowEvent)
        .where(WorkflowEvent.run_id == run_id)
        .order_by(WorkflowEvent.sequence.asc())
    )
    events = list(events_result.scalars().all())

    nodes: dict[str, NodeTiming] = {}
    started: dict[str, WorkflowEvent] = {}
    node_order: list[str] = []
    llm_calls: int | None = None
    llm_tokens: dict[str, int] | None = None
    errors: list[DiagnosticsError] = []
    # run.failed 不带 error_node，回退到最近一次 node.failed 的节点名
    last_failed_node: str | None = None

    for ev in events:
        payload: dict[str, Any] = ev.payload or {}
        node = payload.get("node")
        if ev.type == "node.started" and node:
            if node not in started:
                node_order.append(node)
            started[node] = ev
        elif ev.type in ("node.completed", "node.failed") and node:
            st = started.get(node)
            duration_ms: int | None = None
            if st is not None:
                duration_ms = int(
                    (ev.created_at - st.created_at).total_seconds() * 1000
                )
            nodes[node] = NodeTiming(
                node_name=node,
                duration_ms=duration_ms,
                status="failed" if ev.type == "node.failed" else "completed",
            )
            if ev.type == "node.failed":
                last_failed_node = node
        elif ev.type == "run.llm_stats":
            llm_calls = payload.get("calls")
            if llm_calls is not None:
                llm_tokens = {
                    "prompt": payload.get("prompt_tokens", 0),
                    "completion": payload.get("completion_tokens", 0),
                }
        elif ev.type == "run.failed":
            errors.append(
                DiagnosticsError(
                    error_code=payload.get("error_code"),
                    node_name=payload.get("error_node") or last_failed_node,
                )
            )

    # 仅 started 无终态（如取消中断）的节点 → status=started
    for name in node_order:
        nodes.setdefault(name, NodeTiming(node_name=name, duration_ms=None, status="started"))

    total_duration_ms: int | None = None
    if run.created_at and run.updated_at:
        total_duration_ms = int((run.updated_at - run.created_at).total_seconds() * 1000)

    return RunDiagnosticsResponse(
        run_id=str(run.id),
        status=run.status,
        total_duration_ms=total_duration_ms,
        nodes=[nodes[n] for n in node_order],
        llm_calls=llm_calls,
        llm_tokens=llm_tokens,
        errors=errors,
    )
