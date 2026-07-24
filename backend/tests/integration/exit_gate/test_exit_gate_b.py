"""Phase B Exit Gate 验收测试。

验证 DEV_PLAN §Phase B Exit Gate 全部 6 个场景：
1. 创建项目和会话
2. 创建 action=platform_smoke 的 Run
3. Worker 生成测试 Artifact
4. SSE 事件流完整
5. 查询 Artifact 内容与版本
6. 从 DB 补发事件（Redis 清空后）
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.asyncio
class TestExitGateB:
    """Phase B Exit Gate — 最小纵切验收。"""

    async def test_gate_1_create_project_and_conversation(
        self, async_client: AsyncClient,
    ) -> None:
        """场景 1：创建项目和会话。"""
        # 创建项目
        proj_resp = await async_client.post(
            "/api/v1/projects",
            json={"title": "Exit Gate B 验收项目"},
        )
        assert proj_resp.status_code == 201
        project_id = proj_resp.json()["id"]
        assert proj_resp.json()["status"] == "draft"

        # 创建会话
        conv_resp = await async_client.post(
            f"/api/v1/projects/{project_id}/conversations",
            json={"title": "验收会话"},
        )
        assert conv_resp.status_code == 201
        assert conv_resp.json()["project_id"] == project_id

        # 追加消息
        msg_resp = await async_client.post(
            f"/api/v1/conversations/{conv_resp.json()['id']}/messages",
            json={"role": "user", "content": "平台冒烟测试"},
        )
        assert msg_resp.status_code == 201
        assert msg_resp.json()["sequence"] == 1

    async def test_gate_2_create_platform_smoke_run(
        self, async_client: AsyncClient,
    ) -> None:
        """场景 2：创建 action=platform_smoke 的 Run。"""
        proj_resp = await async_client.post(
            "/api/v1/projects", json={"title": "Smoke Run 项目"},
        )
        project_id = proj_resp.json()["id"]

        run_resp = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={
                "action": "platform_smoke",
                "config": {"smoke": True},
                "idempotency_key": f"smoke-{uuid.uuid4()}",
            },
        )
        assert run_resp.status_code == 202
        data = run_resp.json()
        assert data["status"] == "queued"
        assert data["action"] == "platform_smoke"

        # 验证幂等：相同 key 返回相同 run_id
        key = f"gate-b-idempotent-{uuid.uuid4()}"
        r1 = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"action": "platform_smoke", "idempotency_key": key},
        )
        r2 = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"action": "platform_smoke", "idempotency_key": key},
        )
        assert r1.json()["run_id"] == r2.json()["run_id"]

    async def test_gate_3_worker_generates_smoke_artifact(
        self, async_client: AsyncClient,
    ) -> None:
        """场景 3：Worker 通过 FakeLLM 生成测试 Artifact。

        模拟 Worker 流程：
        1. 查询 queued Run
        2. 更新为 running
        3. 用 ArtifactService 创建 smoke Artifact
        4. 发布事件
        5. 更新为 completed
        """
        proj_resp = await async_client.post(
            "/api/v1/projects", json={"title": "Worker 验收"},
        )
        project_id = uuid.UUID(proj_resp.json()["id"])

        run_resp = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"action": "platform_smoke"},
        )
        run_id = uuid.UUID(run_resp.json()["run_id"])

        # 模拟 Worker：直接通过内部 service 操作
        from app.application.artifact_service import ArtifactService
        from app.application.run_service import RunService
        from app.db.session import _async_session_factory

        assert _async_session_factory is not None, "DB not initialized"
        async with _async_session_factory() as db:
            run_svc = RunService()
            artifact_svc = ArtifactService()

            # 转换状态：queued → running
            await run_svc.transition_status(db, run_id, "running")

            # 生成 smoke Artifact
            artifact = await artifact_svc.create_validated_artifact(
                db,
                project_id=project_id,
                artifact_type="story_bible",
                content={
                    "title": "Smoke Test Bible",
                    "logline": "Platform smoke test artifact",
                    "genre": "test",
                    "tone": [],
                    "protagonist_seed": "smoke test",
                    "conflict_seed": "smoke test",
                    "source_type": "idea",
                    "characters": [],
                    "world_building": "",
                    "opening_hook": "",
                    "story_engine": "",
                },
                prompt_version="smoke-1.0",
            )

            # 发布 artifact.created 事件
            from app.events.publisher import EventPublisher
            pub = EventPublisher()
            await pub.publish(
                db,
                run_id=run_id,
                event_type="artifact.created",
                payload={
                    "artifact_id": str(artifact.id),
                    "artifact_type": artifact.type,
                    "version": artifact.version,
                    "stage": "platform_smoke",
                    "progress": 1.0,
                    "message": "Smoke Artifact 已生成",
                },
            )

            # 转换状态：running → completed
            await run_svc.transition_status(db, run_id, "completed")

            # 验证 Run 状态
            run_after = await run_svc.get_run(db, run_id)
            assert run_after.status == "completed"

            # 验证 Artifact 可查询
            latest = await artifact_svc.get_latest(db, project_id, "story_bible")
            assert latest.status == "valid"
            assert latest.version == 1
            assert latest.content["title"] == "Smoke Test Bible"

    async def test_gate_4_sse_events_complete(
        self, async_client: AsyncClient,
    ) -> None:
        """场景 4：事件流包含 started、artifact.created、completed。

        通过 EventPublisher.get_events_after 验证事件序列。
        """
        proj_resp = await async_client.post(
            "/api/v1/projects", json={"title": "SSE 验收"},
        )
        project_id = uuid.UUID(proj_resp.json()["id"])

        run_resp = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"action": "platform_smoke"},
        )
        run_id = uuid.UUID(run_resp.json()["run_id"])

        from app.application.run_service import RunService
        from app.db.session import _async_session_factory

        assert _async_session_factory is not None, "DB not initialized"
        async with _async_session_factory() as db:
            run_svc = RunService()

            # 模拟完整流程
            await run_svc.transition_status(db, run_id, "running")
            # 发布 artifact.created
            from app.events.publisher import EventPublisher
            pub = EventPublisher()
            await pub.publish(db, run_id=run_id, event_type="artifact.created")
            await run_svc.transition_status(db, run_id, "completed")

            # 查询所有事件
            events = await pub.get_events_after(db, run_id, None)
            event_types = [e.type for e in events]

            # 验证事件顺序包含关键类型
            assert "run.created" in event_types
            assert "run.running" in event_types
            assert "artifact.created" in event_types
            assert "run.completed" in event_types

            # sequence 严格递增
            sequences = [e.sequence for e in events]
            assert sequences == sorted(sequences)
            assert len(sequences) == len(set(sequences))  # 无重复

    async def test_gate_5_query_artifact(
        self, async_client: AsyncClient,
    ) -> None:
        """场景 5：查询 Artifact 内容与版本。"""
        proj_resp = await async_client.post(
            "/api/v1/projects", json={"title": "Artifact 查询验收"},
        )
        project_id = uuid.UUID(proj_resp.json()["id"])

        # 通过 API 创建 Run
        await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"action": "platform_smoke"},
        )

        # 通过 service 创建 Artifact
        from app.application.artifact_service import ArtifactService
        from app.db.session import _async_session_factory

        assert _async_session_factory is not None, "DB not initialized"
        async with _async_session_factory() as db:
            svc = ArtifactService()
            a1 = await svc.create_validated_artifact(
                db,
                project_id=project_id,
                artifact_type="script_draft",
                content={
                    "title": "测试剧本 v1",
                    "scenes": [{"id": "s1", "title": "开场", "dialogues": []}],
                },
            )
            a2 = await svc.create_validated_artifact(
                db,
                project_id=project_id,
                artifact_type="script_draft",
                content={
                    "title": "测试剧本 v2",
                    "scenes": [{"id": "s1", "title": "开场修订", "dialogues": []}],
                },
            )

            # 验证版本递增
            assert a1.version == 1
            assert a2.version == 2
            assert a1.id != a2.id  # 不同记录

            # 通过 API 查询
            for a in (a1, a2):
                resp = await async_client.get(f"/api/v1/artifacts/{a.id}")
                assert resp.status_code == 200
                assert resp.json()["version"] == a.version

            # 版本历史
            versions_resp = await async_client.get(
                f"/api/v1/artifacts/{a1.id}/versions"
            )
            assert len(versions_resp.json()) == 2

            # latest
            latest_resp = await async_client.get(
                f"/api/v1/projects/{project_id}/artifacts/latest"
                "?type=script_draft&episode=1"
            )
            assert latest_resp.json()["version"] == 2

    async def test_gate_6_db_event_replay(
        self, async_client: AsyncClient,
    ) -> None:
        """场景 6：从 DB 补发事件（模拟 Redis 清空后）。

        验证 EventPublisher.get_events_after 可补发历史。
        """
        proj_resp = await async_client.post(
            "/api/v1/projects", json={"title": "DB Replay 验收"},
        )
        project_id = uuid.UUID(proj_resp.json()["id"])

        run_resp = await async_client.post(
            f"/api/v1/projects/{project_id}/runs",
            json={"action": "platform_smoke"},
        )
        run_id = uuid.UUID(run_resp.json()["run_id"])

        from app.db.session import _async_session_factory
        from app.events.publisher import EventPublisher

        assert _async_session_factory is not None, "DB not initialized"
        async with _async_session_factory() as db:
            pub = EventPublisher()

            # 发布多个事件
            e1 = await pub.publish(db, run_id=run_id, event_type="run.created")
            e2 = await pub.publish(db, run_id=run_id, event_type="node.started")
            e3 = await pub.publish(db, run_id=run_id, event_type="node.completed")

            # 模拟断线重连：用 e1 的 ID 作为 Last-Event-ID
            replay = await pub.get_events_after(db, run_id, str(e1.id))
            assert len(replay) == 2  # e2 + e3
            assert replay[0].id == e2.id
            assert replay[1].id == e3.id

            # 无 Last-Event-ID 返回全部
            all_events = await pub.get_events_after(db, run_id, None)
            assert len(all_events) == 3
