"""MCP 外部工具的对话链路集成测试（MCP-03）。

官方 in-process MCP Server 注入（无网络）验证完整用户路径：
工具请求 → use_external_tool Action（proposed，零远程调用）→ 确认 →
mcp_tool_call Run（一次调用）→ action_result 回写；拒绝零调用；
未知工具不产生 Action；定义变化转 stale；重复确认幂等；单活跃 Run。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.base import BaseAgent
from app.application.agent_command_service import AgentCommandService
from app.core.config import Settings
from app.integrations.mcp.manager import MCPClientManager
from app.integrations.mcp.protocol import MCPServerConfig
from app.integrations.mcp.runtime import set_manager
from app.llm.fake import FakeLLM

# ========================================================================
# Fixtures
# ========================================================================


class _CountingServer:
    """带调用计数的官方 in-process MCP Server 工厂。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self) -> Any:
        from mcp.server.mcpserver import MCPServer

        outer = self
        server = MCPServer(name="research", version="2.0.0")

        @server.tool()
        def search(query: str) -> str:
            """检索 足球 青训 资料。"""
            outer.calls.append({"query": query})
            return f"青训资料命中: {query}"

        @server.tool()
        async def slow_search(query: str) -> str:
            """慢速检索 足球 资料。"""
            await asyncio.sleep(3.0)
            outer.calls.append({"query": query, "slow": True})
            return f"慢速命中: {query}"

        return server


@pytest.fixture
def counting_server() -> _CountingServer:
    return _CountingServer()


@pytest_asyncio.fixture
async def mcp_manager(counting_server: _CountingServer) -> AsyncGenerator[Any, None]:
    """启动 Manager 并注入 runtime holder + app.state。"""
    manager = MCPClientManager(
        [MCPServerConfig(
            id="research",
            url="http://127.0.0.1:9000/mcp",
            allowed_tools=["search", "slow_search"],
            allow_private_network=True,
            timeout_seconds=10.0,
        )],
        app_env="test",
        server_factories={"research": counting_server},
    )
    await manager.startup()
    set_manager(manager)
    try:
        yield manager
    finally:
        set_manager(None)
        await manager.shutdown()


def _planner_output(tool: str = "search", arguments: dict[str, Any] | None = None) -> Any:
    from app.domain.agent_planner import (
        AgentPlannerOutput,
        PlannerExternalToolCall,
        PlannerStep,
        PlannerTarget,
    )

    return AgentPlannerOutput(
        turn_type="plan",
        intent="use_external_tool",
        target=PlannerTarget(target_type="external_tool"),
        steps=[PlannerStep(title="检索资料", description="检索相关背景资料并返回结果")],
        expected_impact=["不修改稿件，仅返回外部检索结果"],
        external_tool=PlannerExternalToolCall(
            qualified_tool_name=f"mcp__research__{tool}",
            arguments=arguments or {"query": "足球青训"},
            purpose="检索创作背景资料",
        ),
    )


@pytest.fixture
def planner_llm() -> FakeLLM:
    llm = FakeLLM(seed=42)
    llm.register("agent_command_planner", _planner_output())
    return llm


@pytest.fixture
def agent_service(
    planner_llm: FakeLLM, mcp_manager: Any
) -> AgentCommandService:
    return AgentCommandService(
        settings=Settings(app_env="test"),
        planner_agent=BaseAgent(name="planner", llm=planner_llm),
        mcp_manager_provider=lambda: mcp_manager,
    )


@pytest_asyncio.fixture
async def mcp_api(
    app: Any, agent_service: AgentCommandService, mcp_manager: Any
) -> AsyncGenerator[AsyncClient, None]:
    from app.api.dependencies import get_agent_command_service

    app.dependency_overrides[get_agent_command_service] = lambda: agent_service
    app.state.mcp_manager = mcp_manager
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
    app.state.mcp_manager = None


@pytest_asyncio.fixture
async def db_session(test_engine: Any) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()


