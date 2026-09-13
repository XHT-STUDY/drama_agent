"""L-3/L-4 确认门续跑 API 测试（W1-01 幂等收据版）。

- 非 stage_gate 的 needs_review Run → 409 不可续；
- staged Run 停门后 continue → queued、options 无 stop_after、
  state_summary 无 stage_gate 且大纲刷新为最新 valid；
- 每次合法接受：stage_generation +1、attempt_count 置零（批次推进不耗
  故障重试预算）、上一批的 write/evaluate 完成标记清除；
- 幂等收据：同键同参数重放返回接受快照（不新增阶段）；同键不同参数
  409 IDEMPOTENCY_KEY_REUSED；旧世代请求 409 RUN_STAGE_STALE；
  裸请求（缺世代/幂等键）422；
- 缺省 batch 续跑把 script_count 恢复为项目目标（不沿用上批终点）。
"""

from __future__ import annotations

import uuid
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


def _body(
    expected: int = 0, key: str | None = None, batch: int | None = None
) -> dict[str, Any]:
    """构造合法 continue 请求体。"""
    payload: dict[str, Any] = {
        "expected_stage_generation": expected,
        "idempotency_key": key or f"test-{uuid.uuid4()}",
    }
    if batch is not None:
        payload["batch_size"] = batch
    return payload


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
    """continue → queued、门字段剥离、大纲刷新、世代推进且 attempt 置零。"""
    from app.api.v1 import runs as runs_module

    monkeypatch.setattr(runs_module, "schedule_worker", lambda *a: None)
    latest_outline_id, run = await _seed_gated_run(db_session, with_newer_outline=True)
    # 模拟此前有一次故障领取（W1-01 前续跑会继承 attempt 直至耗尽）
    run.attempt_count = 2
    await db_session.commit()

    resp = await client.post(f"/api/v1/runs/{run.id}/continue", json=_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["stage_generation"] == 1
    options = body["config_snapshot"]["options"]
    assert "stop_after" not in options

    await db_session.refresh(run)
    assert "stage_gate" not in (run.state_summary or {})
    assert "stop_after" not in (run.state_summary or {})
    assert (run.state_summary or {})["outline_set_artifact_id"] == latest_outline_id
    assert run.stage_generation == 1
    assert run.attempt_count == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_clears_batch_completed_nodes(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """scripts 门续跑必须清除 write/evaluate 完成标记——不清除则第二批
    因节点早退守卫空转到 completed（一个字都不写）。"""
    from app.api.v1 import runs as runs_module

    monkeypatch.setattr(runs_module, "schedule_worker", lambda *a: None)
    run = await _seed_scripts_gate(db_session, written=3, target=10)

    resp = await client.post(f"/api/v1/runs/{run.id}/continue", json=_body(batch=5))
    assert resp.status_code == 200
    await db_session.refresh(run)
    nodes = (run.state_summary or {}).get("completed_nodes") or []
    assert "write_episodes" not in nodes
    assert "evaluate_episodes" not in nodes
    # 非批次节点保留（SB/大纲不重算）
    assert "story_bible" in nodes
    assert "outline" in nodes


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

    resp = await client.post(f"/api/v1/runs/{run.id}/continue", json=_body())
    assert resp.status_code == 409
    assert resp.json()["code"] == "RUN_NOT_RETRYABLE"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_replay_returns_receipt(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同键同参数重放 → 200 返回接受快照，不新增阶段（Run 已 queued）。"""
    from app.api.v1 import runs as runs_module

    monkeypatch.setattr(runs_module, "schedule_worker", lambda *a: None)
    _outline_id, run = await _seed_gated_run(db_session)

    key = f"replay-{uuid.uuid4()}"
    first = await client.post(
        f"/api/v1/runs/{run.id}/continue", json=_body(key=key)
    )
    assert first.status_code == 200
    assert first.json()["stage_generation"] == 1

    second = await client.post(
        f"/api/v1/runs/{run.id}/continue", json=_body(key=key)
    )
    assert second.status_code == 200
    assert second.json() == first.json()

    await db_session.refresh(run)
    assert run.stage_generation == 1  # 重放不推进世代


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_key_reuse_different_params(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同键不同参数 → 409 IDEMPOTENCY_KEY_REUSED。"""
    from app.api.v1 import runs as runs_module

    monkeypatch.setattr(runs_module, "schedule_worker", lambda *a: None)
    _outline_id, run = await _seed_gated_run(db_session)

    key = f"reuse-{uuid.uuid4()}"
    first = await client.post(
        f"/api/v1/runs/{run.id}/continue", json=_body(key=key)
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/v1/runs/{run.id}/continue", json=_body(key=key, batch=3)
    )
    assert second.status_code == 409
    assert second.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_twice_different_keys_conflicts(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """续跑入队后不同键 continue → 409 RUN_ALREADY_ACTIVE。"""
    from app.api.v1 import runs as runs_module

    monkeypatch.setattr(runs_module, "schedule_worker", lambda *a: None)
    _outline_id, run = await _seed_gated_run(db_session)

    first = await client.post(f"/api/v1/runs/{run.id}/continue", json=_body())
    assert first.status_code == 200
    second = await client.post(f"/api/v1/runs/{run.id}/continue", json=_body())
    assert second.status_code == 409
    assert second.json()["code"] == "RUN_ALREADY_ACTIVE"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_stale_generation_rejected(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧世代请求在新门重放 → 409 RUN_STAGE_STALE，不多写一批。"""
    from app.api.v1 import runs as runs_module

    monkeypatch.setattr(runs_module, "schedule_worker", lambda *a: None)
    _outline_id, run = await _seed_gated_run(db_session)

    first = await client.post(f"/api/v1/runs/{run.id}/continue", json=_body())
    assert first.status_code == 200

    # 模拟批次完成：Run 再次停在 scripts 门（世代已是 1）——显式 UPDATE
    # 绕开 ORM 旧实例（API 事务已推进同一行）
    from sqlalchemy import update

    summary = dict(run.state_summary or {})
    summary["stage_gate"] = "scripts"
    summary["script_artifact_ids"] = {"1": str(uuid.uuid4())}
    summary.pop("stop_after", None)
    await db_session.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == run.id)
        .values(status="needs_review", state_summary=summary)
    )
    await db_session.commit()
    await db_session.refresh(run)

    # 旧门上的请求（expected=0，不同键）在新门重放 → 世代不符
    stale = await client.post(f"/api/v1/runs/{run.id}/continue", json=_body())
    assert stale.status_code == 409
    assert stale.json()["code"] == "RUN_STAGE_STALE"

    # 当前世代（expected=1）可正常接受
    fresh = await client.post(
        f"/api/v1/runs/{run.id}/continue", json=_body(expected=1, batch=1)
    )
    assert fresh.status_code == 200
    assert fresh.json()["stage_generation"] == 2


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        None,  # 裸请求
        {"batch_size": 5},  # 缺世代与幂等键
        {"expected_stage_generation": 0},  # 缺幂等键
        {"idempotency_key": "short"},  # 键过短
    ],
)
async def test_continue_bare_or_invalid_request_422(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any] | None,
) -> None:
    """旧客户端裸请求缺阶段信息 → 422（版本化升级，防额外生成），
    且响应带"刷新工作台"指引而非无上下文的通用校验报错。"""
    from app.api.v1 import runs as runs_module

    monkeypatch.setattr(runs_module, "schedule_worker", lambda *a: None)
    _outline_id, run = await _seed_gated_run(db_session)

    resp = await client.post(
        f"/api/v1/runs/{run.id}/continue",
        json=payload,
    )
    assert resp.status_code == 422
    # 缺阶段信息的场景给出刷新指引；字段格式错误（键过短）走通用校验
    missing_stage = payload is None or "expected_stage_generation" not in (payload or {})
    format_rejected = (
        payload is not None and "idempotency_key" in payload and len(payload["idempotency_key"]) < 8
    )
    if missing_stage and not format_rejected:
        assert "刷新工作台" in resp.json()["detail"]


# ========================================================================
# L-4：批模式 continue
# ========================================================================


async def _seed_scripts_gate(
    db: AsyncSession,
    *,
    written: int = 3,
    target: int = 10,
    script_count: int | None = None,
) -> WorkflowRun:
    """播种停在 scripts 门的 Run（已写 written 集，目标 target）。"""
    project = Project(title="L-4 批门", target_episode_count=target)
    db.add(project)
    await db.flush()
    scripts = {
        str(ep): str(uuid.uuid4()) for ep in range(1, written + 1)
    }
    run = WorkflowRun(
        project_id=project.id,
        action="create_script",
        status="needs_review",
        config_snapshot={"options": {
            "user_input": "x", "outline_count": target,
            "script_count": script_count if script_count is not None else target,
            "stop_after": "scripts",
        }},
        state_summary={
            "stage_gate": "scripts",
            "stop_after": "scripts",
            "target_episode_count": target,
            "script_artifact_ids": scripts,
            "evaluation_artifact_ids": {
                str(ep): str(uuid.uuid4()) for ep in range(1, written + 1)
            },
            "completed_nodes": [
                "normalize", "retrieve", "story_bible", "outline",
                "write_episodes", "evaluate_episodes",
            ],
        },
    )
    db.add(run)
    await db.commit()
    return run


@pytest.mark.integration
@pytest.mark.asyncio
async def test_batch_continue_sets_end_and_keeps_gate(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """scripts 门 batch=5：终点 = 3+5=8，stop_after=scripts 保留（批模式）。"""
    from app.api.v1 import runs as runs_module

    monkeypatch.setattr(runs_module, "schedule_worker", lambda *a: None)
    run = await _seed_scripts_gate(db_session, written=3, target=10)

    resp = await client.post(
        f"/api/v1/runs/{run.id}/continue", json=_body(batch=5)
    )
    assert resp.status_code == 200
    options = resp.json()["config_snapshot"]["options"]
    assert options["script_count"] == 8
    assert options["stop_after"] == "scripts"

    await db_session.refresh(run)
    assert (run.state_summary or {})["stop_after"] == "scripts"
    assert "stage_gate" not in (run.state_summary or {})


@pytest.mark.integration
@pytest.mark.asyncio
async def test_batch_continue_caps_at_target(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """batch 超过剩余（3+50 > 10）→ 终点封顶 target=10。"""
    from app.api.v1 import runs as runs_module

    monkeypatch.setattr(runs_module, "schedule_worker", lambda *a: None)
    run = await _seed_scripts_gate(db_session, written=3, target=10)

    resp = await client.post(
        f"/api/v1/runs/{run.id}/continue", json=_body(batch=50)
    )
    assert resp.status_code == 200
    assert resp.json()["config_snapshot"]["options"]["script_count"] == 10


@pytest.mark.integration
@pytest.mark.asyncio
async def test_no_batch_restores_target_script_count(
    client: AsyncSession, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺省 batch：写剩余全部——script_count 必须恢复项目目标 10。

    回归：上一批 continue 留下的终点 8 若不清除，write_episodes 只写到
    旧终点，"写完剩余全部"实际一个字都不写（W1-01 修复）。
    """
    from app.api.v1 import runs as runs_module

    monkeypatch.setattr(runs_module, "schedule_worker", lambda *a: None)
    # 模拟上一批留下的 script_count=8 终点
    run = await _seed_scripts_gate(
        db_session, written=3, target=10, script_count=8
    )

    resp = await client.post(f"/api/v1/runs/{run.id}/continue", json=_body())
    assert resp.status_code == 200
    options = resp.json()["config_snapshot"]["options"]
    assert "stop_after" not in options
    assert options["script_count"] == 10
    assert resp.json()["stage_gate"] is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_accepted_event_persisted(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接受收据落为 run.continue_accepted 事件（含键/哈希/世代/响应快照）。"""
    from sqlalchemy import select

    from app.api.v1 import runs as runs_module
    from app.db.models.workflow_event import WorkflowEvent

    monkeypatch.setattr(runs_module, "schedule_worker", lambda *a: None)
    _outline_id, run = await _seed_gated_run(db_session)

    key = f"receipt-{uuid.uuid4()}"
    resp = await client.post(f"/api/v1/runs/{run.id}/continue", json=_body(key=key))
    assert resp.status_code == 200

    result = await db_session.execute(
        select(WorkflowEvent)
        .where(
            WorkflowEvent.run_id == run.id,
            WorkflowEvent.type == "run.continue_accepted",
        )
        .order_by(WorkflowEvent.sequence.desc())
    )
    events = list(result.scalars().all())
    assert len(events) == 1
    payload = events[0].payload or {}
    assert payload["idempotency_key"] == key
    assert payload["expected_stage_generation"] == 0
    assert payload["accepted_stage_generation"] == 1
    assert payload["accepted_response"]["run_id"] == str(run.id)
    assert payload["accepted_response"]["status"] == "queued"
