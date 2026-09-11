"""对话短路（A）与 continue 意图（B）的 API 集成测试。

A：确认/续跑短语在 Planner 之前被确定性路由——命中时 Planner 不被调用、
   proposed Action 被确认或门上 Run 被续跑；未命中回落 Planner 原行为。
B：白名单含 continue 时，Planner 可产出 continue 计划；确认后复用既有
   Run（不新建），Run 终态回写指向 continue Action。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.base import BaseAgent
from app.application.agent_command_service import AgentCommandService
from app.core.config import Settings
from app.db.models.agent_action import AgentAction
from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.db.models.project import Project
from app.db.models.workflow_run import WorkflowRun
from app.domain.agent_command import (
    ActionStep,
    ActionTarget,
    AgentActionPlan,
    CreateScriptCommand,
)
from app.domain.agent_planner import (
    AgentPlannerOutput,
    PlannerStep,
    PlannerTarget,
)
from app.llm.fake import FakeLLM

# ========================================================================
# Fixtures / 替身
# ========================================================================


class _StubPlannerSkill:
    """可编程 Planner 替身：返回预设输出、记录调用与白名单入参。"""

    def __init__(self) -> None:
        self.output: AgentPlannerOutput | None = None
        self.calls = 0
        self.seen_available_intents: list[list[str]] = []

    async def execute(self, context: dict[str, Any]) -> AgentPlannerOutput:
        self.calls += 1
        self.seen_available_intents.append(list(context["input"].available_intents))
        assert self.output is not None, "短路命中时不应调用 Planner"
        return self.output


@pytest.fixture
def stub_skill() -> _StubPlannerSkill:
    return _StubPlannerSkill()


@pytest.fixture
def agent_service(stub_skill: _StubPlannerSkill) -> AgentCommandService:
    return AgentCommandService(
        settings=Settings(app_env="test"),
        planner_agent=BaseAgent(name="planner", llm=FakeLLM(seed=42)),
        planner_skill=stub_skill,
    )


@pytest_asyncio.fixture
async def agent_api(
    app: Any, agent_service: AgentCommandService
) -> AsyncGenerator[AsyncClient, None]:
    from app.api.dependencies import get_agent_command_service

    app.dependency_overrides[get_agent_command_service] = lambda: agent_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def db_session(test_engine: Any) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest.fixture
def no_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """禁用 Dispatcher 唤醒，保证续跑后的 Run 停在 queued，断言确定。"""
    from app.application import agent_command_service as svc_module

    monkeypatch.setattr(svc_module, "schedule_worker", lambda *args: None)


# ========================================================================
# 种子辅助
# ========================================================================


async def _create_project(async_client: AsyncClient, title: str = "短路测试") -> str:
    resp = await async_client.post("/api/v1/projects", json={"title": title})
    assert resp.status_code == 201
    return str(resp.json()["id"])


async def _post_turn(
    async_client: AsyncClient,
    project_id: str,
    content: str,
    *,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "content": content,
        "idempotency_key": f"key-{uuid.uuid4().hex}",
    }
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    resp = await async_client.post(
        f"/api/v1/projects/{project_id}/agent/turns", json=payload
    )
    assert resp.status_code == 200
    return resp.json()


def _create_script_plan() -> AgentActionPlan:
    return AgentActionPlan(
        goal="根据用户输入创建短剧剧本：足球少年逆袭",
        intent="create_script",
        command=CreateScriptCommand(user_input="足球少年逆袭", outline_count=10, script_count=3),
        target=ActionTarget(target_type="project"),
        steps=[ActionStep(step_id="run", title="创建剧本", description="执行创作工作流")],
    )


def _continue_planner_output(batch_size: int | None = None) -> AgentPlannerOutput:
    return AgentPlannerOutput(
        turn_type="plan",
        intent="continue",
        target=PlannerTarget(target_type="project"),
        steps=[PlannerStep(title="继续创作", description="从确认门恢复，继续写剧本")],
        expected_impact=["继续生成剧本并自动评估"],
        batch_size=batch_size,
    )


def _clarification_output() -> AgentPlannerOutput:
    return AgentPlannerOutput(
        turn_type="clarification",
        clarification_question="你想继续做什么？",
    )


async def _seed_proposed_action(
    db_session: AsyncSession, project_id: str
) -> AgentAction:
    """种下 action_proposed Turn + proposed Action（走真实 Turn API 太绕）。"""
    conversation = Conversation(project_id=uuid.UUID(project_id), title="种子会话")
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
    from app.db.models.agent_turn import AgentTurn

    turn = AgentTurn(
        project_id=uuid.UUID(project_id),
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
        project_id=uuid.UUID(project_id),
        conversation_id=conversation.id,
        agent_turn_id=turn.id,
        replan_depth=0,
        intent="create_script",
        status="proposed",
        requires_confirmation=True,
        plan=_create_script_plan().model_dump(mode="json"),
        source_artifact_ids=[],
    )
    db_session.add(action)
    await db_session.commit()
    return action


async def _seed_scripts_gate_run(
    db_session: AsyncSession, *, written: int = 3, target: int = 10
) -> WorkflowRun:
    """播种停在 stage_gate=scripts 的 needs_review Run。"""
    project = Project(title="批门项目", target_episode_count=target)
    db_session.add(project)
    await db_session.flush()
    run = WorkflowRun(
        project_id=project.id,
        action="create_script",
        status="needs_review",
        config_snapshot={"options": {
            "user_input": "x", "outline_count": target, "script_count": target,
            "stop_after": "scripts",
        }},
        state_summary={
            "stage_gate": "scripts",
            "stop_after": "scripts",
            "target_episode_count": target,
            "script_artifact_ids": {str(ep): str(uuid.uuid4()) for ep in range(1, written + 1)},
            "completed_nodes": ["normalize", "retrieve", "story_bible", "outline"],
        },
    )
    db_session.add(run)
    await db_session.commit()
    return run


async def _get_run(db_session: AsyncSession, run_id: Any) -> WorkflowRun:
    refreshed = await db_session.get(WorkflowRun, run_id)
    assert refreshed is not None
    # 绕过 identity map 缓存：API 侧会话的变更需显式刷新才可见
    await db_session.refresh(refreshed)
    return refreshed


async def _get_action(db_session: AsyncSession, action_id: Any) -> AgentAction:
    refreshed = await db_session.get(AgentAction, action_id)
    assert refreshed is not None
    await db_session.refresh(refreshed)
    return refreshed


# ========================================================================
# A：确认短语短路
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_phrase_confirms_proposed_action(
    agent_api: AsyncClient,
    db_session: AsyncSession,
    stub_skill: _StubPlannerSkill,
    no_worker: None,
) -> None:
    """会话内有 proposed Action 时，"确认"直达 confirm，不调 Planner。"""
    action = await _seed_proposed_action(db_session, str(await _create_project(agent_api)))
    project_id = str(action.project_id)

    # 短路按会话查找 pending Action：必须显式传 Action 所属会话
    body = await _post_turn(
        agent_api, project_id, "确认", conversation_id=str(action.conversation_id)
    )

    assert body["status"] == "answered"
    assert body["turn_type"] == "answer"
    assert stub_skill.calls == 0  # Planner 未被调用
    refreshed = await _get_action(db_session, action.id)
    assert refreshed.run_id is not None
    run = await _get_run(db_session, refreshed.run_id)
    assert run.status == "queued"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_phrase_on_gated_run_continues(
    agent_api: AsyncClient,
    db_session: AsyncSession,
    stub_skill: _StubPlannerSkill,
    no_worker: None,
) -> None:
    """无 proposed Action 但有门上 Run 时，"确认"即续跑（对齐按钮语义）。"""
    run = await _seed_scripts_gate_run(db_session)
    project_id = str(run.project_id)

    body = await _post_turn(agent_api, project_id, "确认")

    assert body["status"] == "answered"
    assert stub_skill.calls == 0
    refreshed = await _get_run(db_session, run.id)
    assert refreshed.status == "queued"
    assert "stage_gate" not in (refreshed.state_summary or {})


@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_without_pending_falls_through_to_planner(
    agent_api: AsyncClient,
    db_session: AsyncSession,
    stub_skill: _StubPlannerSkill,
) -> None:
    """无任何 pending 目标时，"确认"回落 Planner（产出澄清）。"""
    stub_skill.output = _clarification_output()
    project_id = await _create_project(agent_api)

    body = await _post_turn(agent_api, project_id, "确认")

    assert body["status"] == "needs_input"
    assert body["turn_type"] == "clarification"
    assert stub_skill.calls == 1
    # 无门上 Run：白名单不含 continue
    assert "continue" not in stub_skill.seen_available_intents[0]


# ========================================================================
# A：续跑短语短路
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_phrase_resumes_gated_run(
    agent_api: AsyncClient,
    db_session: AsyncSession,
    stub_skill: _StubPlannerSkill,
    no_worker: None,
) -> None:
    """"继续"直达门上 Run 续跑，剥离 stage_gate。"""
    run = await _seed_scripts_gate_run(db_session)

    body = await _post_turn(agent_api, str(run.project_id), "继续")

    assert body["status"] == "answered"
    assert stub_skill.calls == 0
    refreshed = await _get_run(db_session, run.id)
    assert refreshed.status == "queued"
    assert "stage_gate" not in (refreshed.state_summary or {})
    assert "stop_after" not in (refreshed.config_snapshot or {})["options"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_phrase_with_batch_maps_batch_size(
    agent_api: AsyncClient,
    db_session: AsyncSession,
    stub_skill: _StubPlannerSkill,
    no_worker: None,
) -> None:
    """"写5集"→ 批模式：script_count = 已写 3 + 5，完成后再次停门。"""
    run = await _seed_scripts_gate_run(db_session, written=3, target=10)

    body = await _post_turn(agent_api, str(run.project_id), "写5集")

    assert body["status"] == "answered"
    assert stub_skill.calls == 0
    refreshed = await _get_run(db_session, run.id)
    assert refreshed.status == "queued"
    options = (refreshed.config_snapshot or {})["options"]
    assert options["script_count"] == 8
    assert options["stop_after"] == "scripts"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_without_gated_run_falls_through_to_planner(
    agent_api: AsyncClient,
    db_session: AsyncSession,
    stub_skill: _StubPlannerSkill,
) -> None:
    """无门上 Run 时，"继续"回落 Planner，不产生续跑副作用。"""
    stub_skill.output = _clarification_output()
    project_id = await _create_project(agent_api)

    body = await _post_turn(agent_api, project_id, "继续")

    assert body["status"] == "needs_input"
    assert stub_skill.calls == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_long_sentence_does_not_shortcut(
    agent_api: AsyncClient,
    db_session: AsyncSession,
    stub_skill: _StubPlannerSkill,
) -> None:
    """确认 + 附加请求的长句不短路：Action 保持 proposed，交给 Planner。"""
    stub_skill.output = _clarification_output()
    action = await _seed_proposed_action(db_session, str(await _create_project(agent_api)))

    body = await _post_turn(agent_api, str(action.project_id), "好的，不过我想把主角改成女生")

    assert body["status"] == "needs_input"
    assert stub_skill.calls == 1
    refreshed = await db_session.get(AgentAction, action.id)
    assert refreshed is not None and refreshed.run_id is None  # 未被确认


# ========================================================================
# B：continue 意图（自然语言续跑经 Planner）
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_intent_whitelist_and_plan_and_confirm(
    agent_api: AsyncClient,
    db_session: AsyncSession,
    stub_skill: _StubPlannerSkill,
    no_worker: None,
) -> None:
    """有门上 Run 时白名单注入 continue；计划确认后复用既有 Run。"""
    run = await _seed_scripts_gate_run(db_session, written=3, target=10)
    stub_skill.output = _continue_planner_output(batch_size=5)

    # "先写5集吧"带附加语气词,不命中短路正则,走 Planner
    body = await _post_turn(agent_api, str(run.project_id), "先写5集吧")

    assert body["status"] == "action_proposed"
    assert body["turn_type"] == "plan"
    assert stub_skill.calls == 1
    assert "continue" in stub_skill.seen_available_intents[0]
    action_id = body["action_id"]
    assert action_id is not None

    # 确认：不新建 Run，复用既有 Run 续跑
    resp = await agent_api.post(f"/api/v1/agent/actions/{action_id}/confirm")
    assert resp.status_code == 202
    payload = resp.json()
    assert payload["action"]["intent"] == "continue"
    assert payload["run"]["run_id"] == str(run.id)

    refreshed_run = await _get_run(db_session, run.id)
    assert refreshed_run.status == "queued"
    options = (refreshed_run.config_snapshot or {})["options"]
    assert options["script_count"] == 8  # 已写 3 + 本批 5
    # Run 终态回写指向 continue Action
    assert (refreshed_run.config_snapshot or {})["agent_action_id"] == action_id
    refreshed_action = await db_session.get(AgentAction, uuid.UUID(action_id))
    assert refreshed_action is not None
    assert refreshed_action.status == "queued"
    assert str(refreshed_action.run_id) == str(run.id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_intent_confirm_stale_when_run_left_gate(
    agent_api: AsyncClient,
    db_session: AsyncSession,
    stub_skill: _StubPlannerSkill,
    no_worker: None,
) -> None:
    """确认前 Run 已离开确认门 → 计划作废（stale）+ 409。"""
    run = await _seed_scripts_gate_run(db_session)
    stub_skill.output = _continue_planner_output()

    # 带附加说明的句子不命中短路正则,走 Planner 产出 continue 计划
    body = await _post_turn(agent_api, str(run.project_id), "可以了，把剩余的集数都写完")
    action_id = body["action_id"]
    assert action_id is not None

    # 规划后 Run 被旁路续跑（如 REST 端点）
    run.status = "queued"
    await db_session.commit()

    resp = await agent_api.post(f"/api/v1/agent/actions/{action_id}/confirm")
    assert resp.status_code == 409
    assert resp.json()["code"] == "RUN_NOT_RETRYABLE"
    refreshed_action = await db_session.get(AgentAction, uuid.UUID(action_id))
    assert refreshed_action is not None
    assert refreshed_action.status == "stale"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_intent_without_gated_run_fails_turn(
    agent_api: AsyncClient,
    db_session: AsyncSession,
    stub_skill: _StubPlannerSkill,
) -> None:
    """白名单漂移防御：无门上 Run 却产出 continue 计划 → Turn failed。"""
    stub_skill.output = _continue_planner_output()
    project_id = await _create_project(agent_api)

    body = await _post_turn(agent_api, project_id, "继续写")

    assert body["status"] == "failed"
    assert body["error_code"] == "GATED_RUN_NOT_FOUND"
    actions = (
        await db_session.execute(
            select(AgentAction).where(AgentAction.project_id == uuid.UUID(project_id))
        )
    ).scalars().all()
    assert actions == []  # 失败 Turn 不落 AgentAction


@pytest.mark.integration
@pytest.mark.asyncio
async def test_explain_turn_whitelist_excludes_continue(
    agent_api: AsyncClient,
    db_session: AsyncSession,
    stub_skill: _StubPlannerSkill,
) -> None:
    """无门上 Run 的普通对话白名单保持基础五意图（回归保护）。"""
    stub_skill.output = _clarification_output()
    project_id = await _create_project(agent_api)

    await _post_turn(agent_api, project_id, "这个项目是关于什么的？")

    assert stub_skill.calls == 1
    assert stub_skill.seen_available_intents[0] == [
        "create_script", "explain", "evaluate", "revise_script", "revise_outline",
    ]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_phrase_while_run_active_answers_executing(
    agent_api: AsyncClient,
    db_session: AsyncSession,
    stub_skill: _StubPlannerSkill,
    no_worker: None,
) -> None:
    """任务执行中（queued/running）发"继续"→ 如实回答执行中，不误导、不调 Planner。"""
    run = await _seed_scripts_gate_run(db_session)
    project_id = str(run.project_id)

    # 第一次"继续"：门上 → 正常续跑
    body = await _post_turn(agent_api, project_id, "继续")
    assert body["status"] == "answered"
    assert "继续创作剧本" in (await _assistant_texts(db_session, str(run.project_id)))[-1]

    # 第二次"继续"：Run 已 queued → 答复"执行中"，不得回落 Planner
    body2 = await _post_turn(agent_api, project_id, "继续")
    assert body2["status"] == "answered"
    assert stub_skill.calls == 0
    assert "正在执行中" in (await _assistant_texts(db_session, str(run.project_id)))[-1]


async def _assistant_texts(db_session: AsyncSession, project_id: str) -> list[str]:
    """取项目全部会话的 assistant text 消息内容（按 sequence 排序）。"""
    from sqlalchemy import select

    from app.db.models.conversation import Conversation
    from app.db.models.message import Message

    rows = (await db_session.execute(
        select(Message.content, Message.role, Message.kind)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Conversation.project_id == uuid.UUID(project_id))
        .order_by(Message.sequence)
    )).all()
    return [content for content, role, kind in rows if role == "assistant" and kind == "text"]


# ========================================================================
# E：失败感知——续跑/确认无目标时如实告知；「重试」直达 retry（P1）
# ========================================================================


async def _seed_failed_run(
    db_session: AsyncSession, *, error_code: str = "LLM_OUTPUT_TRUNCATED"
) -> WorkflowRun:
    """播种一个 failed Run（2026-09-07 事故形态：续跑后写剧本被截断）。"""
    project = Project(title="失败项目", target_episode_count=2)
    db_session.add(project)
    await db_session.flush()
    run = WorkflowRun(
        project_id=project.id, action="create_script", status="failed",
        error_code=error_code,
        error_detail="Episode Writer LLM 调用失败: output_truncated",
    )
    db_session.add(run)
    await db_session.commit()
    return run


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_with_latest_failure_answers_failure(
    agent_api: AsyncClient,
    db_session: AsyncSession,
    stub_skill: _StubPlannerSkill,
    no_worker: None,
) -> None:
    """最新动态是失败时发"继续"→ 如实告知失败原因与重试入口，不回落 Planner。"""
    run = await _seed_failed_run(db_session)
    project_id = str(run.project_id)

    body = await _post_turn(agent_api, project_id, "继续")

    assert body["status"] == "answered"
    assert body["turn_type"] == "answer"
    assert stub_skill.calls == 0, "用户意图已明确，回落 Planner 只会得到误导性澄清"
    texts = await _assistant_texts(db_session, project_id)
    assert "失败" in texts[-1]
    assert "重试" in texts[-1]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retry_phrase_requeues_failed_run(
    agent_api: AsyncClient,
    db_session: AsyncSession,
    stub_skill: _StubPlannerSkill,
    no_worker: None,
) -> None:
    """"重试"→ 最新 failed Run 清错误回队列（I-01 retry 语义），不经 Planner。"""
    run = await _seed_failed_run(db_session)
    project_id = str(run.project_id)

    body = await _post_turn(agent_api, project_id, "重试")

    assert body["status"] == "answered"
    assert stub_skill.calls == 0
    refreshed = await _get_run(db_session, run.id)
    assert refreshed.status == "queued"
    assert refreshed.error_code is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retry_phrase_without_failed_run_answers_deterministically(
    agent_api: AsyncClient,
    db_session: AsyncSession,
    stub_skill: _StubPlannerSkill,
    no_worker: None,
) -> None:
    """没有 failed Run 时发"重试"→ 确定性答复，不调 Planner 也不捏造澄清。"""
    project_id = await _create_project(agent_api, "无失败项目")

    body = await _post_turn(agent_api, project_id, "重试")

    assert body["status"] == "answered"
    assert stub_skill.calls == 0
    texts = await _assistant_texts(db_session, project_id)
    assert "没有失败的任务" in texts[-1]


# ========================================================================
# F：耗尽 Run 的诚实交互——「重试」拒绝死循环（P0' 配套）
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retry_phrase_on_exhausted_run_refuses_honestly(
    agent_api: AsyncClient,
    db_session: AsyncSession,
    stub_skill: _StubPlannerSkill,
    no_worker: None,
) -> None:
    """恢复预算耗尽的 Run：「重试」确定性拒绝并指向重新发起，不回队列。"""
    run = await _seed_failed_run(
        db_session, error_code="WORKFLOW_RECOVERY_EXHAUSTED"
    )
    project_id = str(run.project_id)

    body = await _post_turn(agent_api, project_id, "重试")

    assert body["status"] == "answered"
    assert stub_skill.calls == 0
    refreshed = await _get_run(db_session, run.id)
    assert refreshed.status == "failed", "耗尽 Run 不得被重新排队（会立刻再耗尽）"
    assert refreshed.error_code == "WORKFLOW_RECOVERY_EXHAUSTED"
    texts = await _assistant_texts(db_session, project_id)
    assert "重试次数" in texts[-1]
    assert "重新发起" in texts[-1]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_with_exhausted_failure_suggests_restart(
    agent_api: AsyncClient,
    db_session: AsyncSession,
    stub_skill: _StubPlannerSkill,
    no_worker: None,
) -> None:
    """耗尽后发「继续」：失败答复指向重新发起，而不是教用户去重试。"""
    run = await _seed_failed_run(
        db_session, error_code="WORKFLOW_RECOVERY_EXHAUSTED"
    )
    project_id = str(run.project_id)

    body = await _post_turn(agent_api, project_id, "继续")

    assert body["status"] == "answered"
    assert stub_skill.calls == 0
    texts = await _assistant_texts(db_session, project_id)
    assert "重新发起" in texts[-1]
    assert "「重试」" not in texts[-1], "耗尽场景不得再推荐必然失败的重试"
