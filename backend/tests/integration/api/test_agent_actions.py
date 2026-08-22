"""J-04 Agent Action API 集成测试。

覆盖确认流的过期检测(stale)、并发单活跃 Run 保护、重复确认幂等与 reject 语义。
种子数据直接经 ORM 写入,不经过 Turn API。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.agent_action import AgentAction
from app.db.models.agent_turn import AgentTurn
from app.db.models.artifact import Artifact
from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.db.models.project import Project
from app.db.models.workflow_run import WorkflowRun
from app.domain.agent_command import (
    ActionStep,
    ActionTarget,
    AgentActionPlan,
    ArtifactSnapshot,
    CreateScriptCommand,
    EvaluateCommand,
    ExplainCommand,
    ReviseScriptCommand,
)

_CHECKSUM_V1 = "a" * 64
_CHECKSUM_V2 = "b" * 64


# ========================================================================
# Fixtures / 种子
# ========================================================================


@pytest_asyncio.fixture
async def db_session(test_engine: Any) -> AsyncGenerator[AsyncSession, None]:
    """基于 conftest test_engine 的独立数据库会话。"""
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest.fixture
def no_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """禁用 Dispatcher 唤醒,保证确认后的 Run 停在 queued,断言确定。"""
    from app.application import agent_command_service as svc_module

    monkeypatch.setattr(svc_module, "schedule_worker", lambda *args: None)


@pytest_asyncio.fixture
async def agent_client(app: Any) -> AsyncGenerator[AsyncClient, None]:
    """默认依赖(测试环境 FakeLLM)的 API 客户端;Action 用例不需要 Planner。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _create_script_plan(user_input: str = "足球少年逆袭") -> AgentActionPlan:
    return AgentActionPlan(
        goal=f"根据用户输入创建短剧剧本：{user_input}",
        intent="create_script",
        command=CreateScriptCommand(user_input=user_input, outline_count=10, script_count=3),
        target=ActionTarget(target_type="project"),
        steps=[
            ActionStep(step_id="run", title="创建剧本", description="执行创作工作流并生成剧本"),
        ],
    )


def _evaluate_plan(episode: int | None = None) -> AgentActionPlan:
    scope = "episode" if episode is not None else "project"
    return AgentActionPlan(
        goal="评估最新有效剧本并产出报告",
        intent="evaluate",
        command=EvaluateCommand(scope=scope, episode_number=episode),  # type: ignore[arg-type]
        target=ActionTarget(target_type="evaluation", episode_number=episode),
        steps=[
            ActionStep(step_id="evaluate", title="逐集评估", description="对最新剧本执行评分"),
        ],
    )


def _explain_plan() -> AgentActionPlan:
    return AgentActionPlan(
        goal="解释当前项目状态",
        intent="explain",
        command=ExplainCommand(target=ActionTarget(target_type="project")),
        target=ActionTarget(target_type="project"),
        steps=[
            ActionStep(step_id="explain", title="解释", description="生成只读解释"),
        ],
    )


def _revise_script_plan(episode: int, source_id: uuid.UUID) -> AgentActionPlan:
    return AgentActionPlan(
        goal=f"按用户要求修订第 {episode} 集剧本并重评",
        intent="revise_script",
        command=ReviseScriptCommand(
            source_script_id=source_id,
            episode_number=episode,
            constraints=["增加主角与教练的正面冲突"],
        ),
        target=ActionTarget(target_type="script", episode_number=episode),
        steps=[
            ActionStep(step_id="revise", title="修订", description="生成候选新稿并重评"),
        ],
    )


