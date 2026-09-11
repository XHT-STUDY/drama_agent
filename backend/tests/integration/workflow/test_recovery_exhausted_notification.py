"""耗尽路径（WORKFLOW_RECOVERY_EXHAUSTED）必须通知用户——P0' 回归测试。

事故形态（2026-09-07/08 实测）：重试尝试的终态写入丢失 → Run 僵死 running
22 小时 → 次日重启被标记恢复耗尽，但耗尽分支只发事件不回写 Action——
用户的对话永远停在「已开始从断点重试」。修复后：claim_next 标记耗尽的
同时，在领取事务外独立回写 Action 终态与结果消息。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.workflow_dispatcher import WorkflowDispatcher
from app.db.models.agent_action import AgentAction
from app.db.models.agent_turn import AgentTurn
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


async def _seed_exhausted_scenario(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[WorkflowRun, AgentAction]:
    """僵死 running + 租约过期 + attempt 耗尽 + 关联 running Action。"""
    async with factory() as db, db.begin():
        project = Project(title="耗尽项目", target_episode_count=2)
        db.add(project)
        await db.flush()
        conversation = Conversation(project_id=project.id, title="耗尽会话")
        db.add(conversation)
        await db.flush()
        message = Message(
            conversation_id=conversation.id, role="user", content="seed",
            kind="text", message_metadata={}, sequence=1,
        )
        db.add(message)
        await db.flush()
        turn = AgentTurn(
            project_id=project.id, conversation_id=conversation.id,
            user_message_id=message.id, idempotency_key=f"seed-{uuid.uuid4().hex}",
            request_hash="0" * 64, status="action_proposed", turn_type="plan",
        )
        db.add(turn)
        await db.flush()
        plan = AgentActionPlan(
            goal="创建短剧", intent="create_script",
            command=CreateScriptCommand(user_input="x", outline_count=2, script_count=2),
            target=ActionTarget(target_type="project"),
            steps=[ActionStep(step_id="run", title="创建", description="执行")],
        )
        run = WorkflowRun(
            project_id=project.id, action="create_script", status="running",
            attempt_count=3,  # 已达默认上限
            lease_owner="dead-worker",
            lease_expires_at=datetime.now(UTC) - timedelta(hours=22),  # 僵死租约
            config_snapshot={"agent_action_id": None},  # 占位，flush 后回填
            state_summary={"completed_nodes": ["normalize", "story_bible", "outline"]},
        )
        db.add(run)
        await db.flush()
        action = AgentAction(
            project_id=project.id, conversation_id=conversation.id,
            agent_turn_id=turn.id, replan_depth=0, intent="create_script",
            status="running", requires_confirmation=True,
            plan=plan.model_dump(mode="json"), source_artifact_ids=[],
            run_id=run.id,
        )
        db.add(action)
        await db.flush()
        run.config_snapshot = {"agent_action_id": str(action.id)}
        run_id, action_id = run.id, action.id
    async with factory() as db:
        return (
            await db.get(WorkflowRun, run_id),  # type: ignore[return-value]
            await db.get(AgentAction, action_id),  # type: ignore[return-value]
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_exhausted_run_finalizes_action_and_notifies_user(
    test_engine: Any,
) -> None:
    """耗尽标记 + Action 回写 + 用户可见结果消息，一次 claim 完成。"""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    run, action = await _seed_exhausted_scenario(factory)

    dispatcher = WorkflowDispatcher(
        factory, owner="recovery", executor=lambda *_: asyncio.sleep(0)
    )
    claimed = await dispatcher.claim_next()

    assert claimed is None, "耗尽 Run 不应被领取执行"

    async with factory() as db:
        refreshed_run = await db.get(WorkflowRun, run.id)
        assert refreshed_run is not None
        assert refreshed_run.status == "failed"
        assert refreshed_run.error_code == "WORKFLOW_RECOVERY_EXHAUSTED"

        refreshed_action = await db.get(AgentAction, action.id)
        assert refreshed_action is not None
        assert refreshed_action.status == "failed"
        assert refreshed_action.result is not None

        messages = (await db.execute(
            select(Message).where(
                Message.conversation_id == action.conversation_id,
                Message.kind == "action_result",
            )
        )).scalars().all()
        assert messages, "耗尽必须产生用户可见的结果消息"
        content = messages[-1].content
        assert "失败" in content
        assert "重新发起" in content, "耗尽消息指向重新发起，而不是必然失败的重试"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_exhausted_finalize_failure_does_not_rollback_marking(
    test_engine: Any,
) -> None:
    """Action 回写抛异常时，耗尽标记必须已提交（不得把 Run 留在 running）。"""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    run, _action = await _seed_exhausted_scenario(factory)

    from app.application import workflow_dispatcher as dispatcher_module

    original = dispatcher_module._finalize_agent_action_if_any

    async def _explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("finalize 注入失败")

    dispatcher_module._finalize_agent_action_if_any = _explode
    try:
        dispatcher = WorkflowDispatcher(
            factory, owner="recovery", executor=lambda *_: asyncio.sleep(0)
        )
        claimed = await dispatcher.claim_next()
        assert claimed is None
    finally:
        dispatcher_module._finalize_agent_action_if_any = original

    async with factory() as db:
        refreshed = await db.get(WorkflowRun, run.id)
        assert refreshed is not None
        assert refreshed.status == "failed", "回写失败不得回滚耗尽标记"
        assert refreshed.error_code == "WORKFLOW_RECOVERY_EXHAUSTED"
