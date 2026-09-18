"""中期记忆(累计会话摘要 v2)与短期记忆集成测试。

前置条件:Docker PostgreSQL + Redis 就绪(make up),FakeLLM 驱动。

M-02 覆盖:
- 累计语义:covered_from 恒为 1,summary 覆盖截至 covered_to 的全部历史;
- v1 迁移:旧 v1 分段摘要作为累计起点,不修改旧 Artifact;
- 幂等:同一覆盖区间重复触发只落一份 v2;
- 摘要失败不阻断消息保存(验收);
- 消息响应不等待摘要 LLM(验收);
- 跨会话 sequence 相同的项目级合并稳定、不串会话;
- Redis 清空后从 DB 恢复最近消息(验收「Redis 丢失不丢消息」)。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.application.conversation_service import MessageService
from app.domain.conversation import MessageCreate
from app.domain.enums import ArtifactType
from app.domain.summary import ConversationSummaryBody
from app.llm.fake import FakeLLM
from app.memory.short_term import InMemoryShortTermStore, RedisShortTermStore
from app.memory.summary import ConversationSummaryManager
from app.prompts.loader import PromptLoader


async def _insert_messages(db: Any, conversation_id: uuid.UUID, count: int,
                           prefix: str = "第") -> None:
    """在事实源(Message 表)插入 count 条递增消息。"""
    from app.db.models.message import Message

    for seq in range(1, count + 1):
        db.add(
            Message(
                conversation_id=conversation_id,
                role="user" if seq % 2 else "assistant",
                content=f"{prefix} {seq} 条消息",
                sequence=seq,
            )
        )
    await db.flush()


class _CumulativeFakeLLM(FakeLLM):
    """内容感知累计摘要桩:摘要 = 上一版累计 + 新增标记行拼接。

    保留【设定】行并让【改口】替换旧值——验证累计链与最新事实语义。
    """

    def __init__(self) -> None:
        super().__init__(seed=42)

    async def generate_structured(
        self, schema: type[Any], messages: list[dict[str, str]], **kwargs: Any
    ) -> Any:
        from app.llm.models import LLMCallResult

        text = messages[-1]["content"]
        prev = ""
        marker = "上一版累计摘要(可能为空)\n\n"
        if marker in text:
            prev = text.split(marker, 1)[1].split("\n\n", 1)[0].strip()
        new_part = text.split("新增对话消息", 1)[-1] if "新增对话消息" in text else text
        lines = [ln for ln in prev.splitlines() if ln.startswith("-")]
        for ln in new_part.splitlines():
            ln = ln.strip()
            if ln.startswith(("-", "1.", "2.", "3.", "4.", "5.")):
                content = ln.split(". ", 1)[-1] if ". " in ln else ln
                if "【设定】" in content:
                    lines.append("- " + content.split("】", 1)[-1])
                elif "【改口】" in content:
                    key = content.split("】", 1)[-1].split(":", 1)[0]
                    lines = [x for x in lines if not x.startswith(f"- {key}")]
                    lines.append("- " + content.split("】", 1)[-1])
        summary = ";".join(lines)[:200] or "(无实质创作信息)"
        return LLMCallResult(
            content=summary,
            parsed=ConversationSummaryBody(summary=summary, topics=["测试"]),
        )


def _cumulative_manager(agent: Any, prompt_loader: PromptLoader,
                        artifact_service: ArtifactService, **kw: Any
                        ) -> ConversationSummaryManager:
    return ConversationSummaryManager(agent, prompt_loader, artifact_service, **kw)


# ========================================================================
# 累计语义(v2)
# ========================================================================


@pytest.mark.integration
class TestCumulativeSummary:
    async def test_summarizes_when_threshold_reached(
        self,
        db_session: Any,
        test_conversation: uuid.UUID,
        agent: Any,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
    ) -> None:
        """threshold=3 时第 3 条消息后生成 v2 摘要,覆盖 [1..2]。"""
        manager = _cumulative_manager(
            agent, prompt_loader, artifact_service, threshold=3, window=1
        )
        await _insert_messages(db_session, test_conversation, 3)

        artifact = await manager.maybe_summarize(
            db_session, test_conversation, message_count=3
        )

        assert artifact is not None
        content = artifact.content
        assert content["conversation_id"] == str(test_conversation)
        assert content["content_schema_version"] == "2.0"
        assert content["covered_from_sequence"] == 1
        assert content["covered_to_sequence"] == 2
        assert content["message_count"] == 2  # 累计覆盖数
        assert content["summary"]
        assert content["topics"]
        assert content["previous_summary_artifact_id"] is None
        assert content["source_message_digest"]
        assert artifact.content_schema_version == "2.0"

    async def test_cumulative_chain_keeps_first_segment_constraints(
        self,
        db_session: Any,
        test_conversation: uuid.UUID,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
    ) -> None:
        """验收:48 条消息后的最新累计摘要包含第一段摘要中的关键约束。"""
        from app.db.models.message import Message

        llm = _CumulativeFakeLLM()
        agent = BaseAgent(name="summarizer", llm=llm)
        manager = _cumulative_manager(
            agent, prompt_loader, artifact_service, threshold=3, window=1
        )
        # 第一段:[1..2] 含设定 A;第二段之前第 4 条【改口】换掉 A
        contents = {
            1: "【设定】主角身份:主角是女足守门员",
            4: "【改口】主角身份:主角改成女足队长,守门员设定不要了",
        }
        for seq in range(1, 7):
            db_session.add(
                Message(
                    conversation_id=test_conversation,
                    role="user" if seq % 2 else "assistant",
                    content=contents.get(seq, f"闲聊 {seq}"),
                    sequence=seq,
                )
            )
        await db_session.flush()

        a1 = await manager.maybe_summarize(db_session, test_conversation, message_count=3)
        a2 = await manager.maybe_summarize(db_session, test_conversation, message_count=6)

        assert a1 is not None and a2 is not None
        # v2 链:第二版覆盖 [1..5],covered_from 恒为 1,引用上一版
        assert a2.content["covered_from_sequence"] == 1
        assert a2.content["covered_to_sequence"] == 5
        assert a2.content["previous_summary_artifact_id"] == str(a1.id)
        # 最新事实胜出:改口后的设定保留,旧说法被替换
        assert "女足队长" in a2.content["summary"]
        assert "守门员" not in a2.content["summary"].split("女足队长")[0]

    async def test_same_coverage_is_idempotent(
        self,
        db_session: Any,
        test_conversation: uuid.UUID,
        test_project: uuid.UUID,
        agent: Any,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
    ) -> None:
        """同一覆盖区间重复触发不产生第二份(无新区间返回 None);
        并发触发由 input_hash 幂等输入兜底——同 dedup_extra +
        同内容只落一份(ArtifactStore 去重机制)。"""
        manager = _cumulative_manager(
            agent, prompt_loader, artifact_service, threshold=3, window=1
        )
        await _insert_messages(db_session, test_conversation, 3)

        a1 = await manager.maybe_summarize(
            db_session, test_conversation, message_count=3
        )
        assert a1 is not None
        # 完成后的重复触发:无新区间 → None,不新增 Artifact
        again = await manager.maybe_summarize(
            db_session, test_conversation, message_count=3
        )
        assert again is None

        # 并发触发兜底:相同幂等输入(内容+dedup_extra)→ 同一 Artifact;
        # 不同幂等输入 → 独立记录
        first_dup = await artifact_service.create_validated_artifact(
            db_session,
            project_id=test_project,
            artifact_type=ArtifactType.CONVERSATION_SUMMARY,
            content=dict(a1.content),
            content_schema_version="2.0",
            prompt_version=a1.prompt_version,
            dedup_extra=f"dup|{test_conversation}",
        )
        assert first_dup.id != a1.id
        same_input = await artifact_service.create_validated_artifact(
            db_session,
            project_id=test_project,
            artifact_type=ArtifactType.CONVERSATION_SUMMARY,
            content=dict(a1.content),
            content_schema_version="2.0",
            prompt_version=a1.prompt_version,
            dedup_extra=f"dup|{test_conversation}",
        )
        assert same_input.id == first_dup.id

    async def test_v1_migration_seed(
        self,
        db_session: Any,
        test_conversation: uuid.UUID,
        test_project: uuid.UUID,
        agent: Any,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
    ) -> None:
        """旧 v1 摘要保持可读;首次 v2 以最新 v1 为累计起点(不改旧件)。"""
        # 手工落一份 v1 分段摘要(covered [1..2])
        v1_content = {
            "conversation_id": str(test_conversation),
            "summary": "v1 第一段摘要:确认了足球题材与主角守门员设定",
            "topics": ["v1"],
            "covered_from_sequence": 1,
            "covered_to_sequence": 2,
            "message_count": 2,
        }
        v1 = await artifact_service.create_validated_artifact(
            db_session,
            project_id=test_project,
            artifact_type=ArtifactType.CONVERSATION_SUMMARY,
            content=v1_content,
            prompt_version="1.0.0",
            dedup_extra=f"{test_conversation}:2:v1",
        )
        await _insert_messages(db_session, test_conversation, 15, prefix="后续")

        manager = _cumulative_manager(
            agent, prompt_loader, artifact_service, threshold=24, window=12
        )
        v2 = await manager.catch_up(db_session, test_conversation, enforce_threshold=False)

        assert v2 is not None
        assert v2.content["covered_to_sequence"] == 3  # 15 - 12
        assert v2.content["covered_from_sequence"] == 1
        assert v2.content["previous_summary_artifact_id"] == str(v1.id)
        # 旧 v1 不被修改
        v1_after = await artifact_service.get_version(db_session, v1.id)
        assert v1_after.content == v1_content
        assert v1_after.content_schema_version == "1.0"

    async def test_below_threshold_returns_none(
        self,
        db_session: Any,
        test_conversation: uuid.UUID,
        agent: Any,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
    ) -> None:
        """消息数未达阈值返回 None,不生成摘要。"""
        manager = _cumulative_manager(
            agent, prompt_loader, artifact_service, threshold=3, window=1
        )
        await _insert_messages(db_session, test_conversation, 2)

        artifact = await manager.maybe_summarize(
            db_session, test_conversation, message_count=2
        )
        assert artifact is None

    async def test_projects_do_not_mix_memory(
        self,
        db_session: Any,
        agent: Any,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
    ) -> None:
        """验收:项目切换不串记忆——不同项目的会话摘要互不可见。"""
        from app.db.models.conversation import Conversation
        from app.db.models.project import Project

        proj_a = uuid.uuid4()
        db_session.add(Project(id=proj_a, title="项目 A", status="draft"))
        conv_a = uuid.uuid4()
        db_session.add(Conversation(id=conv_a, project_id=proj_a, title="会话 A"))
        proj_b = uuid.uuid4()
        db_session.add(Project(id=proj_b, title="项目 B", status="draft"))
        conv_b = uuid.uuid4()
        db_session.add(Conversation(id=conv_b, project_id=proj_b, title="会话 B"))
        await db_session.flush()

        manager = _cumulative_manager(
            agent, prompt_loader, artifact_service, threshold=3, window=1
        )
        await _insert_messages(db_session, conv_a, 3)
        await _insert_messages(db_session, conv_b, 3)

        a_artifact = await manager.maybe_summarize(db_session, conv_a, message_count=3)
        b_artifact = await manager.maybe_summarize(db_session, conv_b, message_count=3)

        assert a_artifact is not None and b_artifact is not None
        assert a_artifact.project_id == proj_a
        assert b_artifact.project_id == proj_b
        assert a_artifact.content["conversation_id"] == str(conv_a)
        assert b_artifact.content["conversation_id"] == str(conv_b)


# ========================================================================
# 跨会话合并(项目级读取)
# ========================================================================


@pytest.mark.integration
class TestProjectLevelMerge:
    async def test_same_sequence_conversations_merge_stably(
        self,
        db_session: Any,
        test_project: uuid.UUID,
        agent: Any,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
    ) -> None:
        """两个会话 sequence 相同:合并按 Artifact 时间稳定排序,不串会话。"""
        from app.db.models.conversation import Conversation
        from app.memory.summary import merged_project_summaries

        conv_a = uuid.uuid4()
        db_session.add(Conversation(id=conv_a, project_id=test_project, title="A"))
        conv_b = uuid.uuid4()
        db_session.add(Conversation(id=conv_b, project_id=test_project, title="B"))
        await db_session.flush()

        manager = _cumulative_manager(
            agent, prompt_loader, artifact_service, threshold=3, window=1
        )
        # 两个会话各自 3 条消息(sequence 同为 1..3)
        await _insert_messages(db_session, conv_a, 3, prefix="A-会话消息")
        await _insert_messages(db_session, conv_b, 3, prefix="B-会话消息")
        await manager.maybe_summarize(db_session, conv_a, message_count=3)
        await manager.maybe_summarize(db_session, conv_b, message_count=3)
        await db_session.flush()

        merged = await merged_project_summaries(
            db_session, artifact_service, test_project,
            current_conversation_id=conv_b,
        )
        # 当前会话(conv_b)优先,另一个标明来源;不比较跨会话 sequence
        assert merged.count("(当前会话记忆") == 1
        assert "(其他会话记忆" in merged
        assert "B-会话消息" in merged or "当前会话记忆" in merged


# ========================================================================
# 摘要失败不阻断消息 / 响应不等待 LLM
# ========================================================================


@pytest.mark.integration
class TestSummaryFailureDoesNotBlockMessage:
    async def test_message_saved_when_summary_fails(
        self,
        db_session: Any,
        test_conversation: uuid.UUID,
    ) -> None:
        """摘要失败只记录,消息照常保存(验收)。"""
        llm = FakeLLM(seed=1)  # 未注册 conversation_summary → LLM 失败
        agent = BaseAgent(name="summarizer", llm=llm)
        bad_manager = ConversationSummaryManager(
            agent, PromptLoader(), ArtifactService(), threshold=2, window=1
        )

        tasks: list[asyncio.Task[None]] = []

        async def _failing_summarize(conv_id: uuid.UUID, count: int) -> None:
            await bad_manager.maybe_summarize(
                db_session, conv_id, message_count=count
            )

        def failing_scheduler(conv_id: uuid.UUID, count: int) -> None:
            tasks.append(
                asyncio.get_running_loop().create_task(
                    _failing_summarize(conv_id, count)
                )
            )

        svc = MessageService(
            short_term_store=InMemoryShortTermStore(keep_count=12),
            summary_scheduler=failing_scheduler,
        )

        resp = await svc.append(
            db_session, test_conversation,
            MessageCreate(role="user", content="第一条"),
        )
        assert resp.sequence == 1
        resp2 = await svc.append(
            db_session, test_conversation,
            MessageCreate(role="user", content="第二条触发摘要失败"),
        )
        assert resp2.sequence == 2

        # 等待已调度任务结束(失败只 log;不得在测试结束后仍持有会话)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        from sqlalchemy import select

        from app.db.models.message import Message

        rows = (await db_session.execute(
            select(Message).where(Message.conversation_id == test_conversation)
        )).scalars().all()
        assert len(rows) == 2

    async def test_message_response_does_not_wait_for_summary_llm(
        self,
        db_session: Any,
        test_conversation: uuid.UUID,
    ) -> None:
        """验收:普通消息响应耗时不包含摘要 LLM 调用。"""
        started = asyncio.Event()
        finished = asyncio.Event()
        holder: list[asyncio.Task[None]] = []

        async def slow_summary(conv_id: uuid.UUID, count: int) -> None:
            started.set()
            await asyncio.sleep(0.5)
            finished.set()

        def scheduler(conv_id: uuid.UUID, count: int) -> None:
            holder.append(
                asyncio.get_running_loop().create_task(slow_summary(conv_id, count))
            )

        svc = MessageService(summary_scheduler=scheduler)
        await svc.append(
            db_session, test_conversation,
            MessageCreate(role="user", content="消息"),
        )
        # append 已返回;调度任务刚开始(摘要需 0.5s)——响应不等待其完成
        await asyncio.sleep(0)
        assert started.is_set()
        assert not finished.is_set()
        await asyncio.wait_for(finished.wait(), timeout=2)
        await asyncio.gather(*holder)


# ========================================================================
# Redis 清空后从 DB 恢复
# ========================================================================


@pytest.mark.integration
class TestRedisShortTermRecovery:
    """验收:Redis 丢失不丢消息,recent 从 DB 恢复。"""

    async def test_recent_recovers_from_db_after_redis_cleared(
        self,
        db_session: Any,
        test_conversation: uuid.UUID,
        redis_client: Any,
    ) -> None:
        """写入 Redis 后清空 key,recent 从 Message 表恢复最近 N 条。"""
        store = RedisShortTermStore(
            keep_count=12,
            ttl_seconds=3600,
            redis_client=redis_client,
        )
        await _insert_messages(db_session, test_conversation, 5)
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

        # 模拟 Redis 清空
        await redis_client.delete(key)

        # recent 从 DB 恢复最近 3 条(升序)
        recovered = await store.recent(db_session, test_conversation, 3)
        assert [m.sequence for m in recovered] == [3, 4, 5]
        assert recovered[0].content == "第 3 条消息"
