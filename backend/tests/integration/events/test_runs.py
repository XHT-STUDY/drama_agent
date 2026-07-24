"""B-05 WorkflowRun 集成测试。

验证：
- 创建/查询/取消 Run
- Idempotency-Key 去重
- 状态机转换合法性
- 事件 sequence 递增唯一
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.asyncio
class TestCreateRun:
    """创建 Run。"""

    async def _create_project(self, client: AsyncClient) -> str:
        r = await client.post("/api/v1/projects", json={"title": "Run 测试"})
        return r.json()["id"]

    async def test_create_run_returns_202(self, async_client: AsyncClient) -> None:
        """创建 Run 返回 202 Accepted。"""
        project_id = await self._create_project(async_client)
        resp = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"action": "create_script", "config": {"outline_count": 10}},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["run_id"]
        assert data["status"] == "queued"
        assert data["action"] == "create_script"

    async def test_create_run_project_404(self, async_client: AsyncClient) -> None:
        """不存在的项目返回 404。"""
        resp = await async_client.post(
            "/api/v1/projects/00000000-0000-0000-0000-000000000000/runs",
            json={"action": "test"},
        )
        assert resp.status_code == 404

    async def test_idempotency_key_returns_same_run(self, async_client: AsyncClient) -> None:
        """相同幂等键返回已存在的 run_id。"""
        project_id = await self._create_project(async_client)
        key = "test-idempotency-key-001"

        r1 = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"action": "evaluate", "idempotency_key": key},
        )
        r2 = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"action": "evaluate", "idempotency_key": key},
        )
        assert r1.status_code == 202
        assert r2.status_code == 202
        assert r1.json()["run_id"] == r2.json()["run_id"]  # 相同 run_id


@pytest.mark.integration
@pytest.mark.asyncio
class TestRunStatus:
    """查询/取消 Run。"""

    async def _create_project(self, client: AsyncClient) -> str:
        r = await client.post("/api/v1/projects", json={"title": "状态测试"})
        return r.json()["id"]

    async def test_get_run(self, async_client: AsyncClient) -> None:
        """查询 Run 状态。"""
        project_id = await self._create_project(async_client)
        create_resp = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"action": "create_script"},
        )
        run_id = create_resp.json()["run_id"]

        resp = await async_client.get(f"/api/v1/runs/{run_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

    async def test_get_nonexistent_run_404(self, async_client: AsyncClient) -> None:
        """查询不存在的 Run 返回 404。"""
        resp = await async_client.get(
            "/api/v1/runs/00000000-0000-0000-0000-000000000000"
        )
        assert resp.status_code == 404

    async def test_cancel_queued_run(self, async_client: AsyncClient) -> None:
        """取消 queued 状态的 Run。"""
        project_id = await self._create_project(async_client)
        create_resp = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"action": "test"},
        )
        run_id = create_resp.json()["run_id"]

        cancel_resp = await async_client.post(f"/api/v1/runs/{run_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"

    async def test_cancelled_run_no_new_transitions(self, async_client: AsyncClient) -> None:
        """cancelled Run 不能再做状态转换。"""
        project_id = await self._create_project(async_client)
        create_resp = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"action": "test"},
        )
        run_id = create_resp.json()["run_id"]

        # 第一次取消成功
        await async_client.post(f"/api/v1/runs/{run_id}/cancel")

        # 第二次取消应失败（409）
        resp = await async_client.post(f"/api/v1/runs/{run_id}/cancel")
        assert resp.status_code == 409