# ========================================================================
# 辅助
# ========================================================================


async def _create_project(client: AsyncClient) -> str:
    resp = await client.post("/api/v1/projects", json={"title": "MCP 测试项目"})
    assert resp.status_code == 201
    return str(resp.json()["id"])


async def _request_turn(
    client: AsyncClient, project_id: str, content: str, key: str
) -> Any:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/agent/turns",
        json={"content": content, "idempotency_key": key},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _wait_action_terminal(client: AsyncClient, action_id: str) -> dict[str, Any]:
    """轮询 Action 直至终态（Worker 异步执行）。"""
    for _ in range(100):
        resp = await client.get(f"/api/v1/agent/actions/{action_id}")
        assert resp.status_code == 200, resp.text
        action = resp.json()
        if action["status"] in (
            "completed", "failed", "cancelled", "stale", "rejected", "needs_review",
        ):
            return action
        await asyncio.sleep(0.1)
    raise AssertionError("Action 未在超时时间内到达终态")


# ========================================================================
# 用例
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tool_request_creates_proposed_action_without_remote_call(
    mcp_api: AsyncClient, counting_server: _CountingServer
) -> None:
    """明确的工具请求生成 use_external_tool Action，规划阶段零远程调用。"""
    project_id = await _create_project(mcp_api)
    turn = await _request_turn(mcp_api, project_id, "帮我检索足球青训资料", "mcp-1")

    assert turn["turn_type"] == "plan"
    action_id = turn["action_id"]
    assert action_id

    resp = await mcp_api.get(f"/api/v1/agent/actions/{action_id}")
    action = resp.json()
    assert action["intent"] == "use_external_tool"
    assert action["status"] == "proposed"
    assert action["requires_confirmation"] is True
    command = action["plan"]["command"]
    assert command["server_id"] == "research"
    assert command["tool_name"] == "search"
    assert command["qualified_tool_name"] == "mcp__research__search"
    assert command["arguments"] == {"query": "足球青训"}
    assert command["tool_definition_digest"]
    # 计划阶段不产生任何远程调用
    assert counting_server.calls == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reject_keeps_zero_remote_calls(
    mcp_api: AsyncClient, counting_server: _CountingServer
) -> None:
    """用户拒绝 → Action rejected，Fake Server 调用计数为 0。"""
    project_id = await _create_project(mcp_api)
    turn = await _request_turn(mcp_api, project_id, "帮我检索足球青训资料", "mcp-rej")
    resp = await mcp_api.post(f"/api/v1/agent/actions/{turn['action_id']}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert counting_server.calls == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_executes_once_and_writes_result_message(
    mcp_api: AsyncClient,
    counting_server: _CountingServer,
    db_session: AsyncSession,
) -> None:
    """确认 → 一次 Run/一次调用 → completed + action_result 消息。"""
    project_id = await _create_project(mcp_api)
    turn = await _request_turn(mcp_api, project_id, "帮我检索足球青训资料", "mcp-ok")
    action_id = turn["action_id"]

    resp = await mcp_api.post(f"/api/v1/agent/actions/{action_id}/confirm")
    assert resp.status_code in (200, 202), resp.text  # 202 = Run 已排队
    run_id = resp.json()["run"]["run_id"]

    action = await _wait_action_terminal(mcp_api, action_id)
    assert action["status"] == "completed"
    assert action["run_id"] == run_id
    assert counting_server.calls == [{"query": "足球青训"}]

    # 结果消息（action_result，携带规范化 MCP 结果）
    from sqlalchemy import select

    from app.db.models.message import Message

    rows = (
        await db_session.execute(
            select(Message).where(
                Message.conversation_id == uuid.UUID(turn["conversation_id"]),
                Message.kind == "action_result",
            )
        )
    ).scalars().all()
    mcp_results = [
        m for m in rows
        if (m.message_metadata or {}).get("message_subtype") == "mcp_tool_result"
    ]
    assert len(mcp_results) == 1
    mcp = mcp_results[0].message_metadata["mcp_result"]
    assert mcp["tool_name"] == "search"
    assert mcp["content"][0]["text"] == "青训资料命中: 足球青训"
    assert "青训资料命中" in mcp_results[0].content