async def _seed_action(
    db_session: AsyncSession,
    *,
    plan: AgentActionPlan,
    snapshots: list[ArtifactSnapshot] | None = None,
    project_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """直接种下 action_proposed Turn + proposed Action(可选复用已有 project)。"""
    if project_id is None:
        project = Project(title="Action 测试", target_episode_count=10)
        db_session.add(project)
        await db_session.flush()
        project_id = project.id
    conversation = Conversation(project_id=project_id, title="种子会话")
    db_session.add(conversation)
    await db_session.flush()
    message = Message(
        conversation_id=conversation.id,
        role="user",
        content="seed",
        kind="text",
        message_metadata={},
        sequence=1,
    )
    db_session.add(message)
    await db_session.flush()
    turn = AgentTurn(
        project_id=project_id,
        conversation_id=conversation.id,
        user_message_id=message.id,
        idempotency_key=f"seed-{uuid.uuid4().hex}",
        request_hash="0" * 64,
        status="action_proposed",
        turn_type="plan",
    )
    db_session.add(turn)
    await db_session.flush()
    action = AgentAction(
        project_id=project_id,
        conversation_id=conversation.id,
        agent_turn_id=turn.id,
        replan_depth=0,
        intent=plan.intent,
        status="proposed",
        requires_confirmation=True,
        plan=plan.model_dump(mode="json"),
        source_artifact_ids=[s.model_dump(mode="json") for s in (snapshots or [])],
    )
    db_session.add(action)
    await db_session.commit()
    return project_id, action.id


async def _seed_script(
    db_session: AsyncSession,
    project_id: uuid.UUID,
    *,
    episode: int = 1,
    version: int = 1,
    checksum: str = _CHECKSUM_V1,
) -> Artifact:
    """种一版 valid script_draft Artifact。"""
    artifact = Artifact(
        project_id=project_id,
        type="script_draft",
        version=version,
        episode_number=episode,
        content={"scenes": []},
        status="valid",
        checksum=checksum,
    )
    db_session.add(artifact)
    await db_session.commit()
    return artifact


async def _active_run_count(db_session: AsyncSession) -> int:
    result = await db_session.execute(
        select(func.count()).select_from(WorkflowRun).where(
            WorkflowRun.status.in_(("queued", "running"))
        )
    )
    return int(result.scalar_one())


async def _total_run_count(db_session: AsyncSession) -> int:
    result = await db_session.execute(select(func.count()).select_from(WorkflowRun))
    return int(result.scalar_one())


# ========================================================================
# 确认流
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_happy_path_creates_run_and_queues_action(
    agent_client: AsyncClient,
    db_session: AsyncSession,
    no_worker: None,
) -> None:
    """确认 proposed Action 创建 create_script Run 并把 Action 置为 queued。"""
    _project_id, action_id = await _seed_action(db_session, plan=_create_script_plan())

    resp = await agent_client.post(f"/api/v1/agent/actions/{action_id}/confirm")
    assert resp.status_code == 202
    body = resp.json()
    assert body["action"]["status"] == "queued"
    assert body["action"]["run_id"]
    assert body["run"]["action"] == "create_script"
    assert body["run"]["run_id"] == body["action"]["run_id"]

    run_resp = await agent_client.get(f"/api/v1/runs/{body['run']['run_id']}")
    assert run_resp.status_code == 200
    options = run_resp.json()["config_snapshot"]["options"]
    assert options["user_input"] == "足球少年逆袭"
    assert options["outline_count"] == 10
    assert options["script_count"] == 3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_confirm_returns_original_run(
    agent_client: AsyncClient,
    db_session: AsyncSession,
    no_worker: None,
) -> None:
    """重复确认返回原 Run,不创建第二个 Run。"""
    _project_id, action_id = await _seed_action(db_session, plan=_create_script_plan())

    first = await agent_client.post(f"/api/v1/agent/actions/{action_id}/confirm")
    second = await agent_client.post(f"/api/v1/agent/actions/{action_id}/confirm")
    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["run"]["run_id"] == first.json()["run"]["run_id"]
    assert await _total_run_count(db_session) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stale_plan_is_blocked_without_creating_run(
    agent_client: AsyncClient,
    db_session: AsyncSession,
    no_worker: None,
) -> None:
    """来源 Artifact 已更新时确认返回 409 ACTION_STALE,Action→stale,不创建 Run。"""
    project = Project(title="stale 测试", target_episode_count=10)
    db_session.add(project)
    await db_session.flush()
    artifact_v1 = await _seed_script(db_session, project.id, episode=1, version=1)
    _project_id, action_id = await _seed_action(
        db_session,
        plan=_evaluate_plan(episode=1),
        project_id=project.id,
        snapshots=[
            ArtifactSnapshot(
                artifact_id=artifact_v1.id,
                artifact_type="script_draft",
                episode_number=1,
                version=1,
                checksum=_CHECKSUM_V1,
            )
        ],
    )
    # 快照之后同集出现更高版本 → 确认时过期检测命中。
    await _seed_script(db_session, project.id, episode=1, version=2, checksum=_CHECKSUM_V2)

    resp = await agent_client.post(f"/api/v1/agent/actions/{action_id}/confirm")
    assert resp.status_code == 409
    assert resp.json()["code"] == "ACTION_STALE"
    assert await _total_run_count(db_session) == 0

    detail = await agent_client.get(f"/api/v1/agent/actions/{action_id}")
    assert detail.json()["status"] == "stale"

    again = await agent_client.post(f"/api/v1/agent/actions/{action_id}/confirm")
    assert again.status_code == 409


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_actions_cannot_create_two_active_runs(
    agent_client: AsyncClient,
    db_session: AsyncSession,
    no_worker: None,
) -> None:
    """同一项目两个 proposed Action 并发确认,只允许一个活跃 Run。"""
    project_id, action1 = await _seed_action(db_session, plan=_create_script_plan("请求一"))
    _project2, action2 = await _seed_action(
        db_session, plan=_create_script_plan("请求二"), project_id=project_id
    )

    resp1, resp2 = await asyncio.gather(
        agent_client.post(f"/api/v1/agent/actions/{action1}/confirm"),
        agent_client.post(f"/api/v1/agent/actions/{action2}/confirm"),
    )
    statuses = sorted([resp1.status_code, resp2.status_code])
    assert statuses == [202, 409]

    loser = resp1 if resp1.status_code == 409 else resp2
    assert loser.json()["code"] == "PROJECT_HAS_ACTIVE_RUN"

    assert await _active_run_count(db_session) == 1

    winner_id = action1 if resp1.status_code == 202 else action2
    loser_id = action2 if winner_id == action1 else action1
    winner = await agent_client.get(f"/api/v1/agent/actions/{winner_id}")
    assert winner.json()["status"] == "queued"
    assert winner.json()["run_id"]
    looser_detail = await agent_client.get(f"/api/v1/agent/actions/{loser_id}")
    assert looser_detail.json()["status"] == "proposed"
    assert looser_detail.json()["run_id"] is None


# ========================================================================
# Reject / 守卫 / 查询
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reject_transitions_proposed_to_rejected(
    agent_client: AsyncClient, db_session: AsyncSession
) -> None:
    """reject 仅允许 proposed→rejected;重复 reject 返回 409。"""
    _project_id, action_id = await _seed_action(db_session, plan=_create_script_plan())

    first = await agent_client.post(f"/api/v1/agent/actions/{action_id}/reject")
    assert first.status_code == 200
    assert first.json()["status"] == "rejected"

    second = await agent_client.post(f"/api/v1/agent/actions/{action_id}/reject")
    assert second.status_code == 409
    assert await _total_run_count(db_session) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_rejected_action_returns_409(
    agent_client: AsyncClient, db_session: AsyncSession, no_worker: None
) -> None:
    """rejected 之后不可再确认。"""
    _project_id, action_id = await _seed_action(db_session, plan=_create_script_plan())
    await agent_client.post(f"/api/v1/agent/actions/{action_id}/reject")

    resp = await agent_client.post(f"/api/v1/agent/actions/{action_id}/confirm")
    assert resp.status_code == 409
    assert await _total_run_count(db_session) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_unsupported_intent_returns_400(
    agent_client: AsyncClient, db_session: AsyncSession, no_worker: None
) -> None:
    """explain intent 不映射 Run action,确认返回 400 UNSUPPORTED_AGENT_INTENT。"""
    _project_id, action_id = await _seed_action(db_session, plan=_explain_plan())

    resp = await agent_client.post(f"/api/v1/agent/actions/{action_id}/confirm")
    assert resp.status_code == 400
    assert resp.json()["code"] == "UNSUPPORTED_AGENT_INTENT"
    assert await _total_run_count(db_session) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_revise_script_creates_run_with_source_snapshot(
    agent_client: AsyncClient,
    db_session: AsyncSession,
    no_worker: None,
) -> None:
    """确认 revise_script 计划 → Run action=revise_script，config 携带服务端解析的目标与约束。"""
    project = Project(title="修订测试", target_episode_count=10)
    db_session.add(project)
    await db_session.flush()
    script = await _seed_script(db_session, project.id, episode=2, version=1)
    _project_id, action_id = await _seed_action(
        db_session,
        plan=_revise_script_plan(2, script.id),
        project_id=project.id,
        snapshots=[
            ArtifactSnapshot(
                artifact_id=script.id,
                artifact_type="script_draft",
                episode_number=2,
                version=1,
                checksum=_CHECKSUM_V1,
            )
        ],
    )

    resp = await agent_client.post(f"/api/v1/agent/actions/{action_id}/confirm")
    assert resp.status_code == 202
    body = resp.json()
    assert body["run"]["action"] == "revise_script"

    run_resp = await agent_client.get(f"/api/v1/runs/{body['run']['run_id']}")
    assert run_resp.status_code == 200
    options = run_resp.json()["config_snapshot"]["options"]
    assert options["source_script_artifact_id"] == str(script.id)
    assert options["episode_number"] == 2
    assert options["user_constraints"] == ["增加主角与教练的正面冲突"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_action_unknown_returns_404_agent_action_not_found(
    agent_client: AsyncClient,
) -> None:
    resp = await agent_client.get(f"/api/v1/agent/actions/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "AGENT_ACTION_NOT_FOUND"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_action_returns_plan_and_run_reference(
    agent_client: AsyncClient,
    db_session: AsyncSession,
    no_worker: None,
) -> None:
    """确认后 GET Action 返回完整 plan 与 run 引用。"""
    _project_id, action_id = await _seed_action(db_session, plan=_create_script_plan())
    await agent_client.post(f"/api/v1/agent/actions/{action_id}/confirm")

    resp = await agent_client.get(f"/api/v1/agent/actions/{action_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"]["intent"] == "create_script"
    assert body["plan"]["command"]["user_input"] == "足球少年逆袭"
    assert body["run_id"]
