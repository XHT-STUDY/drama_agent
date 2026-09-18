"""Memory 生产装配(M-02)——统一 MessageService 工厂与后台摘要调度。

所有真实消息入口(普通消息 API、Agent Turn、Action 生命周期)必须共享
同一套记忆挂载构造(docs/MEMORY_IMPLEMENTATION_PLAN.md §4):

- get_short_term_store():Redis 最近消息窗口(miss 回源 PostgreSQL);
- get_summary_manager():累计摘要 v2 管理器;
- get_message_service():带上述挂载的 MessageService(进程级单例);
- schedule_summary():消息提交后的 best-effort 后台补摘要
  (fire-and-forget asyncio 任务,独立短事务;不占消息响应延迟)。

测试环境(app_env=test)使用 FakeLLM;集成测试通过注入 scheduler /
store 覆盖,不依赖本模块的后台任务。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.core.config import load_settings
from app.memory.short_term import RedisShortTermStore

logger = logging.getLogger(__name__)

_message_service: Any = None
_background_tasks: set[asyncio.Task[None]] = set()

# 后台任务等待消息可见的重试参数(调用方事务提交可能晚于调度)
_VISIBILITY_RETRIES = 10
_VISIBILITY_DELAY_SECONDS = 0.2


def build_short_term_store() -> RedisShortTermStore:
    """按设置构建 Redis 短期记忆窗口。"""
    settings = load_settings()
    return RedisShortTermStore(
        keep_count=settings.short_term_message_count,
        ttl_seconds=settings.short_term_ttl_seconds,
    )


def build_summary_manager() -> Any:
    """构建累计摘要管理器(测试环境 FakeLLM)。"""
    from app.agents.base import BaseAgent
    from app.application.artifact_service import ArtifactService
    from app.core.config import Settings
    from app.domain.summary import ConversationSummaryBody
    from app.llm.fake import FakeLLM
    from app.llm.openai_compatible import OpenAICompatibleLLM
    from app.memory.summary import ConversationSummaryManager
    from app.prompts.loader import PromptLoader

    settings = load_settings()
    if settings.app_env == "test":
        fake = FakeLLM(seed=42)
        fake.register(
            "conversation_summary",
            ConversationSummaryBody(
                summary="测试累计会话摘要:保留主角设定、否决项与未决问题。",
                topics=["测试"],
            ),
        )
        llm: Any = fake
    else:
        llm = OpenAICompatibleLLM(Settings())
    agent = BaseAgent(name="summarizer", llm=llm)
    return ConversationSummaryManager(
        agent,
        PromptLoader(),
        ArtifactService(),
        threshold=settings.conversation_summary_threshold,
        window=settings.short_term_message_count,
    )


def get_message_service() -> Any:
    """带记忆挂载的 MessageService(进程级单例)。

    普通消息 API、Agent Turn 与 Action 生命周期共用此构造——
    任何入口发消息都会写入短期记忆并调度累计摘要(M-02 验收)。
    """
    global _message_service
    if _message_service is None:
        from app.application.conversation_service import MessageService

        _message_service = MessageService(
            short_term_store=build_short_term_store(),
            summary_scheduler=schedule_summary,
        )
    return _message_service


def reset_message_service() -> None:
    """重置单例(仅测试装配用)。"""
    global _message_service
    _message_service = None


# ---- 后台摘要调度(post-commit best effort) ----


def schedule_summary(conversation_id: uuid.UUID, message_count: int) -> None:
    """调度一次后台累计摘要(fire-and-forget;不阻塞消息响应)。

    消息可能尚未提交(调用方事务未结束),后台任务轮询等待其可见,
    超时则记录摘要缺口——下一次触发或创作 Run 会按
    covered_to_sequence 补齐(设计 §8.1)。
    """
    task = asyncio.create_task(
        _summarize_in_background(conversation_id, message_count)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _summarize_in_background(
    conversation_id: uuid.UUID, message_count: int
) -> None:
    from app.db.session import get_session_factory

    manager = build_summary_manager()
    try:
        factory = get_session_factory()
    except RuntimeError:
        logger.warning("后台摘要跳过:数据库会话工厂未初始化")
        return
    # 1) 等待调用方事务提交(消息可见);会话被删除则放弃
    visible = False
    for _ in range(_VISIBILITY_RETRIES):
        try:
            async with factory() as session:
                from app.db.models.conversation import Conversation

                exists = (
                    await session.execute(
                        select(Conversation.id).where(
                            Conversation.id == conversation_id
                        )
                    )
                ).scalar_one_or_none()
                if exists is None:
                    logger.info(
                        "后台摘要放弃:会话已删除(conversation=%s)",
                        conversation_id,
                    )
                    return
                count = await manager.current_message_count(
                    session, conversation_id
                )
            if count >= message_count:
                visible = True
                break
        except Exception:  # noqa: BLE001 — best effort
            logger.exception(
                "后台摘要可见性检查失败(conversation=%s)", conversation_id
            )
            return
        await asyncio.sleep(_VISIBILITY_DELAY_SECONDS)
    if not visible:
        logger.warning(
            "后台摘要放弃等待消息可见(conversation=%s, expected>=%s)"
            "——保留缺口,下次触发或创作 Run 补齐",
            conversation_id, message_count,
        )
        return
    # 2) 独立短事务补摘要(阈值语义;失败保留缺口)
    try:
        async with factory() as session:
            await manager.maybe_summarize(session, conversation_id)
            await session.commit()
    except Exception:  # noqa: BLE001 — best effort
        logger.exception(
            "后台累计摘要失败(conversation=%s),保留缺口待下次补做",
            conversation_id,
        )


async def wait_for_background_summaries(timeout: float = 5.0) -> None:
    """等待所有已调度的后台摘要完成(测试辅助)。"""
    tasks = list(_background_tasks)
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)
