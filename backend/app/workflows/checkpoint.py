"""Checkpoint 基础 — Workflow 状态检查点 + 协作式取消 + 失败分类 (I-01)。

三部分职责：
1. 状态检查点（MVP 既有）：save_checkpoint / load_checkpoint 读写
   workflow_run.state_summary；retry 时以 state_summary 恢复 completed_nodes。
2. 协作式取消：模块级 cancel registry（run_id → 取消标记，跨 asyncio Task
   共享）+ 节点入口 raise_if_cancelled()。取消标记由 API 层的 cancel 端点
   设置，Worker 运行到下一节点守卫时抛出 RunCancelledError 中断工作流。
   RunCancelledError 继承 BaseException——避免被节点内 `except Exception`
   吞掉（关键验收：cancel 后不创建新 Artifact）。
3. 失败分类：classify_error_code / node_failure 从异常提取机器可读错误码
   （AppError.code 优先，LLM 错误码从消息文本兜底），供 Run 落库。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.workflow_run import WorkflowRun
from app.llm.retry import LLM_ERROR_RUN_CODES

# ========================================================================
# 协作式取消
# ========================================================================

# 模块级取消注册表：run_id(str) → 已请求取消。跨 asyncio Task 共享
#（cancel 端点在 HTTP Task 中写、Worker 在自身 Task 中读）。
_cancel_registry: dict[str, bool] = {}


class RunCancelledError(BaseException):
    """工作流被请求取消（协作式中断信号）。

    继承 BaseException 而非 Exception：节点内的 `except Exception`
    不会吞掉它，保证取消标记在节点任意位置（含 Artifact 写入前）
    抛出后都能一路传播到 Worker 并被标记为 cancelled。
    """

    def __init__(self, run_id: str = "") -> None:
        super().__init__(f"Run 已请求取消: {run_id}")


def request_cancel(run_id: str) -> None:
    """请求取消某 run（幂等）。Worker 在下一节点守卫处中断。"""
    _cancel_registry[run_id] = True


def cancel_requested(run_id: str) -> bool:
    """该 run 是否已被请求取消。"""
    return _cancel_registry.get(run_id, False)


def clear_cancel(run_id: str) -> None:
    """清除取消标记（run 达到终态后调用，避免 registry 泄漏）。"""
    _cancel_registry.pop(run_id, None)


def raise_if_cancelled(run_id: str) -> None:
    """节点守卫：若当前 run 已被请求取消则抛 RunCancelledError。

    调用位置：每个节点入口（completed_nodes 早退之前）、
    以及 write_episodes 等多 Artifact 节点的循环内写入前。
    """
    if cancel_requested(run_id):
        raise RunCancelledError(run_id)


# ========================================================================
# 失败分类（error_code 落库）
# ========================================================================


def classify_error_code(exc: BaseException) -> str | None:
    """从异常提取机器可读错误码。

    优先级：
    1. AppError.code（如 BudgetExceededError → RUN_BUDGET_EXCEEDED）；
    2. LLM 错误码文本（skill 抛出的 "LLM 调用失败: llm_timeout - ..."）→
       LLM_TIMEOUT / LLM_RATE_LIMITED / LLM_PROVIDER_ERROR / LLM_INVALID_OUTPUT。
    """
    from app.core.errors import AppError

    if isinstance(exc, AppError):
        return exc.code
    text = str(exc)
    for raw_code in LLM_ERROR_RUN_CODES:
        if raw_code in text:
            return LLM_ERROR_RUN_CODES[raw_code]
    return None


def node_failure(node: str, exc: BaseException) -> dict[str, Any]:
    """构造节点失败返回（含 error_code，I-01 验收"所有失败有 error_code"）。"""
    return {
        "status": "failed",
        "error_node": node,
        "error_code": classify_error_code(exc),
        "error_detail": str(exc),
    }


# ========================================================================
# 状态检查点（state_summary）
# ========================================================================


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
