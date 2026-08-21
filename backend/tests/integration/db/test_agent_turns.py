"""J-01 AgentTurn 持久化与 planning lease 集成测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AgentStateTransitionError, IdempotencyKeyReusedError
from app.db.models.agent_turn import AgentTurn
from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.db.models.project import Project
from app.db.repositories.agent_turns import AgentTurnRepository


async def _seed_turn_context(
    db: AsyncSession,
) -> tuple[Project, Conversation, Message, Message]:
    """创建 AgentTurn 所需的项目、会话和请求/响应消息。"""
    project = Project(title="AgentTurn 测试")
    db.add(project)
    await db.flush()
    conversation = Conversation(project_id=project.id, title="幂等测试")
    db.add(conversation)
    await db.flush()
    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        content="解释当前项目",
        sequence=1,
    )
    response_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content="当前项目处于草稿阶段",
        sequence=2,
    )
    db.add_all([user_message, response_message])
    await db.flush()
    return project, conversation, user_message, response_message


@pytest.mark.integration
@pytest.mark.asyncio
class TestAgentTurnRepository:
    """AgentTurn 的幂等收据和状态迁移。"""

    async def test_duplicate_answer_turn_returns_original_response_without_second_llm_call(
        self,
        test_session: AsyncSession,
    ) -> None:
        """同一请求重试复用已结束 Turn，不再次执行模拟 Planner。"""
        project, conversation, user_message, response_message = await _seed_turn_context(test_session)
        repo = AgentTurnRepository(test_session)
        planner_calls = 0
        planning_now = datetime.now(UTC)

        turn, created = await repo.get_or_create(
            project_id=project.id,
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            idempotency_key="turn-explain-1",
            request_hash="a" * 64,
        )
        if created:
            planner_calls += 1
            claimed = await repo.claim_planning_lease(
                turn.id,
                lease_owner="worker-a",
                lease_expires_at=planning_now + timedelta(seconds=60),
                now=planning_now,
            )
            assert claimed is not None
            await repo.transition(
                turn.id,
                "answered",
                expected_statuses={"planning"},
                lease_owner="worker-a",
                now=planning_now,
                response_message_id=response_message.id,
                planner_output={"intent": "explain"},
            )

        duplicate, duplicate_created = await repo.get_or_create(
            project_id=project.id,
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            idempotency_key="turn-explain-1",
            request_hash="a" * 64,
        )
        if duplicate_created:
            planner_calls += 1

        assert duplicate.id == turn.id
        assert duplicate.status == "answered"
        assert duplicate.turn_type == "answer"
        assert duplicate.response_message_id == response_message.id
        assert planner_calls == 1

    async def test_reused_key_with_different_hash_is_rejected(
        self,
        test_session: AsyncSession,
    ) -> None:
        """同一幂等键不能掩盖不同 payload。"""
        project, conversation, user_message, _ = await _seed_turn_context(test_session)
        repo = AgentTurnRepository(test_session)
        await repo.get_or_create(
            project_id=project.id,
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            idempotency_key="same-key",
            request_hash="a" * 64,
        )

        with pytest.raises(IdempotencyKeyReusedError):
            await repo.get_or_create(
                project_id=project.id,
                conversation_id=conversation.id,
                user_message_id=user_message.id,
                idempotency_key="same-key",
                request_hash="b" * 64,
            )

    async def test_planning_lease_can_only_be_claimed_after_expiry(
        self,
        test_session: AsyncSession,
    ) -> None:
        """有效租约拒绝第二个 worker，过期后允许原子接管。"""
        project, conversation, user_message, _ = await _seed_turn_context(test_session)
        repo = AgentTurnRepository(test_session)
        turn, _ = await repo.get_or_create(
            project_id=project.id,
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            idempotency_key="lease-key",
            request_hash="c" * 64,
        )
        now = datetime.now(UTC)

        first = await repo.claim_planning_lease(
            turn.id,
            lease_owner="worker-a",
            lease_expires_at=now + timedelta(seconds=120),
            now=now,
        )
        blocked = await repo.claim_planning_lease(
            turn.id,
            lease_owner="worker-b",
            lease_expires_at=now + timedelta(seconds=240),
            now=now + timedelta(seconds=30),
        )
        recovered = await repo.claim_planning_lease(
            turn.id,
            lease_owner="worker-b",
            lease_expires_at=now + timedelta(seconds=360),
            now=now + timedelta(seconds=121),
        )

        assert first is not None
        assert blocked is None
        assert recovered is not None
        assert recovered.planning_lease_owner == "worker-b"
        assert recovered.planning_attempt_count == 2

    async def test_only_valid_lease_owner_can_write_final_response(
        self,
        test_session: AsyncSession,
    ) -> None:
        """非持有者和过期 lease 都不能结束 planning Turn。"""
        project, conversation, user_message, _ = await _seed_turn_context(test_session)
        repo = AgentTurnRepository(test_session)
        turn, _ = await repo.get_or_create(
            project_id=project.id,
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            idempotency_key="lease-write-key",
            request_hash="e" * 64,
        )
        now = datetime.now(UTC)
        claimed = await repo.claim_planning_lease(
            turn.id,
            lease_owner="worker-a",
            lease_expires_at=now + timedelta(seconds=30),
            now=now,
        )
        assert claimed is not None

        with pytest.raises(AgentStateTransitionError, match="lease"):
            await repo.transition(
                turn.id,
                "answered",
                lease_owner="worker-b",
                now=now,
            )
        with pytest.raises(AgentStateTransitionError, match="lease"):
            await repo.transition(
                turn.id,
                "answered",
                lease_owner="worker-a",
                now=now + timedelta(seconds=31),
            )

    async def test_invalid_turn_transition_is_rejected(
        self,
        test_session: AsyncSession,
    ) -> None:
        """终态 Turn 不允许重新进入 planning。"""
        project, conversation, user_message, _ = await _seed_turn_context(test_session)
        repo = AgentTurnRepository(test_session)
        turn = AgentTurn(
            project_id=project.id,
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            idempotency_key="terminal-key",
            request_hash="d" * 64,
            status="answered",
            turn_type="answer",
        )
        test_session.add(turn)
        await test_session.flush()

        with pytest.raises(AgentStateTransitionError):
            await repo.transition(turn.id, "planning")
