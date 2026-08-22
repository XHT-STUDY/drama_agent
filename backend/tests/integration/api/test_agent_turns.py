"""J-04 Agent Turn API 集成测试。

覆盖三段式 Turn 执行的事务边界、幂等语义、澄清/答复/计划三类终态,
以及规划失败不产生 AgentAction/WorkflowRun 的保护。
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
from app.db.models.agent_turn import AgentTurn
from app.db.models.message import Message
from app.db.models.workflow_run import WorkflowRun
from app.domain.agent_planner import (
    AgentPlannerOutput,
    PlannerStep,
    PlannerTarget,
)
from app.llm.fake import FakeLLM
from app.skills.agent_command_planner import AgentCommandPlannerSkill

# ========================================================================
# Fixtures
# ========================================================================


@pytest.fixture
def planner_llm() -> FakeLLM:
    return FakeLLM(seed=42)


@pytest.fixture
def agent_service(planner_llm: FakeLLM) -> AgentCommandService:
    """基于 FakeLLM 的 AgentCommandService(确定性 Planner)。"""
    return AgentCommandService(
        settings=Settings(app_env="test"),
        planner_agent=BaseAgent(name="planner", llm=planner_llm),
    )


@pytest_asyncio.fixture
async def agent_api(
    app: Any, agent_service: AgentCommandService
) -> AsyncGenerator[AsyncClient, None]:
    """覆盖服务依赖后的 Agent API 客户端。"""
    from app.api.dependencies import get_agent_command_service

    app.dependency_overrides[get_agent_command_service] = lambda: agent_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def db_session(test_engine: Any) -> AsyncGenerator[AsyncSession, None]:
    """基于 conftest test_engine 的独立数据库会话。"""
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


# ========================================================================
# 辅助函数
# ========================================================================


def _plan_output() -> AgentPlannerOutput:
    """合法的 create_script plan 输出。"""
    return AgentPlannerOutput(
        turn_type="plan",
        intent="create_script",
        target=PlannerTarget(target_type="project"),
        steps=[PlannerStep(title="整理需求", description="确认项目创作范围")],
        expected_impact=["生成新的创作计划"],
    )


def _answer_output() -> AgentPlannerOutput:
    """合法的 explain answer 输出。"""
    return AgentPlannerOutput(
        turn_type="answer",
        intent="explain",
        answer="当前项目已有 3 集剧本。",
    )


async def _create_project(async_client: AsyncClient, title: str = "Agent 测试") -> str:
    resp = await async_client.post("/api/v1/projects", json={"title": title})
    assert resp.status_code == 201
    return str(resp.json()["id"])


def _turn_body(content: str, key: str, conversation_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"content": content, "idempotency_key": key}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    return body


async def _count(db_session: AsyncSession, model: Any) -> int:
    rows = (await db_session.execute(select(model.id))).all()
    return len(rows)


# ========================================================================
# TDD 锚点与用例
# ========================================================================


class _ProbeSkill:
    """包裹真实 Planner Skill,在 LLM 调用前用独立会话检查数据库可见状态。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.turns_seen: list[tuple[str, str]] = []
        self.messages_seen: list[tuple[str, str, str]] = []

    async def execute(self, context: dict[str, Any]) -> Any:
        import app.db.session as db_session_module

        factory = db_session_module._async_session_factory
        assert factory is not None
        async with factory() as session:
            turn_rows = (
                await session.execute(select(AgentTurn.id, AgentTurn.status))
            ).all()
            self.turns_seen = [(str(row[0]), str(row[1])) for row in turn_rows]
            msg_rows = (
                await session.execute(select(Message.id, Message.role, Message.kind))
            ).all()
            self.messages_seen = [(str(row[0]), str(row[1]), str(row[2])) for row in msg_rows]
        return await self._inner.execute(context)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_planner_runs_after_initial_transaction_commits(
    agent_api: AsyncClient, planner_llm: FakeLLM, app: Any
) -> None:
    """LLM 调用前,事务 A(用户消息+Turn)与 lease 事务均已提交并对其他会话可见。"""
    project_id = await _create_project(agent_api)
    planner_llm.register("agent_command_planner", _plan_output())

    # 用探针替换服务的 Planner Skill,在模型调用前以独立会话读取数据库。
    from app.api.dependencies import get_agent_command_service

    inner = AgentCommandPlannerSkill()
    probe = _ProbeSkill(inner)
    original = agent_api._transport  # 仅占位,探针服务通过覆盖依赖注入
    service = AgentCommandService(
        settings=Settings(app_env="test"),
        planner_agent=BaseAgent(name="planner", llm=planner_llm),
        planner_skill=probe,
    )
    app.dependency_overrides[get_agent_command_service] = lambda: service

    resp = await agent_api.post(
        f"/api/v1/projects/{project_id}/agent/turns", json=_turn_body("请创建剧本", "probe-1")
    )
    assert original is not None
    assert resp.status_code == 200
    body = resp.json()
    assert body["turn_type"] == "plan"
    assert body["action_id"]

    # 探针在 LLM 调用前观察到:Turn 已处于 planning(lease 事务已提交),
    # user 消息已可见(事务 A 已提交)。
    assert len(probe.turns_seen) == 1
    assert probe.turns_seen[0][1] == "planning"
    user_messages = [m for m in probe.messages_seen if m[1] == "user"]
    assert len(user_messages) == 1
    assert user_messages[0][2] == "text"
    assert len(planner_llm.get_call_history()) >= 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_clarification_turn_returns_original_response(
    agent_api: AsyncClient, planner_llm: FakeLLM
) -> None:
    """重复请求(同 key 同载荷)返回持久化的澄清结果,不再次调用模型。"""
    project_id = await _create_project(agent_api)
    # "把这里改得更紧张" 命中确定性 preflight,无需注册模型输出。
    body = _turn_body("把这里改得更紧张", "clarify-1")

    first = await agent_api.post(f"/api/v1/projects/{project_id}/agent/turns", json=body)
    assert first.status_code == 200
    first_json = first.json()
    assert first_json["turn_type"] == "clarification"
    assert first_json["status"] == "needs_input"

    second = await agent_api.post(f"/api/v1/projects/{project_id}/agent/turns", json=body)
    assert second.status_code == 200
    second_json = second.json()
    assert second_json["id"] == first_json["id"]
    assert second_json["response_message_id"] == first_json["response_message_id"]
    assert second_json["turn_type"] == "clarification"

    # 会话中恰有 2 条消息(user + clarification),Planner 未调用模型。
    conversation_id = first_json["conversation_id"]
    msgs = await agent_api.get(f"/api/v1/conversations/{conversation_id}/messages")
    assert msgs.status_code == 200
    items = msgs.json()["items"]
    assert len(items) == 2
    assert {m["kind"] for m in items} == {"text", "clarification"}
    assert planner_llm.get_call_history() == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reused_idempotency_key_with_different_payload_is_rejected(
    agent_api: AsyncClient, planner_llm: FakeLLM
) -> None:
    """同 key 不同载荷返回 409 IDEMPOTENCY_KEY_REUSED,且不追加第二条 user 消息。"""
    project_id = await _create_project(agent_api)
    planner_llm.register("agent_command_planner", _plan_output())

    first = await agent_api.post(
        f"/api/v1/projects/{project_id}/agent/turns", json=_turn_body("请创建剧本", "reuse-1")
    )
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]

    second = await agent_api.post(
        f"/api/v1/projects/{project_id}/agent/turns",
        json=_turn_body("评估项目", "reuse-1", conversation_id=conversation_id),
    )
    assert second.status_code == 409
    assert second.json()["code"] == "IDEMPOTENCY_KEY_REUSED"

    msgs = await agent_api.get(f"/api/v1/conversations/{conversation_id}/messages")
    items = msgs.json()["items"]
    assert len(items) == 2  # user + action_plan,重复请求未追加消息


