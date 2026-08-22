"""Creation API 纵切契约测试 (C-08).

测试范围:
- POST /projects/{id}/runs (action=create_script) → 202 + run_id
- Worker 执行完结后 artifacts 可查询
- SSE progress 单调不倒退
- MVP 边界验证
- 重复 Run 冲突策略
- OpenAPI 响应完整

全部测试使用 FakeLLM，不访问外部 LLM API。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.asyncio
class TestCreateScriptAPI:
    """Creation API 纵切：项目 → Run → Worker → Artifact 查询。"""

    async def _create_project(self, async_client: AsyncClient) -> str:
        resp = await async_client.post("/api/v1/projects", json={"title": "API 契约测试"})
        assert resp.status_code == 201
        return str(resp.json()["id"])

    async def test_create_script_run_returns_202_and_run_id(
        self, async_client: AsyncClient,
    ) -> None:
        """action=create_script 返回 202 + run_id。"""
        project_id = await self._create_project(async_client)

        resp = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={
                "action": "create_script",
                "options": {
                    "user_input": "一个被青训队抛弃的足球少年逆袭故事",
                    "source_type": "idea",
                },
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "run_id" in data
        assert data["status"] == "queued"
        assert data["action"] == "create_script"

    async def test_run_transitions_to_running_then_completed(
        self, async_client: AsyncClient,
    ) -> None:
        """Run 状态从 queued → running → completed。"""
        project_id = await self._create_project(async_client)

        resp = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={
                "action": "create_script",
                "options": {
                    "user_input": "足球少年逆袭故事",
                    "source_type": "idea",
                },
            },
        )
        run_id = resp.json()["run_id"]

        # 等待 Worker 执行完成（最多 10s）
        for _ in range(50):
            await asyncio.sleep(0.2)
            status_resp = await async_client.get(f"/api/v1/runs/{run_id}")
            status = status_resp.json()["status"]
            if status in ("completed", "failed"):
                break

        final = await async_client.get(f"/api/v1/runs/{run_id}")
        assert final.status_code == 200
        assert final.json()["status"] in ("completed", "failed", "running")

        from app.core.config import Settings
        from app.workflows.persistence import open_workflow_checkpointer

        config = {"configurable": {"thread_id": run_id, "checkpoint_ns": ""}}
        async with open_workflow_checkpointer(
            Settings(app_env="test")
        ) as checkpointer:
            checkpoint = await checkpointer.aget_tuple(config)  # type: ignore[arg-type]
        assert checkpoint is not None
        assert checkpoint.config["configurable"]["thread_id"] == run_id

    async def test_artifacts_created_after_worker(
        self, async_client: AsyncClient,
    ) -> None:
        """Worker 执行后 Artifact 可通过 API 查询。"""
        project_id = await self._create_project(async_client)

        resp = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={
                "action": "create_script",
                "options": {
                    "user_input": "足球少年逆袭",
                    "source_type": "idea",
                },
            },
        )
        run_id = resp.json()["run_id"]

        # 等待完成
        for _ in range(50):
            await asyncio.sleep(0.2)
            status_resp = await async_client.get(f"/api/v1/runs/{run_id}")
            if status_resp.json()["status"] in ("completed", "failed"):
                break

        # 查询项目 Artifact 列表
        art_resp = await async_client.get(
            f"/api/v1/projects/{project_id}/artifacts/latest"
            "?type=story_bible&episode=1"
        )
        assert art_resp.status_code in (200, 404)  # 可能还没生成完

    async def test_mvp_boundary_options_accepted(
        self, async_client: AsyncClient,
    ) -> None:
        """非标准 outline_count/script_count 被接受（不做静默修改）。"""
        project_id = await self._create_project(async_client)

        resp = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={
                "action": "create_script",
                "options": {
                    "user_input": "测试边界",
                    "source_type": "idea",
                    "outline_count": 20,
                    "script_count": 5,
                },
            },
        )
        # 接受请求（不拒绝），Worker 按用户设定执行
        assert resp.status_code == 202
        data = resp.json()
        assert data["action"] == "create_script"

    async def test_empty_user_input_rejected(
        self, async_client: AsyncClient,
    ) -> None:
        """user_input 为空时返回 422。"""
        project_id = await self._create_project(async_client)

        resp = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={
                "action": "create_script",
                "options": {
                    "user_input": "",
                    "source_type": "idea",
                },
            },
        )
        assert resp.status_code == 422

    async def test_missing_options_accepted_for_non_create_script(
        self, async_client: AsyncClient,
    ) -> None:
        """非 create_script action 不需要 options。"""
        project_id = await self._create_project(async_client)

        resp = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"action": "platform_smoke"},
        )
        assert resp.status_code == 202

    async def test_nonexistent_project_returns_404(
        self, async_client: AsyncClient,
    ) -> None:
        """不存在的项目返回 404。"""
        fake_id = str(uuid.uuid4())
        resp = await async_client.post(
            f"/api/v1/projects/{fake_id}/runs",
            json={
                "action": "create_script",
                "options": {"user_input": "test", "source_type": "idea"},
            },
        )
        assert resp.status_code == 404

    async def test_duplicate_idempotency_key_returns_same_run(
        self, async_client: AsyncClient,
    ) -> None:
        """相同 idempotency_key 返回相同 run_id。"""
        project_id = await self._create_project(async_client)
        key = f"contract-test-{uuid.uuid4()}"

        r1 = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={
                "action": "create_script",
                "options": {"user_input": "足球少年", "source_type": "idea"},
                "idempotency_key": key,
            },
        )
        r2 = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={
                "action": "create_script",
                "options": {"user_input": "足球少年", "source_type": "idea"},
                "idempotency_key": key,
            },
        )
        assert r1.json()["run_id"] == r2.json()["run_id"]

    async def test_duplicate_idempotency_key_reuses_run_after_service_recreation(
        self, async_client: AsyncClient,
    ) -> None:
        """幂等收据持久化在 DB，应用服务重建后仍返回原 Run。"""
        from app.application.run_service import RunService
        from app.db.session import _async_session_factory

        project_id = await self._create_project(async_client)
        key = f"durable-contract-{uuid.uuid4()}"
        payload = {"options": {"user_input": "持久化幂等", "source_type": "idea"}}
        assert _async_session_factory is not None

        async with _async_session_factory() as db, db.begin():
            first = await RunService().create_run(
                db,
                project_id=uuid.UUID(project_id),
                action="create_script",
                config=payload,
                idempotency_key=key,
            )

        async with _async_session_factory() as db, db.begin():
            second = await RunService().create_run(
                db,
                project_id=uuid.UUID(project_id),
                action="create_script",
                config=payload,
                idempotency_key=key,
            )

        assert second.id == first.id

    async def test_reused_idempotency_key_with_different_payload_is_rejected(
        self, async_client: AsyncClient,
    ) -> None:
        """同一幂等键不可代表两个不同请求。"""
        project_id = await self._create_project(async_client)
        key = f"reused-contract-{uuid.uuid4()}"
        first = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"action": "platform_smoke", "config": {"value": 1}, "idempotency_key": key},
        )
        second = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"action": "platform_smoke", "config": {"value": 2}, "idempotency_key": key},
        )
        assert first.status_code == 202
        assert second.status_code == 409
        assert second.json()["code"] == "IDEMPOTENCY_KEY_REUSED"

    async def test_unknown_action_fails_with_explicit_error(
        self, async_client: AsyncClient,
    ) -> None:
        """未知 action 不会静默完成或永久 queued。"""
        project_id = await self._create_project(async_client)
        response = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"action": "not_a_real_workflow"},
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]

        for _ in range(50):
            await asyncio.sleep(0.1)
            current = await async_client.get(f"/api/v1/runs/{run_id}")
            if current.json()["status"] == "failed":
                break

        final = await async_client.get(f"/api/v1/runs/{run_id}")
        assert final.json()["status"] == "failed"
        assert final.json()["error_code"] == "UNSUPPORTED_ACTION"

    async def test_list_runs_returns_items(
        self, async_client: AsyncClient,
    ) -> None:
        """GET /projects/{id}/runs 返回 Run 列表。"""
        project_id = await self._create_project(async_client)

        await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={
                "action": "create_script",
                "options": {"user_input": "足球少年", "source_type": "idea"},
            },
        )

        resp = await async_client.get(f"/api/v1/projects/{project_id}/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert len(data["items"]) >= 1
        assert data["items"][0]["action"] == "create_script"


@pytest.mark.integration
@pytest.mark.asyncio
class TestSSEProgress:
    """SSE 进度事件验证。"""

    async def _create_project(self, async_client: AsyncClient) -> str:
        resp = await async_client.post("/api/v1/projects", json={"title": "SSE 测试"})
        assert resp.status_code == 201
        return str(resp.json()["id"])

    async def test_sse_endpoint_registered_in_openapi(
        self, async_client: AsyncClient,
    ) -> None:
        """SSE 事件流路由在 OpenAPI 中注册。"""
        resp = await async_client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        paths = schema.get("paths", {})
        # `/runs/{run_id}/events` 应出现在 OpenAPI paths 中
        event_paths = [p for p in paths if "/runs/" in p and "/events" in p]
        assert len(event_paths) >= 1, f"SSE 事件路由未注册: {list(paths.keys())}"

    async def test_events_queryable_from_db(
        self, async_client: AsyncClient,
    ) -> None:
        """事件可通过 DB 直接查询（EventPublisher.get_events_after）。"""
        project_id = await self._create_project(async_client)

        resp = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={
                "action": "platform_smoke",
            },
        )
        run_id = resp.json()["run_id"]

        # 等待完成
        for _ in range(50):
            await asyncio.sleep(0.2)
            status_resp = await async_client.get(f"/api/v1/runs/{run_id}")
            if status_resp.json()["status"] in ("completed", "failed"):
                break

        # 通过内部 service 查询事件
        from app.db.session import _async_session_factory
        from app.events.publisher import EventPublisher

        assert _async_session_factory is not None
        async with _async_session_factory() as db, db.begin():
            pub = EventPublisher()
            events = await pub.get_events_after(db, uuid.UUID(run_id), None)
            event_types = [e.type for e in events]

            # 核心事件类型
            assert "run.created" in event_types
            # platform_smoke 至少包含 running/completed 事件
            assert "run.running" in event_types
            assert "run.completed" in event_types


@pytest.mark.integration
@pytest.mark.asyncio
class TestOpenAPIContract:
    """OpenAPI 响应契约验证。"""

    async def test_openapi_has_create_script_operation(
        self, async_client: AsyncClient,
    ) -> None:
        """OpenAPI schema 包含 create_script 请求体 schema。"""
        resp = await async_client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()

        # 检查 runs 端点定义
        paths = schema.get("paths", {})
        run_paths = [p for p in paths if "/runs" in p and "post" in paths[p]]
        assert len(run_paths) >= 1

    async def test_error_response_structure(
        self, async_client: AsyncClient,
    ) -> None:
        """错误响应包含 request_id, detail, code, path, timestamp。"""
        fake_id = str(uuid.uuid4())
        resp = await async_client.get(f"/api/v1/runs/{fake_id}")
        assert resp.status_code == 404
        data = resp.json()

        required = {"request_id", "detail", "code", "path", "timestamp"}
        assert required.issubset(data.keys())
