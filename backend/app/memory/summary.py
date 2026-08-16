"""会话摘要管理器（G-01 中期记忆）。

当会话消息数达到 threshold 的整数倍时，把"超出短期窗口"的旧消息
生成 ConversationSummary Artifact（项目记忆指针），供后续创作读取。

设计要点：
- 服务端回填确定性字段（conversation_id / covered_from/to / message_count），
  LLM 只产出 summary + topics；
- 摘要失败只 log 不阻断消息保存（验收）；
- 每次只摘要"上一段摘要之后"的新消息（covered_from = 上次 covered_to + 1），
  避免重复摘要、控制成本；
- PostgreSQL 消息表是事实源，即使摘要缺失也可从消息重建。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactResponse, ArtifactService
from app.core.config import load_settings
from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.domain.enums import ArtifactType
from app.domain.summary import ConversationSummary, ConversationSummaryBody
from app.memory.short_term import ShortTermMessage
from app.prompts.loader import PromptLoader

logger = logging.getLogger(__name__)


class ConversationSummaryManager:
    """会话摘要生成管理器（G-01）。

    构造参数注入（便于测试）：threshold / window 缺省时读 settings。
    调用方（MessageService.append）捕获异常——摘要失败不阻断消息保存。
    """

    def __init__(
        self,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
        *,
        threshold: int | None = None,
        window: int | None = None,
    ) -> None:
        settings = load_settings()
        self._agent = agent
        self._prompt_loader = prompt_loader
        self._artifact_service = artifact_service
        self._threshold = threshold or settings.conversation_summary_threshold
        self._window = window or settings.short_term_message_count

    # ---- 对外入口 ----

    async def maybe_summarize(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        *,
        message_count: int,
    ) -> ArtifactResponse | None:
        """消息数达到阈值时生成会话摘要并落库；未达阈值返回 None。

        Args:
            db: 调用方事务会话
            conversation_id: 目标会话
            message_count: 当前会话消息总数（append 时等于 next_sequence）

        Returns:
            落库的 ArtifactResponse；无需摘要或摘要失败（调用方兜底捕获）返回 None。
        """
        if message_count < self._threshold or message_count % self._threshold != 0:
            return None

        covered_to = message_count - self._window
        if covered_to < 1:
            # 窗口尚未被填满，没有"旧消息"可摘
            return None

        covered_from = await self._next_covered_from(db, conversation_id, covered_to)
        if covered_from > covered_to:
            return None

        messages = await self._load_messages(db, conversation_id, covered_from, covered_to)
        if not messages:
            return None

        body = await self._generate(db, conversation_id, messages)

        project_id = await self._get_project_id(db, conversation_id)
        if project_id is None:
            logger.warning(
                "会话 %s 无项目归属，跳过摘要落库（conversation 应已级联删除）",
                conversation_id,
            )
            return None

        summary = ConversationSummary(
            conversation_id=str(conversation_id),
            summary=body.summary,
            topics=body.topics,
            covered_from_sequence=covered_from,
            covered_to_sequence=covered_to,
            message_count=len(messages),
        )
        return await self._artifact_service.create_validated_artifact(
            db,
            project_id=project_id,
            artifact_type=ArtifactType.CONVERSATION_SUMMARY,
            episode_number=1,
            content=summary.model_dump(),
            prompt_version="1.0.0",
            # 同一会话同覆盖区间多次触发会幂等去重（D-05 的 dedup 机制）
            dedup_extra=f"{conversation_id}:{covered_to}",
        )

    # ---- 内部步骤 ----

    async def _next_covered_from(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        covered_to: int,
    ) -> int:
        """推断本次摘要的起始序号（上次 covered_to + 1；无则从 1）。"""
        last = await self._latest_for_conversation(db, conversation_id)
        if last is None:
            return 1
        prev_covered_to: int = last["content"].get("covered_to_sequence", 0)
        return prev_covered_to + 1

    async def _latest_for_conversation(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """取指定会话最新一条摘要 Artifact（按 covered_to_sequence 取最大）。"""
        project_id = await self._get_project_id(db, conversation_id)
        if project_id is None:
            return None
        page = await self._artifact_service.list_by_project(
            db,
            project_id,
            artifact_type=ArtifactType.CONVERSATION_SUMMARY,
            offset=0,
            limit=100,
        )
        items = cast("list[dict[str, Any]]", page["items"])
        matched = [
            item
            for item in items
            if item["content"].get("conversation_id") == str(conversation_id)
        ]
        if not matched:
            return None
        return max(
            matched, key=lambda it: it["content"].get("covered_to_sequence", 0)
        )

    async def _load_messages(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        start: int,
        end: int,
    ) -> list[ShortTermMessage]:
        """从事实源（Message 表）加载 [start, end] 区间消息（升序）。"""
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .where(Message.sequence >= start, Message.sequence <= end)
            .order_by(Message.sequence.asc())
        )
        result = await db.execute(stmt)
        rows = list(result.scalars().all())
        return [
            ShortTermMessage(role=r.role, content=r.content, sequence=r.sequence)
            for r in rows
        ]

    async def _generate(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        messages: list[ShortTermMessage],
    ) -> ConversationSummaryBody:
        """渲染模板 + 调用 LLM，返回校验通过的摘要体。"""
        transcript = "\n".join(
            f"{m.sequence}. [{m.role}] {m.content}" for m in messages
        )
        tpl = self._prompt_loader.get("conversation_summary")
        rendered = tpl.render(
            conversation_transcript=transcript,
            message_count=str(len(messages)),
        )
        result = await self._agent.generate_structured(
            ConversationSummaryBody,
            [{"role": "user", "content": rendered}],
            prompt_name="conversation_summary",
            temperature=0.3,
        )
        if result.error_code or result.parsed is None:
            logger.error(
                "会话摘要 LLM 失败: code=%s detail=%s",
                result.error_code,
                result.error_detail,
            )
            raise RuntimeError(
                f"会话摘要生成失败: {result.error_code} - {result.error_detail}"
            )
        return cast(ConversationSummaryBody, result.parsed)

    async def _get_project_id(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """反查会话所属项目（会话已删除返回 None）。"""
        result = await db.execute(
            select(Conversation.project_id).where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none()


async def latest_project_summary_text(
    db: AsyncSession,
    artifact_service: Any,
    project_id: uuid.UUID,
) -> str:
    """取项目最新会话摘要文本（G-02 集成点——「旧会话优先摘要」）。

    跨会话取 covered_to_sequence 最大的一条摘要作为最新创作背景；
    无摘要返回空串。供 write_episode 节点组装 previous_summary_continuity。
    """
    page = await artifact_service.list_by_project(
        db,
        project_id,
        artifact_type=ArtifactType.CONVERSATION_SUMMARY,
        offset=0,
        limit=100,
    )
    items = cast("list[dict[str, Any]]", page["items"])
    if not items:
        return ""
    latest = max(
        items, key=lambda it: it["content"].get("covered_to_sequence", 0)
    )
    summary = latest["content"].get("summary", "")
    return summary if isinstance(summary, str) else ""
