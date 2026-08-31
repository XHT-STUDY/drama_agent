"""AgentAction 生命周期事件集成测试（J-09）。

核心锚点：Run 终态后的 reconciliation（重复 finalize）只追加一条
result 消息，并发布携带 agent_action_id / goal_status 的
agent_action.updated 事件。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.agent_action_lifecycle import AgentActionLifecycle
from app.db.models.agent_action import AgentAction
from app.db.models.agent_turn import AgentTurn
from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.db.models.project import Project
from app.db.models.workflow_event import WorkflowEvent
from app.db.models.workflow_run import WorkflowRun


@pytest_asyncio.fixture
async def db_session(test_engine: Any) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


async def _seed_completed_agent_run(
    session: AsyncSession,
    *,
    intent: str = "revise_outline",
    state_summary: dict[str, Any] | None = None,
) -> tuple[AgentAction, WorkflowRun]:
    """播种 终态 Run + running Action（模拟 Worker 在回写前崩溃）。"""
    project = Project(title="Lifecycle 测试", target_episode_count=10)
    session.add(project)
    await session.flush()
    conversation = Conversation(project_id=project.id, title="会话")
    session.add(conversation)
    await session.flush()
    message = Message(
        conversation_id=conversation.id, role="user", content="seed",
        kind="text", message_metadata={}, sequence=1,
    )
    session.add(message)
    await session.flush()
    turn = AgentTurn(
        project_id=project.id, conversation_id=conversation.id,
        user_message_id=message.id, idempotency_key=f"seed-{uuid.uuid4().hex}",
        request_hash="0" * 64, status="action_proposed", turn_type="plan",
    )
    session.add(turn)
    await session.flush()
    run = WorkflowRun(
        project_id=project.id, action=intent, status="completed",
        config_snapshot={"agent_action_id": "pending"},
        state_summary=state_summary or {},
    )
    session.add(run)
    await session.flush()
    action = AgentAction(
        project_id=project.id, conversation_id=conversation.id,
        agent_turn_id=turn.id, intent=intent, status="running",
        plan={"goal": "修订大纲", "intent": intent, "constraints": [], "steps": []},
        run_id=run.id,
    )
    session.add(action)
    await session.commit()
    return action, run


async def _messages_of(
    session: AsyncSession, conversation_id: uuid.UUID, kind: str
) -> list[Message]:
    result = await session.execute(
        select(Message).where(
            Message.conversation_id == conversation_id, Message.kind == kind
        )
    )
    return list(result.scalars().all())


@pytest.mark.integration
@pytest.mark.asyncio
class TestAgentActionEvents:
    async def test_terminal_run_reconciliation_appends_one_result_message(
        self, db_session: AsyncSession
    ) -> None:
        """TDD anchor: 重复 finalize（reconciliation）只追加一条 result 消息。"""
        action, run = await _seed_completed_agent_run(db_session)

        lifecycle = AgentActionLifecycle()
        first = await lifecycle.finalize(
            db_session, action_id=action.id, run=run, final_state=run.state_summary
        )
        await db_session.commit()
        assert first is not None and first.status == "completed"
        assert first.result is not None
        assert first.result["goal_status"] == "achieved"

        # Worker 崩溃后重放（reconciliation）：不得重复消息/事件/回写
        second = await lifecycle.finalize(
            db_session, action_id=action.id, run=run, final_state=run.state_summary
        )
        await db_session.commit()
        assert second is not None and second.status == "completed"

        result_messages = await _messages_of(db_session, action.conversation_id, "action_result")
        assert len(result_messages) == 1
        assert result_messages[0].message_metadata["agent_action_id"] == str(action.id)
        assert result_messages[0].message_metadata["goal_status"] == "achieved"

        # agent_action.updated 事件只发布一次，payload 含 agent_action_id/goal_status
        events = await db_session.execute(
            select(WorkflowEvent).where(WorkflowEvent.type == "agent_action.updated")
        )
        action_events = [
            e
            for e in events.scalars().all()
            if (e.payload or {}).get("agent_action_id") == str(action.id)
        ]
        assert len(action_events) >= 1
        assert action_events[0].payload["goal_status"] == "achieved"  # type: ignore[index]
        assert action_events[0].run_id == run.id

    async def test_partial_outcome_appends_follow_up_plan_message_once(
        self, db_session: AsyncSession
    ) -> None:
        """部分达成 + 依赖剧本 → 一次后续计划消息与 proposed 子 Action（各一条）。"""
        state_summary = {
            "outline_impact": {
                "changed_episodes": [3],
                "dependent_script_ids": ["00000000-0000-0000-0000-000000000003"],
                "follow_ups": ["第 3 集剧本依赖旧大纲且该集大纲已变化，建议发起剧本修订"],
            },
            "outline_set_artifact_id": "00000000-0000-0000-0000-000000000001",
        }
        action, run = await _seed_completed_agent_run(
            db_session, state_summary=state_summary
        )
        # 子提案目标重解析需要：第 3 集存在最新 valid 剧本
        from app.artifacts.store import ArtifactStore

        await ArtifactStore().create(
            db_session,
            project_id=action.project_id,
            artifact_type="script_draft",
            episode_number=3,
            status="valid",
            content={"title": "第 3 集", "scenes": []},
        )
        await db_session.commit()

        lifecycle = AgentActionLifecycle()
        await lifecycle.finalize(
            db_session, action_id=action.id, run=run, final_state=state_summary
        )
        await db_session.commit()
        # 重复 reconciliation
        await lifecycle.finalize(
            db_session, action_id=action.id, run=run, final_state=state_summary
        )
        await db_session.commit()

        assert action.result["goal_status"] == "partially_achieved"  # type: ignore[index]

        children = await db_session.execute(
            select(AgentAction).where(AgentAction.parent_action_id == action.id)
        )
        child_list = list(children.scalars().all())
        assert len(child_list) == 1
        child = child_list[0]
        assert child.status == "proposed"
        assert child.replan_depth == 1
        assert child.run_id is None  # 后续 Action 只展示，不自动建 Run
        # 子计划携带父计划未自动确认的约束（不得无约束盲改）
        assert child.plan["constraints"] != []
        assert child.plan["expected_impact"] == ["落实上一轮未自动确认的修改要求"]

        plan_messages = await _messages_of(db_session, action.conversation_id, "action_plan")
        assert len(plan_messages) == 1
        assert plan_messages[0].message_metadata["agent_action_id"] == str(child.id)

    async def test_depth_one_action_never_creates_child(
        self, db_session: AsyncSession
    ) -> None:
        """深度 1 的 Action 完成后不得继续生成子 Action。"""
        state_summary = {
            "outline_impact": {
                "changed_episodes": [3],
                "dependent_script_ids": ["00000000-0000-0000-0000-000000000003"],
                "follow_ups": ["建议修订"],
            }
        }
        action, run = await _seed_completed_agent_run(
            db_session, state_summary=state_summary
        )
        # 深度 1 的子 Action 完成场景（独立 Turn：agent_turn_id 唯一约束）
        parent_turn = await db_session.get(AgentTurn, action.agent_turn_id)
        assert parent_turn is not None
        trigger_message = Message(
            conversation_id=action.conversation_id, role="assistant",
            content="上一个计划已部分完成", kind="action_result",
            message_metadata={"agent_action_id": str(action.id), "message_type": "result"},
        )
        db_session.add(trigger_message)
        await db_session.flush()
        child_turn = AgentTurn(
            project_id=action.project_id, conversation_id=action.conversation_id,
            user_message_id=trigger_message.id,
            idempotency_key=f"depth1-{uuid.uuid4().hex}",
            request_hash="0" * 64, status="action_proposed", turn_type="plan",
        )
        db_session.add(child_turn)
        await db_session.flush()
        parent = AgentAction(
            project_id=action.project_id, conversation_id=action.conversation_id,
            agent_turn_id=child_turn.id, parent_action_id=action.id,
            replan_depth=1, intent="revise_outline", status="running",
            plan={"goal": "后续修订", "intent": "revise_outline", "constraints": [], "steps": []},
            run_id=None,
        )
        db_session.add(parent)
        await db_session.flush()

        outcome_run = WorkflowRun(
            project_id=action.project_id, action="revise_outline", status="completed",
            config_snapshot={}, state_summary=state_summary,
        )
        db_session.add(outcome_run)
        await db_session.flush()
        parent.run_id = outcome_run.id
        await db_session.commit()

        result = await AgentActionLifecycle().finalize(
            db_session, action_id=parent.id, run=outcome_run, final_state=state_summary
        )
        await db_session.commit()
        assert result is not None

        children = await db_session.execute(
            select(AgentAction).where(AgentAction.parent_action_id == parent.id)
        )
        assert children.scalar_one_or_none() is None

    async def test_stage_gate_outcome_is_clean_and_has_no_follow_up(
        self, db_session: AsyncSession
    ) -> None:
        """确认门（stage_gate）是设计内的阶段暂停：干净进展消息 + 无子提案。

        - 结果消息为可读阶段文案，metadata 带 stage_gate，
          不携带未完成约束与产物清单；
        - 不生成"建议后续动作"子 Action（用户只需在门上确认续跑）。
        """
        state_summary = {
            "stage_gate": "outline",
            "stop_after": "outline",
            "story_bible_artifact_id": "00000000-0000-0000-0000-00000000000a",
            "outline_set_artifact_id": "00000000-0000-0000-0000-00000000000b",
            "completed_nodes": ["normalize", "retrieve", "story_bible", "outline"],
        }
        action, run = await _seed_completed_agent_run(
            db_session, intent="create_script", state_summary=state_summary
        )
        run.status = "needs_review"
        await db_session.commit()

        finalized = await AgentActionLifecycle().finalize(
            db_session, action_id=action.id, run=run, final_state=state_summary
        )
        await db_session.commit()

        assert finalized is not None
        assert finalized.status == "needs_review"
        result = finalized.result or {}
        assert result["goal_status"] == "partially_achieved"
        assert result["remaining_constraints"] == []
        assert result["recommended_next_action"] is None

        # 不生成子提案
        children = await db_session.execute(
            select(AgentAction).where(AgentAction.parent_action_id == action.id)
        )
        assert children.scalar_one_or_none() is None

        # 结果消息：干净阶段文案 + stage_gate metadata，无未完成约束/产物
        messages = await _messages_of(db_session, action.conversation_id, "action_result")
        assert len(messages) == 1
        meta = messages[0].message_metadata
        assert meta["stage_gate"] == "outline"
        assert "remaining_constraints" not in meta
        assert "evidence_artifact_ids" not in meta
        assert "大纲已生成" in messages[0].content
        assert "未完成" not in messages[0].content

    async def test_completed_revision_with_gated_run_appends_next_step_once(
        self, db_session: AsyncSession
    ) -> None:
        """门上多轮修订的主场景：修订完成 + 主任务停在门 → 一次下一步指引。

        - 结果消息为自然文案（无 goal_status 术语/产物清单）；
        - 指引消息幂等（reconciliation 不重复）；
        - 项目无门上 Run 时不追加指引。
        """
        action, run = await _seed_completed_agent_run(db_session)
        # 同项目播种一个停在大纲门的主创作 Run
        gate_run = WorkflowRun(
            project_id=action.project_id, action="create_script",
            status="needs_review", config_snapshot={},
            state_summary={"stage_gate": "outline"},
        )
        db_session.add(gate_run)
        await db_session.commit()

        lifecycle = AgentActionLifecycle()
        await lifecycle.finalize(
            db_session, action_id=action.id, run=run, final_state=run.state_summary
        )
        await db_session.commit()
        # reconciliation 重放：不得重复指引
        await lifecycle.finalize(
            db_session, action_id=action.id, run=run, final_state=run.state_summary
        )
        await db_session.commit()

        results = await _messages_of(db_session, action.conversation_id, "action_result")
        assert len(results) == 1
        assert results[0].content.startswith("本轮任务已完成。")
        assert "achieved" not in results[0].content
        assert "Artifact" not in results[0].content

        texts = await _messages_of(db_session, action.conversation_id, "text")
        next_steps = [
            m for m in texts
            if (m.message_metadata or {}).get("message_type") == "next_step"
        ]
        assert len(next_steps) == 1
        assert "继续" in next_steps[0].content
        assert "确认门" in next_steps[0].content

    async def test_no_next_step_without_gated_run(self, db_session: AsyncSession) -> None:
        """项目无门上 Run 时，完成任务不追加指引。"""
        action, run = await _seed_completed_agent_run(db_session)
        await db_session.commit()

        await AgentActionLifecycle().finalize(
            db_session, action_id=action.id, run=run, final_state=run.state_summary
        )
        await db_session.commit()

        texts = await _messages_of(db_session, action.conversation_id, "text")
        next_steps = [
            m for m in texts
            if (m.message_metadata or {}).get("message_type") == "next_step"
        ]
        assert next_steps == []


    async def test_next_step_hint_is_replaced_not_accumulated(
        self, db_session: AsyncSession
    ) -> None:
        """门上多轮修订：每轮完成替换旧指引，聊天里始终只有一条。"""
        action, run = await _seed_completed_agent_run(db_session)
        gate_run = WorkflowRun(
            project_id=action.project_id, action="create_script",
            status="needs_review", config_snapshot={},
            state_summary={"stage_gate": "outline"},
        )
        db_session.add(gate_run)
        await db_session.commit()

        lifecycle = AgentActionLifecycle()
        await lifecycle.finalize(
            db_session, action_id=action.id, run=run, final_state=run.state_summary
        )
        await db_session.commit()

        # 第二轮修订（新 Action/新 Run，同会话）完成后旧指引应被替换
        message = Message(
            conversation_id=action.conversation_id, role="user", content="再改一次",
            kind="text", message_metadata={}, sequence=100,
        )
        db_session.add(message)
        await db_session.flush()
        turn2 = AgentTurn(
            project_id=action.project_id, conversation_id=action.conversation_id,
            user_message_id=message.id, idempotency_key=f"seed-{uuid.uuid4().hex}",
            request_hash="0" * 64, status="action_proposed", turn_type="plan",
        )
        db_session.add(turn2)
        await db_session.flush()
        action2 = AgentAction(
            project_id=action.project_id, conversation_id=action.conversation_id,
            agent_turn_id=turn2.id, intent="revise_outline", status="running",
            plan={"goal": "再修一版", "intent": "revise_outline",
                  "constraints": [], "steps": []},
            run_id=None,
        )
        db_session.add(action2)
        await db_session.flush()
        run2 = WorkflowRun(
            project_id=action.project_id, action="revise_outline", status="completed",
            config_snapshot={}, state_summary={},
        )
        db_session.add(run2)
        await db_session.flush()
        action2.run_id = run2.id
        await db_session.commit()

        await lifecycle.finalize(
            db_session, action_id=action2.id, run=run2, final_state={}
        )
        await db_session.commit()

        texts = await _messages_of(db_session, action.conversation_id, "text")
        next_steps = [
            m for m in texts
            if (m.message_metadata or {}).get("message_type") == "next_step"
        ]
        assert len(next_steps) == 1
        assert next_steps[0].message_metadata["agent_action_id"] == str(action2.id)
