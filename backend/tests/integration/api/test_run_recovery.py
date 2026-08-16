"""I-01 Run 恢复 API 契约测试（retry / cancel / error_code 暴露）。

覆盖（I-01 验收）：
- POST /runs/{id}/retry 守卫：completed/cancelled → 409 RUN_NOT_RETRYABLE；
  queued/running → 409 RUN_ALREADY_ACTIVE
- POST /runs/{id}/retry 成功路径：failed → queued + error 字段清空
- POST /runs/{id}/cancel：queued → 立即 cancelled；completed → 409
  INVALID_TRANSITION；running → 协作式（置取消标记，状态保持 running）
- GET /runs/{id} 暴露 error_code / error_detail（每失败均有 error_code）

全部使用 FakeLLM（APP_ENV=test），无真实 LLM 调用。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.workflows.checkpoint import cancel_requested, clear_cancel


async def _seed_run(
    *,
    status: str,
    error_code: str | None = None,
    error_detail: str | None = None,
    action: str = "create_script",
) -> tuple[str, str]:
    """直接在测试 DB 创建 Project + Run，返回 (project_id, run_id)。"""
    from app.db.models.project import Project
    from app.db.models.workflow_run import WorkflowRun
    from app.db.session import _async_session_factory

    assert _async_session_factory is not None  # conftest 初始化后才可使用
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    async with _async_session_factory() as db:
        db.add(Project(id=project_id, title="I-01 API 恢复测试", status="draft"))
        await db.flush()
        db.add(
            WorkflowRun(
                id=run_id,
                project_id=project_id,
                action=action,
                status=status,
                error_code=error_code,
                error_detail=error_detail,
            )
        )
        await db.commit()
    return str(project_id), str(run_id)


@pytest.mark.integration
@pytest.mark.asyncio
class TestRetryEndpoint:
    """retry 端点守卫与成功路径。"""

    async def test_retry_completed_rejected(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """终态 completed 不可重试 → 409 RUN_NOT_RETRYABLE。"""
        _, run_id = await _seed_run(status="completed")
        resp = await async_client.post(f"/api/v1/runs/{run_id}/retry")
        assert resp.status_code == 409
        assert resp.json()["code"] == "RUN_NOT_RETRYABLE"

    async def test_retry_cancelled_rejected(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """终态 cancelled 不可重试 → 409 RUN_NOT_RETRYABLE。"""
        _, run_id = await _seed_run(status="cancelled")
        resp = await async_client.post(f"/api/v1/runs/{run_id}/retry")
        assert resp.status_code == 409
        assert resp.json()["code"] == "RUN_NOT_RETRYABLE"

    async def test_retry_queued_rejected(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """排队中已有活跃 Worker → 409 RUN_ALREADY_ACTIVE。"""
        _, run_id = await _seed_run(status="queued")
        resp = await async_client.post(f"/api/v1/runs/{run_id}/retry")
        assert resp.status_code == 409
        assert resp.json()["code"] == "RUN_ALREADY_ACTIVE"

    async def test_retry_running_rejected(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """运行中不可重复重试 → 409 RUN_ALREADY_ACTIVE。"""
        _, run_id = await _seed_run(status="running")
        resp = await async_client.post(f"/api/v1/runs/{run_id}/retry")
        assert resp.status_code == 409
        assert resp.json()["code"] == "RUN_ALREADY_ACTIVE"

    async def test_retry_failed_goes_queued(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """failed → queued 并清空上一轮 error 字段。"""
        _, run_id = await _seed_run(
            status="failed", error_code="LLM_TIMEOUT", error_detail="模拟超时"
        )
        resp = await async_client.post(f"/api/v1/runs/{run_id}/retry")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert data["error_code"] is None
        assert data["error_detail"] is None


@pytest.mark.integration
@pytest.mark.asyncio
class TestCancelEndpoint:
    """cancel 端点：queued 立即取消、running 协作式、终态拒绝。"""

    async def test_cancel_queued_immediate(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """queued → 立即 cancelled。"""
        _, run_id = await _seed_run(status="queued")
        resp = await async_client.post(f"/api/v1/runs/{run_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    async def test_cancel_completed_rejected(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """终态 completed 不可取消 → 409 INVALID_TRANSITION。"""
        _, run_id = await _seed_run(status="completed")
        resp = await async_client.post(f"/api/v1/runs/{run_id}/cancel")
        assert resp.status_code == 409
        assert resp.json()["code"] == "INVALID_TRANSITION"

    async def test_cancel_running_cooperative(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """running → 协作式：置取消标记，状态保持 running 由 Worker 转换。"""
        _, run_id = await _seed_run(status="running")
        try:
            resp = await async_client.post(f"/api/v1/runs/{run_id}/cancel")
            assert resp.status_code == 200
            assert resp.json()["status"] == "running"
            assert cancel_requested(run_id) is True
        finally:
            clear_cancel(run_id)


@pytest.mark.integration
@pytest.mark.asyncio
class TestRunResponseErrorCode:
    """Run 响应暴露 error_code / error_detail（I-01）。"""

    async def test_get_run_exposes_error_fields(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """failed Run 的 GET 响应带 error_code / error_detail。"""
        _, run_id = await _seed_run(
            status="failed", error_code="RUN_BUDGET_EXCEEDED", error_detail="预算超限"
        )
        resp = await async_client.get(f"/api/v1/runs/{run_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert data["error_code"] == "RUN_BUDGET_EXCEEDED"
        assert data["error_detail"] == "预算超限"