@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_confirm_is_idempotent(
    mcp_api: AsyncClient, counting_server: _CountingServer
) -> None:
    """重复确认复用同一个 Run，只执行一次工具调用。"""
    project_id = await _create_project(mcp_api)
    turn = await _request_turn(mcp_api, project_id, "帮我检索足球青训资料", "mcp-idem")
    action_id = turn["action_id"]

    first = await mcp_api.post(f"/api/v1/agent/actions/{action_id}/confirm")
    assert first.status_code in (200, 202)
    run_id = first.json()["run"]["run_id"]
    await _wait_action_terminal(mcp_api, action_id)

    second = await mcp_api.post(f"/api/v1/agent/actions/{action_id}/confirm")
    assert second.status_code in (200, 202)
    assert second.json()["run"]["run_id"] == run_id
    assert len(counting_server.calls) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unknown_tool_output_rejected_no_action_created(
    mcp_api: AsyncClient, planner_llm: FakeLLM
) -> None:
    """Planner 输出目录外工具 → 不创建 Action（Turn 失败）。"""
    planner_llm.register(
        "agent_command_planner", _planner_output(tool="not_in_catalog")
    )
    project_id = await _create_project(mcp_api)
    resp = await mcp_api.post(
        f"/api/v1/projects/{project_id}/agent/turns",
        json={"content": "检索资料", "idempotency_key": "mcp-unknown"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["action_id"] is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_definition_changed_before_confirm_marks_stale(
    mcp_api: AsyncClient, mcp_manager: Any, counting_server: _CountingServer
) -> None:
    """确认前工具定义变化（digest 不一致）→ Action 转 stale，不执行。"""
    project_id = await _create_project(mcp_api)
    turn = await _request_turn(mcp_api, project_id, "帮我检索足球青训资料", "mcp-stale")
    action_id = turn["action_id"]

    # 模拟工具定义变化：catalog 中该工具转 stale
    mcp_manager.catalog.mark_server_stale("research")

    resp = await mcp_api.post(f"/api/v1/agent/actions/{action_id}/confirm")
    assert resp.status_code == 409
    assert resp.json()["code"] == "ACTION_STALE"

    action_resp = await mcp_api.get(f"/api/v1/agent/actions/{action_id}")
    action = action_resp.json()
    assert action["status"] == "stale"
    assert counting_server.calls == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_single_active_run_constraint(
    mcp_api: AsyncClient, planner_llm: FakeLLM
) -> None:
    """MCP Run 执行期间确认第二个计划 → 单活跃 Run 保护（PROJECT_HAS_ACTIVE_RUN）。"""
    planner_llm.register("agent_command_planner", _planner_output(tool="slow_search"))
    project_id = await _create_project(mcp_api)
    turn1 = await _request_turn(mcp_api, project_id, "慢速检索资料", "mcp-run-1")
    resp1 = await mcp_api.post(f"/api/v1/agent/actions/{turn1['action_id']}/confirm")
    assert resp1.status_code in (200, 202)

    turn2 = await _request_turn(mcp_api, project_id, "再次检索资料", "mcp-run-2")
    resp2 = await mcp_api.post(f"/api/v1/agent/actions/{turn2['action_id']}/confirm")
    assert resp2.status_code == 409
    assert resp2.json()["code"] == "PROJECT_HAS_ACTIVE_RUN"

    # 等第一个 Run 结束，避免污染后续测试
    await _wait_action_terminal(mcp_api, turn1["action_id"])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_creation_path_survives_all_mcp_servers_degraded(
    app: Any, db_session: AsyncSession
) -> None:
    """MCP-04 验收：全部 MCP Server 故障时核心创作路径照常完成。

    用默认服务依赖（FakeLLM create_script fixture）跑完整创建链路，
    同时挂一个全部 Server degraded 的 Manager——MCP 是可选依赖，
    任何故障不得影响核心创作。
    """
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select

    from app.api.dependencies import get_agent_command_service
    from app.db.models.workflow_run import WorkflowRun
    from app.integrations.mcp.manager import MCPClientManager
    from app.integrations.mcp.protocol import MCPServerConfig
    from app.integrations.mcp.runtime import set_manager

    degraded_manager = MCPClientManager(
        [
            MCPServerConfig(
                id="dead_a",
                url="http://127.0.0.1:9/mcp",
                allowed_tools=["x"],
                allow_private_network=True,
                timeout_seconds=2,
            ),
            MCPServerConfig(
                id="dead_b",
                url="http://127.0.0.1:9/mcp",
                allowed_tools=["y"],
                allow_private_network=True,
                timeout_seconds=2,
            ),
        ],
        app_env="test",
    )
    await degraded_manager.startup()
    states = degraded_manager.server_states()
    assert all(s.status == "degraded" for s in states.values()), states

    app.state.mcp_manager = degraded_manager
    set_manager(degraded_manager)
    # 默认服务依赖（FakeLLM create_script fixture，与生产惰性单例同构）
    from app.agents.base import BaseAgent
    from app.application.agent_command_service import AgentCommandService
    from app.core.config import Settings
    from app.domain.agent_planner import (
        AgentPlannerOutput,
        PlannerStep,
        PlannerTarget,
    )

    planner_llm = FakeLLM(seed=42)
    planner_llm.register(
        "agent_command_planner",
        AgentPlannerOutput(
            turn_type="plan",
            intent="create_script",
            target=PlannerTarget(target_type="project"),
            steps=[PlannerStep(title="整理需求", description="确认项目创作范围")],
            expected_impact=["生成新的创作计划"],
        ),
    )
    service = AgentCommandService(
        settings=Settings(app_env="test"),
        planner_agent=BaseAgent(name="planner", llm=planner_llm),
    )
    app.dependency_overrides[get_agent_command_service] = lambda: service
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/projects", json={"title": "MCP 故障下创作"})
            project_id = str(resp.json()["id"])
            turn = await client.post(
                f"/api/v1/projects/{project_id}/agent/turns",
                json={
                    "content": "写一个被青训队抛弃的足球少年逆袭的短剧",
                    "idempotency_key": "mcp-degraded-creation",
                    "staged": True,  # SB+大纲确认门即证明核心链路可用
                    "target_episode_count": 10,
                },
            )
            assert turn.status_code == 200, turn.text
            confirm = await client.post(
                f"/api/v1/agent/actions/{turn.json()['action_id']}/confirm"
            )
            assert confirm.status_code == 202, confirm.text
            run_id = confirm.json()["run"]["run_id"]

            for _ in range(600):
                rows = await db_session.execute(
                    select(WorkflowRun.status).where(WorkflowRun.id == uuid.UUID(run_id))
                )
                status = rows.scalar_one_or_none()
                await db_session.rollback()
                if status in ("completed", "needs_review", "failed"):
                    break
                await asyncio.sleep(0.2)
            assert status in ("completed", "needs_review"), f"核心创作被 MCP 故障拖垮: {status}"
            # 终态事件已发布（SSE 与事件查询的事实源：MCP 关闭/故障不影响事件链路）
            from app.db.models.workflow_event import WorkflowEvent as RunEvent

            events = await db_session.execute(
                select(RunEvent.type).where(RunEvent.run_id == uuid.UUID(run_id))
            )
            event_types = [e[0] for e in events.all()]
            assert "run.needs_review" in event_types or "run.completed" in event_types
    finally:
        app.dependency_overrides.clear()
        set_manager(None)
        app.state.mcp_manager = None
        await degraded_manager.shutdown()
