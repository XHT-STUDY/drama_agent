"""J-01 AgentAction 状态机与数据库约束集成测试。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AgentStateTransitionError
from app.db.models.agent_action import AgentAction
from app.db.models.agent_turn import AgentTurn
from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.db.models.project import Project
from app.db.models.workflow_run import WorkflowRun
from app.db.repositories.agent_actions import AgentActionRepository


async def _seed_action_context(
    db: AsyncSession,
) -> tuple[Project, Conversation, AgentTurn]:
    """创建 AgentAction 所需的项目、会话和已规划 Turn。"""
    project = Project(title="AgentAction 测试")
    db.add(project)
    await db.flush()
    conversation = Conversation(project_id=project.id, title="Action 测试")
    db.add(conversation)
    await db.flush()
    message = Message(
        conversation_id=conversation.id,
        role="user",
        content="评估当前剧本",
        sequence=1,
    )
    db.add(message)
    await db.flush()
    turn = AgentTurn(
        project_id=project.id,
        conversation_id=conversation.id,
        user_message_id=message.id,
        idempotency_key=f"turn-{uuid.uuid4()}",
        request_hash="a" * 64,
        status="action_proposed",
        turn_type="plan",
    )
    db.add(turn)
    await db.flush()
    return project, conversation, turn


def _new_action(
    project: Project,
    conversation: Conversation,
    turn: AgentTurn,
    *,
    parent_action_id: uuid.UUID | None = None,
    replan_depth: int = 0,
) -> AgentAction:
    """构造最小合法的 proposed Action。"""
    return AgentAction(
        project_id=project.id,
        conversation_id=conversation.id,
        agent_turn_id=turn.id,
        parent_action_id=parent_action_id,
        replan_depth=replan_depth,
        intent="evaluate",
        status="proposed",
        requires_confirmation=True,
        plan={"goal": "评估当前剧本"},
        source_artifact_ids=[],
    )


@pytest.mark.integration
@pytest.mark.asyncio
class TestAgentActionRepository:
    """AgentAction 的执行边界必须由状态机和约束共同保护。"""

    async def test_duplicate_confirmation_cannot_attach_two_runs(
        self,
        test_session: AsyncSession,
    ) -> None:
        """Action 已进入 queued 后，第二次确认不能替换关联 Run。"""
        project, conversation, turn = await _seed_action_context(test_session)
        action = _new_action(project, conversation, turn)
        run_one = WorkflowRun(
            project_id=project.id,
            action="evaluate",
            status="queued",
        )
        run_two = WorkflowRun(
            project_id=project.id,
            action="evaluate",
            status="completed",
        )
        test_session.add_all([action, run_one, run_two])
        await test_session.flush()
        repo = AgentActionRepository(test_session)

        queued = await repo.transition(
            action.id,
            "queued",
            expected_statuses={"proposed"},
            run_id=run_one.id,
        )
        assert queued.run_id == run_one.id

        with pytest.raises(AgentStateTransitionError):
            await repo.transition(
                action.id,
                "queued",
                expected_statuses={"proposed"},
                run_id=run_two.id,
            )
        assert action.run_id == run_one.id

    async def test_run_id_is_unique_across_actions(
        self,
        test_session: AsyncSession,
    ) -> None:
        """同一个 WorkflowRun 不能关联到两个 Action。"""
        project, conversation, turn = await _seed_action_context(test_session)
        second_message = Message(
            conversation_id=conversation.id,
            role="user",
            content="再次评估",
            sequence=2,
        )
        test_session.add(second_message)
        await test_session.flush()
        second_turn = AgentTurn(
            project_id=project.id,
            conversation_id=conversation.id,
            user_message_id=second_message.id,
            idempotency_key=f"turn-{uuid.uuid4()}",
            request_hash="b" * 64,
            status="action_proposed",
            turn_type="plan",
        )
        run = WorkflowRun(project_id=project.id, action="evaluate", status="queued")
        test_session.add_all([second_turn, run])
        await test_session.flush()
        first = _new_action(project, conversation, turn)
        first.run_id = run.id
        first.status = "queued"
        second = _new_action(project, conversation, second_turn)
        second.run_id = run.id
        second.status = "queued"
        test_session.add_all([first, second])

        with pytest.raises(IntegrityError):
            await test_session.flush()

    @pytest.mark.parametrize("invalid_depth", [1, 2])
    async def test_child_action_depth_is_bounded_by_database(
        self,
        test_session: AsyncSession,
        invalid_depth: int,
    ) -> None:
        """数据库拒绝无父 Action 的 depth=1 和超过上限的深度。"""
        project, conversation, turn = await _seed_action_context(test_session)
        invalid = _new_action(
            project,
            conversation,
            turn,
            replan_depth=invalid_depth,
        )
        test_session.add(invalid)

        with pytest.raises(IntegrityError):
            await test_session.flush()


class TestRunIdPartialUniqueW101:
    """W1-01：run_id 从全局唯一改为"非 continue Action 每 Run 至多一个"。"""

    async def test_continue_action_can_share_run_with_creator(
        self,
        test_session: AsyncSession,
    ) -> None:
        """continue 审计 Action 可关联创建者 Action 的 Run（聊天 continue
        意图确认的物理前提——此前全局唯一约束使该路径 IntegrityError）。"""
        project, conversation, turn = await _seed_action_context(test_session)
        second_message = Message(
            conversation_id=conversation.id,
            role="user",
            content="继续",
            sequence=2,
        )
        test_session.add(second_message)
        await test_session.flush()
        continue_turn = AgentTurn(
            project_id=project.id,
            conversation_id=conversation.id,
            user_message_id=second_message.id,
            idempotency_key=f"turn-continue-{uuid.uuid4()}",
            request_hash="c" * 64,
            status="action_proposed",
            turn_type="plan",
        )
        run = WorkflowRun(project_id=project.id, action="create_script", status="needs_review")
        test_session.add_all([continue_turn, run])
        await test_session.flush()

        creator = _new_action(project, conversation, turn)
        creator.intent = "create_script"
        creator.run_id = run.id
        creator.status = "needs_review"
        continue_action = AgentAction(
            project_id=project.id,
            conversation_id=conversation.id,
            agent_turn_id=continue_turn.id,
            replan_depth=0,
            intent="continue",
            status="queued",
            requires_confirmation=True,
            plan={"goal": "继续创作"},
            source_artifact_ids=[],
            run_id=run.id,
        )
        test_session.add_all([creator, continue_action])
        # 非 continue + continue 关联同一 Run：合法，不再 IntegrityError
        await test_session.flush()

    async def test_two_non_continue_actions_still_rejected(
        self,
        test_session: AsyncSession,
    ) -> None:
        """两个非 continue Action 关联同一 Run 仍被部分唯一索引拒绝。"""
        project, conversation, turn = await _seed_action_context(test_session)
        second_message = Message(
            conversation_id=conversation.id,
            role="user",
            content="再评估一次",
            sequence=2,
        )
        test_session.add(second_message)
        await test_session.flush()
        second_turn = AgentTurn(
            project_id=project.id,
            conversation_id=conversation.id,
            user_message_id=second_message.id,
            idempotency_key=f"turn-second-{uuid.uuid4()}",
            request_hash="d" * 64,
            status="action_proposed",
            turn_type="plan",
        )
        run = WorkflowRun(project_id=project.id, action="evaluate", status="queued")
        test_session.add_all([second_turn, run])
        await test_session.flush()
        first = _new_action(project, conversation, turn)
        first.run_id = run.id
        first.status = "queued"
        second = _new_action(project, conversation, second_turn)
        second.run_id = run.id
        second.status = "queued"
        test_session.add_all([first, second])

        with pytest.raises(IntegrityError):
            await test_session.flush()
