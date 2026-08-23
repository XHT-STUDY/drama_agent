"""J-12 E2E 路径复现：agent_e2e 场景下的 Turn → 修订 Run 全链路。

复现 e2e test 4 的精确路径：先创作（lowscore → ep1 自动修订 → needs_review），
再带 active context 发起第 3 集修订 Turn → 确认 → dispatcher 执行 revise_script。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.workflow_run import WorkflowRun

GOLDEN = Path(__file__).resolve().parents[1] / "golden"


def _load(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8")))


@pytest_asyncio.fixture
async def db_session(test_engine: Any) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(app: Any, test_engine: Any) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _run_to_terminal(
    db_session: AsyncSession, run_id: uuid.UUID, action: str, owner: str = "repro"
) -> WorkflowRun:
    """直接调用 dispatcher 执行器把 Run 跑到终态。"""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from app.application.workflow_dispatcher import _execute_workflow

    # 模拟 dispatcher 领取：queued→running + 租约（executor 校验 owner）
    await db_session.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == run_id)
        .values(
            status="running",
            lease_owner=owner,
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            attempt_count=1,
        )
    )
    await db_session.commit()
    await _execute_workflow(run_id, action, {}, owner)
    result = await db_session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
    run = result.scalar_one()
    await db_session.refresh(run)
    return run


@pytest.mark.integration
@pytest.mark.asyncio
async def test_e2e_path_creation_then_episode3_revision(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_LLM_SCENARIO", "agent_e2e")
    # 重置进程级服务单例，使新场景生效
    import app.api.dependencies as deps

    deps._agent_command_service = None  # noqa: SLF001

    # 1) 创建项目 + 创作 Turn（stub → create_script plan）
    resp = await client.post("/api/v1/projects", json={"title": "e2e 路径复现", "target_episode_count": 10})
    project_id = resp.json()["id"]

    turn = await client.post(
        f"/api/v1/projects/{project_id}/agent/turns",
        json={"content": "写一个被青训队抛弃的足球少年逆袭的短剧", "idempotency_key": "e2e-1"},
    )
    assert turn.status_code == 200, turn.text
    action_id = turn.json()["action_id"]
    assert action_id, turn.json()

    confirm = await client.post(f"/api/v1/agent/actions/{action_id}/confirm")
    assert confirm.status_code == 202, confirm.text
    creation_run_id = uuid.UUID(confirm.json()["run"]["run_id"])

    creation = await _run_to_terminal(db_session, creation_run_id, "create_script")
    assert creation.status in ("completed", "needs_review"), (
        creation.status,
        creation.error_code,
        creation.error_detail,
    )

    # 2) 第 3 集剧本与绑定评估存在（creation 已评估三集）
    from app.artifacts.store import ArtifactStore
    from app.db.repositories.artifacts import ArtifactRepository

    store = ArtifactStore()
    s3 = await store.get_latest(db_session, uuid.UUID(project_id), "script_draft", 3)
    assert s3 is not None, "创作应产出第 3 集剧本"
    repo = ArtifactRepository(db_session)
    bound = await repo.find_evaluation_for_script(uuid.UUID(project_id), s3.id)
    assert bound is not None, "第 3 集应有绑定评估"

    # 3) active context + 第 3 集修订 Turn → 确认 → revise_script Run
    revision_turn = await client.post(
        f"/api/v1/projects/{project_id}/agent/turns",
        json={
            "content": "修改第 3 集剧本，增加主角与教练的正面冲突",
            "idempotency_key": "e2e-2",
            "active_context": {
                "artifact_id": str(s3.id),
                "artifact_type": "script_draft",
                "episode_number": 3,
                "version": s3.version,
            },
        },
    )
    assert revision_turn.status_code == 200, revision_turn.text
    assert revision_turn.json()["status"] == "action_proposed", revision_turn.json()
    revise_action_id = revision_turn.json()["action_id"]

    confirm2 = await client.post(f"/api/v1/agent/actions/{revise_action_id}/confirm")
    assert confirm2.status_code == 202, confirm2.text
    assert confirm2.json()["run"]["action"] == "revise_script"
    revise_run_id = uuid.UUID(confirm2.json()["run"]["run_id"])

    revision = await _run_to_terminal(db_session, revise_run_id, "revise_script")
    assert revision.status in ("completed", "needs_review"), (
        revision.status,
        revision.error_code,
        (revision.error_detail or "")[:300],
    )
