"""L-3 确认门续跑 API 测试。

- 非 stage_gate 的 needs_review Run → 409 不可续；
- staged Run 停门后 continue → queued、options 无 stop_after、
  state_summary 无 stage_gate 且大纲刷新为最新 valid；
- 重复 continue（queued）→ 409 RUN_ALREADY_ACTIVE。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.artifact_service import ArtifactService
from app.db.models.project import Project
from app.db.models.workflow_run import WorkflowRun


@pytest_asyncio.fixture
async def db_session(test_engine: Any) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(app: Any) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_gated_run(
    db: AsyncSession, *, with_newer_outline: bool = False
) -> tuple[str, WorkflowRun]:
    """播种一个停在 stage_gate=outline 的 needs_review Run。"""
    import json
    from pathlib import Path

    golden = Path(__file__).resolve().parents[2] / "golden"

    def load(name: str) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(
            (golden / f"{name}.json").read_text(encoding="utf-8")
        )
        return data

    project = Project(title="L-3 续跑", target_episode_count=10)
    db.add(project)
    await db.flush()
    svc = ArtifactService()
    await svc.create_validated_artifact(
        db, project_id=project.id, artifact_type="story_bible",
        content=load("story_bible_valid"),
    )
    outline_v1 = await svc.create_validated_artifact(
        db, project_id=project.id, artifact_type="episode_outline_set",
        content=load("outline_set_valid"),
    )
    outline_id = str(outline_v1.id)
    if with_newer_outline:
        newer = await svc.create_validated_artifact(
            db, project_id=project.id, artifact_type="episode_outline_set",
            content=load("outline_revision_valid"),
        )
        outline_id = str(newer.id)
    run = WorkflowRun(
        project_id=project.id,
        action="create_script",
        status="needs_review",
        config_snapshot={"options": {
            "user_input": "x", "outline_count": 10, "script_count": 3,
            "stop_after": "outline",
        }},
        state_summary={
            "stage_gate": "outline",
            "stop_after": "outline",
            "story_bible_artifact_id": "00000000-0000-0000-0000-000000000001",
            "outline_set_artifact_id": "00000000-0000-0000-0000-000000000002",
            "completed_nodes": ["normalize", "retrieve", "story_bible", "outline"],
        },
    )
    db.add(run)
    await db.commit()
    return outline_id, run


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_clears_gate_and_refreshes_outline(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """continue → queued、门字段剥离、大纲刷新为最新 valid 版本。"""
    from app.api.v1 import runs as runs_module

    monkeypatch.setattr(runs_module, "schedule_worker", lambda *a: None)
    latest_outline_id, run = await _seed_gated_run(db_session, with_newer_outline=True)

    resp = await client.post(f"/api/v1/runs/{run.id}/continue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    options = body["config_snapshot"]["options"]
    assert "stop_after" not in options

    await db_session.refresh(run)
    assert "stage_gate" not in (run.state_summary or {})
    assert "stop_after" not in (run.state_summary or {})
    assert (run.state_summary or {})["outline_set_artifact_id"] == latest_outline_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_rejects_non_gated_run(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """普通 needs_review（无 stage_gate）→ 409 不可续。"""
    project = Project(title="不可续", target_episode_count=10)
    db_session.add(project)
    await db_session.flush()
    run = WorkflowRun(
        project_id=project.id, action="create_script", status="needs_review",
        config_snapshot={}, state_summary={"needs_manual_review_reason": "人工复核"},
    )
    db_session.add(run)
    await db_session.commit()

    resp = await client.post(f"/api/v1/runs/{run.id}/continue")
    assert resp.status_code == 409
    assert resp.json()["code"] == "RUN_NOT_RETRYABLE"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_twice_conflicts(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """续跑入队后重复 continue → 409 RUN_ALREADY_ACTIVE。"""
    from app.api.v1 import runs as runs_module

    monkeypatch.setattr(runs_module, "schedule_worker", lambda *a: None)
    _outline_id, run = await _seed_gated_run(db_session)

    first = await client.post(f"/api/v1/runs/{run.id}/continue")
    assert first.status_code == 200
    second = await client.post(f"/api/v1/runs/{run.id}/continue")
    assert second.status_code == 409
    assert second.json()["code"] == "RUN_ALREADY_ACTIVE"