@pytest.mark.integration
@pytest.mark.asyncio
async def test_conversation_auto_created_with_truncated_title(agent_api: AsyncClient) -> None:
    """conversation_id=null 时自动创建会话,标题取首条消息前 30 字。"""
    project_id = await _create_project(agent_api)
    content = "这是一个超过三十个字符的初始请求内容用来验证标题截断逻辑是否正确"

    resp = await agent_api.post(
        f"/api/v1/projects/{project_id}/agent/turns", json=_turn_body(content, "conv-1")
    )
    assert resp.status_code == 200
    conversation_id = resp.json()["conversation_id"]
    assert conversation_id

    convs = await agent_api.get(f"/api/v1/projects/{project_id}/conversations")
    items = convs.json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == content[:30]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_planner_failure_marks_turn_failed_without_action_or_run(
    agent_api: AsyncClient, planner_llm: FakeLLM, db_session: AsyncSession
) -> None:
    """Planner 失败只把 Turn 标为 failed,不创建 AgentAction 或 WorkflowRun。"""
    project_id = await _create_project(agent_api)
    # 不注册 fixture → FakeLLM 返回 INVALID_OUTPUT → Skill 抛 InvalidPlannerOutputError。
    resp = await agent_api.post(
        f"/api/v1/projects/{project_id}/agent/turns", json=_turn_body("请创建剧本", "fail-1")
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error_code"]
    assert body["action_id"] is None

    msgs = await agent_api.get(
        f"/api/v1/conversations/{body['conversation_id']}/messages"
    )
    kinds = [m["kind"] for m in msgs.json()["items"]]
    assert "error" in kinds

    assert await _count(db_session, AgentAction) == 0
    assert await _count(db_session, WorkflowRun) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_explain_answer_creates_no_action_and_no_run(
    agent_api: AsyncClient, planner_llm: FakeLLM, db_session: AsyncSession
) -> None:
    """explain 只读答复不产生 AgentAction/WorkflowRun。"""
    project_id = await _create_project(agent_api)
    planner_llm.register("agent_command_planner", _answer_output())

    resp = await agent_api.post(
        f"/api/v1/projects/{project_id}/agent/turns",
        json=_turn_body("解释当前项目进度", "explain-1"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["turn_type"] == "answer"
    assert body["status"] == "answered"
    assert body["action_id"] is None

    assert await _count(db_session, AgentAction) == 0
    assert await _count(db_session, WorkflowRun) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_plan_creates_proposed_action_and_action_plan_message(
    agent_api: AsyncClient, planner_llm: FakeLLM
) -> None:
    """plan 终态创建 proposed AgentAction 与 action_plan 消息,步骤由服务端模板生成。"""
    project_id = await _create_project(agent_api)
    planner_llm.register("agent_command_planner", _plan_output())

    resp = await agent_api.post(
        f"/api/v1/projects/{project_id}/agent/turns", json=_turn_body("请创建剧本", "plan-1")
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["turn_type"] == "plan"
    assert body["status"] == "action_proposed"
    action_id = body["action_id"]
    assert action_id

    detail = await agent_api.get(f"/api/v1/agent/actions/{action_id}")
    assert detail.status_code == 200
    action = detail.json()
    assert action["status"] == "proposed"
    assert action["intent"] == "create_script"
    assert action["plan"]["command"]["outline_count"] == 10
    assert action["plan"]["command"]["script_count"] == 3
    assert len(action["plan"]["steps"]) == 5  # 服务端固定 5 步模板
    assert action["source_artifact_ids"] == []

    msgs = await agent_api.get(
        f"/api/v1/conversations/{body['conversation_id']}/messages"
    )
    plan_messages = [m for m in msgs.json()["items"] if m["kind"] == "action_plan"]
    assert len(plan_messages) == 1
    assert plan_messages[0]["metadata"]["agent_action_id"] == action_id
    assert plan_messages[0]["metadata"]["agent_turn_id"] == body["id"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_turn_returns_persisted_snapshot(agent_api: AsyncClient) -> None:
    """GET /agent/turns/{id} 返回持久化快照。"""
    project_id = await _create_project(agent_api)
    create = await agent_api.post(
        f"/api/v1/projects/{project_id}/agent/turns", json=_turn_body("把这里改得更紧张", "get-1")
    )
    turn_id = create.json()["id"]

    resp = await agent_api.get(f"/api/v1/agent/turns/{turn_id}")
    assert resp.status_code == 200
    assert resp.json()["turn_type"] == "clarification"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_turn_unknown_returns_404_agent_turn_not_found(agent_api: AsyncClient) -> None:
    resp = await agent_api.get(f"/api/v1/agent/turns/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "AGENT_TURN_NOT_FOUND"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cross_project_conversation_returns_409(
    agent_api: AsyncClient, planner_llm: FakeLLM
) -> None:
    """跨项目会话返回 409,不调用模型、不追加消息。"""
    project_a = await _create_project(agent_api, title="项目A")
    project_b = await _create_project(agent_api, title="项目B")
    conv = await agent_api.post(f"/api/v1/projects/{project_b}/conversations", json={"title": "B会话"})
    conversation_id = conv.json()["id"]

    resp = await agent_api.post(
        f"/api/v1/projects/{project_a}/agent/turns",
        json=_turn_body("请创建剧本", "cross-1", conversation_id=conversation_id),
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "INVALID_ACTIVE_CONTEXT"

    msgs = await agent_api.get(f"/api/v1/conversations/{conversation_id}/messages")
    assert msgs.json()["items"] == []
    assert planner_llm.get_call_history() == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openapi_contains_all_agent_endpoints(agent_api: AsyncClient) -> None:
    """OpenAPI 同步包含 5 个 Agent 端点。"""
    resp = await agent_api.get("/openapi.json")
    paths = resp.json()["paths"]
    for path in (
        "/api/v1/projects/{project_id}/agent/turns",
        "/api/v1/agent/turns/{turn_id}",
        "/api/v1/agent/actions/{action_id}",
        "/api/v1/agent/actions/{action_id}/confirm",
        "/api/v1/agent/actions/{action_id}/reject",
    ):
        assert path in paths, f"missing OpenAPI path: {path}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repeated_clarifications_offer_legal_examples_after_three_turns(
    agent_api: AsyncClient
) -> None:
    """连续 3 轮未解决后,第 4 轮澄清包含 4 个合法命令示例。"""
    project_id = await _create_project(agent_api)
    conversation_id: str | None = None

    for i in range(4):
        resp = await agent_api.post(
            f"/api/v1/projects/{project_id}/agent/turns",
            json=_turn_body("把这里改得更紧张", f"repeat-{i}", conversation_id=conversation_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["turn_type"] == "clarification"
        conversation_id = body["conversation_id"]

    question = body.get("clarification_snapshot")
    # 服务端响应没有内嵌 clarification 字段时,从消息读取。
    if question is None:
        msgs = await agent_api.get(f"/api/v1/conversations/{conversation_id}/messages")
        question = [m["content"] for m in msgs.json()["items"] if m["kind"] == "clarification"][-1]
    assert "创建剧本" in question
    assert "评估项目" in question
