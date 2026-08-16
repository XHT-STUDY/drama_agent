"""G-01 中期记忆（会话摘要）与短期记忆集成测试。

前置条件：Docker PostgreSQL + Redis 就绪（make up），FakeLLM 驱动。

覆盖：
- 消息数达阈值生成 ConversationSummary Artifact（覆盖区间连续、不重叠）；
- 摘要失败不阻断消息保存（验收）；
- Redis 清空后从 DB 恢复最近消息（验收「Redis 丢失不丢消息」）。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.application.artifact_service import ArtifactService
from app.application.conversation_service import MessageService
from app.domain.conversation import MessageCreate
from app.domain.enums import ArtifactType
from app.llm.fake import FakeLLM
from app.memory.short_term import RedisShortTermStore
from app.memory.summary import ConversationSummaryManager
from app.prompts.loader import PromptLoader


async def _insert_messages(db: Any, conversation_id: uuid.UUID, count: int) -> None:
    """在事实源（Message 表）插入 count 条递增消息。"""
    from app.db.models.message import Message

    for seq in range(1, count + 1):
        db.add(
            Message(
                conversation_id=conversation_id,
                role="user" if seq % 2 else "assistant",
                content=f"第 {seq} 条消息",
                sequence=seq,
            )
        )
    await db.flush()


# ========================================================================
# 会话摘要管理器
# ========================================================================


@pytest.mark.integration
class TestConversationSummaryManager:
    """摘要触发条件与覆盖区间。"""

    async def test_summarizes_when_threshold_reached(
        self,
        db_session: Any,
        test_conversation: uuid.UUID,
        agent: Any,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
    ) -> None:
        """threshold=3 时第 3 条消息后生成摘要，覆盖 [1..2]。"""
        manager = ConversationSummaryManager(
            agent, prompt_loader, artifact_service, threshold=3, window=1
        )
        await _insert_messages(db_session, test_conversation, 3)

        artifact = await manager.maybe_summarize(
            db_session, test_conversation, message_count=3
        )

        assert artifact is not None
        content = artifact.content
        assert content["conversation_id"] == str(test_conversation)
        assert content["covered_from_sequence"] == 1
        assert content["covered_to_sequence"] == 2
        assert content["message_count"] == 2
        assert content["summary"]  # 来自 FakeLLM fixture
        assert content["topics"]

    async def test_summaries_are_continuous_no_overlap(
        self,
        db_session: Any,
        test_conversation: uuid.UUID,
        agent: Any,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
    ) -> None:
        """多次触发时覆盖区间连续且不重叠（1..2 → 3..5）。"""
        manager = ConversationSummaryManager(
            agent, prompt_loader, artifact_service, threshold=3, window=1
        )
        await _insert_messages(db_session, test_conversation, 6)

        a1 = await manager.maybe_summarize(db_session, test_conversation, message_count=3)
        a2 = await manager.maybe_summarize(db_session, test_conversation, message_count=6)

        assert a1 is not None and a2 is not None
        assert (a1.content["covered_from_sequence"], a1.content["covered_to_sequence"]) == (1, 2)
        assert (a2.content["covered_from_sequence"], a2.content["covered_to_sequence"]) == (3, 5)

        # 两者 input_hash 不同（dedup_extra 含 covered_to），都是独立记录
        assert a1.id != a2.id

    async def test_below_threshold_returns_none(
        self,
        db_session: Any,
        test_conversation: uuid.UUID,
        agent: Any,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
    ) -> None:
        """消息数未达阈值返回 None，不生成摘要。"""
        manager = ConversationSummaryManager(
            agent, prompt_loader, artifact_service, threshold=3, window=1
        )
        await _insert_messages(db_session, test_conversation, 2)

        artifact = await manager.maybe_summarize(db_session, test_conversation, message_count=2)
        assert artifact is None

    async def test_projects_do_not_mix_memory(
        self,
        db_session: Any,
        agent: Any,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
    ) -> None:
        """验收：项目切换不串记忆——不同项目的会话摘要互不可见。"""
        from app.db.models.conversation import Conversation
        from app.db.models.project import Project

        # 项目 A 与会话 A
        proj_a = uuid.uuid4()
        db_session.add(Project(id=proj_a, title="项目 A", status="draft"))
        conv_a = uuid.uuid4()
        db_session.add(Conversation(id=conv_a, project_id=proj_a, title="会话 A"))
        # 项目 B 与会话 B
        proj_b = uuid.uuid4()
        db_session.add(Project(id=proj_b, title="项目 B", status="draft"))
        conv_b = uuid.uuid4()
        db_session.add(Conversation(id=conv_b, project_id=proj_b, title="会话 B"))
        await db_session.flush()

        manager = ConversationSummaryManager(
            agent, prompt_loader, artifact_service, threshold=3, window=1
        )
        await _insert_messages(db_session, conv_a, 3)
        await _insert_messages(db_session, conv_b, 3)

        a_artifact = await manager.maybe_summarize(db_session, conv_a, message_count=3)
        b_artifact = await manager.maybe_summarize(db_session, conv_b, message_count=3)

        assert a_artifact is not None and b_artifact is not None
        # 各摘要归属于各自项目
        assert a_artifact.project_id == proj_a
        assert b_artifact.project_id == proj_b
        # 互相不串：A 的摘要只覆盖 A 的消息，B 同理
        assert a_artifact.content["conversation_id"] == str(conv_a)
        assert b_artifact.content["conversation_id"] == str(conv_b)


# ========================================================================
# 摘要失败不阻断消息保存
# ========================================================================


@pytest.mark.integration
class TestSummaryFailureDoesNotBlockMessage:
    """验收：摘要生成失败只 log，消息照常保存。"""

    async def test_message_saved_when_summary_manager_raises(
        self,
        db_session: Any,
        test_conversation: uuid.UUID,
        artifact_service: ArtifactService,
    ) -> None:
        """FakeLLM 未注册 conversation_summary → 摘要失败 → 消息仍落库。"""
        # 未注册 conversation_summary 的 FakeLLM，_generate 会抛 RuntimeError
        llm = FakeLLM(seed=1)
        from app.agents.base import BaseAgent

        agent = BaseAgent(name="summarizer", llm=llm)
        bad_manager = ConversationSummaryManager(
            agent,
            PromptLoader(),
            artifact_service,
            threshold=2,
            window=1,
        )
        from app.memory.short_term import InMemoryShortTermStore

        svc = MessageService(
            short_term_store=InMemoryShortTermStore(keep_count=12),
            summary_manager=bad_manager,
        )

        # threshold=2：第 2 条消息会触发摘要 → 失败
        resp = await svc.append(
            db_session,
            test_conversation,
            MessageCreate(role="user", content="第一条"),
        )
        assert resp.sequence == 1
        resp2 = await svc.append(
            db_session,
            test_conversation,
            MessageCreate(role="user", content="第二条触发摘要失败"),
        )
        assert resp2.sequence == 2

        # 消息仍在事实源
        from sqlalchemy import select

        from app.db.models.message import Message
        from app.db.repositories.base import BaseRepository

        repo = BaseRepository(db_session, Message)
        items = await repo.list(conversation_id=test_conversation)
        assert len(items) == 2

        # 未产生任何摘要 Artifact（摘要失败不落库）
        from app.db.models.conversation import Conversation

        project_id = (
            await db_session.execute(
                select(Conversation.project_id).where(Conversation.id == test_conversation)
            )
        ).scalar_one()
        summaries = await artifact_service.list_by_project(
            db_session, project_id, artifact_type=ArtifactType.CONVERSATION_SUMMARY
        )
        assert summaries["total"] == 0


# ========================================================================
# Redis 清空后从 DB 恢复
# ========================================================================


@pytest.mark.integration
class TestRedisShortTermRecovery:
    """验收：Redis 丢失不丢消息，recent 从 DB 恢复。"""

    async def test_recent_recovers_from_db_after_redis_cleared(
        self,
        db_session: Any,
        test_conversation: uuid.UUID,
        redis_client: Any,
    ) -> None:
        """写入 Redis 后清空 key，recent 从 Message 表恢复最近 N 条。"""
        store = RedisShortTermStore(
            keep_count=12,
            ttl_seconds=3600,
            redis_client=redis_client,
        )
        # 1) 事实源落库
        await _insert_messages(db_session, test_conversation, 5)
        # 2) 短期记忆写入 Redis
        for seq in range(1, 6):
            await store.push(
                db_session,
                test_conversation,
                role="user" if seq % 2 else "assistant",
                content=f"第 {seq} 条消息",
                sequence=seq,
            )

        key = RedisShortTermStore._key(test_conversation)
        assert await redis_client.exists(key) == 1

        # 3) 模拟 Redis 清空
        await redis_client.delete(key)

        # 4) recent 从 DB 恢复最近 3 条（升序）
        recovered = await store.recent(db_session, test_conversation, 3)
        assert [m.sequence for m in recovered] == [3, 4, 5]
        assert recovered[0].content == "第 3 条消息"
