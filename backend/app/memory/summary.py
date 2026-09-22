"""会话摘要管理器(G-01;M-02 升级累计语义 v2)。

v2 契约(docs/MEMORY_DESIGN.md §7.3、§8.1):
- summary_k = summarize(summary_{k-1}, messages[new_from:new_to]),
  covered_from 通常为 1,summary 含截至 covered_to 的全部历史意图;
- 摘要 Artifact 记录 content_schema_version / previous_summary_artifact_id /
  source_message_digest;幂等输入 = 会话 + 覆盖终点 + 上一摘要 +
  Prompt 版本 + 新增消息 digest(ArtifactStore input_hash 去重);
- 同一会话按 covered_to_sequence、Artifact 版本、创建时间稳定选择;
  跨会话不得比较 sequence——项目级读取用有界合并;
- v1 摘要保持可读;首次生成 v2 以最新 v1 摘要 + 后续消息为迁移输入;
- 摘要失败不阻断消息保存;调用方决定何时补做(catch_up)。
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any, cast

from sqlalchemy import func, select
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

# 项目级合并时,除当前会话外最多携带的其他会话摘要数
_MAX_OTHER_CONVERSATIONS = 2


def summary_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    """同会话内稳定排序键:covered_to → Artifact 版本 → 创建时间。"""
    content = item.get("content") or {}
    return (
        int(content.get("covered_to_sequence", 0) or 0),
        int(item.get("version", 0) or 0),
        str(item.get("created_at", "")),
    )


class ConversationSummaryManager:
    """累计会话摘要生成管理器(v2)。

    构造参数注入(便于测试):threshold / window 缺省读 settings。
    摘要调用应在消息事务提交后进行(响应关键路径之外);
    失败只 log,由下一次触发或创作 Run 补做。
    """

    def __init__(
        self,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
        *,
        threshold: int | None = None,
        window: int | None = None,
        prompt_name: str = "conversation_summary",
    ) -> None:
        settings = load_settings()
        self._agent = agent
        self._prompt_loader = prompt_loader
        self._artifact_service = artifact_service
        self._threshold = threshold or settings.conversation_summary_threshold
        self._window = window or settings.short_term_message_count
        self._prompt_name = prompt_name
        self._template = prompt_loader.get(prompt_name)
        self._prompt_version = self._template.version

    # ---- 对外入口 ----

    async def maybe_summarize(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        *,
        message_count: int | None = None,
    ) -> ArtifactResponse | None:
        """阈值触发入口:消息数达到阈值整数倍时补齐累计摘要。

        兼容旧调用签名;阈值未到返回 None。摘要失败抛出由调用方兜底,
        不影响已提交的消息。
        """
        if message_count is None:
            message_count = await self._message_count(db, conversation_id)
        if message_count < self._threshold or message_count % self._threshold != 0:
            return None
        return await self.catch_up(
            db, conversation_id, message_count=message_count,
            enforce_threshold=False,
        )

    async def catch_up(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        *,
        message_count: int | None = None,
        enforce_threshold: bool = True,
    ) -> ArtifactResponse | None:
        """把累计摘要补齐到当前可覆盖范围(幂等;无新区间返回 None)。

        Args:
            enforce_threshold: True 时仅在消息数达到阈值整数倍才执行
                (消息路径语义);False 时无条件补齐(创作 Run / 恢复路径,
                属于后台创作过程,允许等待补齐)。
        """
        if message_count is None:
            message_count = await self._message_count(db, conversation_id)
        if message_count < 1:
            return None
        if enforce_threshold and (
            message_count < self._threshold
            or message_count % self._threshold != 0
        ):
            return None

        target_to = message_count - self._window
        if target_to < 1:
            return None  # 窗口尚未填满,没有可摘的旧消息

        project_id = await self._get_project_id(db, conversation_id)
        if project_id is None:
            logger.warning(
                "会话 %s 无项目归属,跳过摘要落库(conversation 应已级联删除)",
                conversation_id,
            )
            return None

        prev = await self._latest_summary(db, project_id, conversation_id)
        if prev is not None and prev["content"].get("content_schema_version") == "2.0":
            prev_text = str(prev["content"].get("summary", ""))
            prev_id = str(prev["id"])
            start_from = int(prev["content"].get("covered_to_sequence", 0)) + 1
        elif prev is not None:
            # 迁移:最新 v1 分段摘要作为累计起点(不修改旧 Artifact)
            prev_text = str(prev["content"].get("summary", ""))
            prev_id = str(prev["id"])
            start_from = int(prev["content"].get("covered_to_sequence", 0)) + 1
        else:
            prev_text = ""
            prev_id = ""
            start_from = 1

        if start_from > target_to:
            return None

        messages = await self._load_messages(
            db, conversation_id, start_from, target_to
        )
        if not messages:
            return None

        digest = self._digest(conversation_id, messages)
        body = await self._generate(prev_text, messages)

        summary = ConversationSummary(
            conversation_id=str(conversation_id),
            summary=body.summary,
            topics=body.topics,
            covered_from_sequence=1,
            covered_to_sequence=target_to,
            message_count=target_to,
            content_schema_version="2.0",
            previous_summary_artifact_id=prev_id or None,
            source_message_digest=digest,
        )
        return await self._artifact_service.create_validated_artifact(
            db,
            project_id=project_id,
            artifact_type=ArtifactType.CONVERSATION_SUMMARY,
            episode_number=1,
            content=summary.model_dump(),
            content_schema_version="2.0",
            prompt_version=self._prompt_version,
            # 幂等输入:会话 + 覆盖终点 + 上一摘要 + Prompt 版本 + digest。
            # 同一覆盖区间并发触发得到相同 input_hash → 只落一份。
            dedup_extra=(
                f"v2|{conversation_id}|{target_to}|{prev_id}|"
                f"{self._prompt_version}|{digest}"
            ),
        )

    # ---- 读取 ----

    async def current_message_count(
        self, db: AsyncSession, conversation_id: uuid.UUID
    ) -> int:
        """会话当前已提交消息数(后台调度用它判断消息可见性)。"""
        return await self._message_count(db, conversation_id)

    async def latest_for_conversation(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """指定会话最新摘要(v1/v2 混排下按稳定键取最大)。"""
        return await self._latest_summary(db, project_id, conversation_id)

    # ---- 内部步骤 ----

    async def _message_count(
        self, db: AsyncSession, conversation_id: uuid.UUID
    ) -> int:
        result = await db.execute(
            select(func.coalesce(func.max(Message.sequence), 0)).where(
                Message.conversation_id == conversation_id
            )
        )
        return int(result.scalar_one())

    async def _latest_summary(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """取会话最新摘要 Artifact(covered_to → version → created_at)。"""
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
            if (item.get("content") or {}).get("conversation_id")
            == str(conversation_id)
        ]
        if not matched:
            return None
        return max(matched, key=summary_sort_key)

    async def _load_messages(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
        start: int,
        end: int,
    ) -> list[ShortTermMessage]:
        """从事实源(Message 表)加载 [start, end] 区间消息(升序)。"""
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

    @staticmethod
    def _digest(
        conversation_id: uuid.UUID, messages: list[ShortTermMessage]
    ) -> str:
        """新增消息区间的确定性摘要哈希(幂等输入之一)。"""
        payload = "\n".join(
            f"{m.sequence}|{m.role}|{m.content}" for m in messages
        )
        return hashlib.sha256(f"{conversation_id}\n{payload}".encode()).hexdigest()

    async def _generate(
        self,
        previous_summary: str,
        messages: list[ShortTermMessage],
    ) -> ConversationSummaryBody:
        """渲染 v2 模板(上一版累计 + 新增区间)并调用 LLM。"""
        transcript = "\n".join(
            f"{m.sequence}. [{m.role}] {m.content}" for m in messages
        )
        rendered = self._template.render(
            previous_summary=previous_summary,
            conversation_transcript=transcript,
            message_count=str(len(messages)),
        )
        result = await self._agent.generate_structured(
            ConversationSummaryBody,
            [{"role": "user", "content": rendered}],
            prompt_name=self._prompt_name,
            temperature=0.3,
        )
        if result.error_code or result.parsed is None:
            logger.error(
                "累计会话摘要 LLM 失败: code=%s detail=%s",
                result.error_code,
                result.error_detail,
            )
            raise RuntimeError(
                f"累计会话摘要生成失败: {result.error_code} - {result.error_detail}"
            )
        return cast("ConversationSummaryBody", result.parsed)

    async def _get_project_id(
        self,
        db: AsyncSession,
        conversation_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """反查会话所属项目(会话已删除返回 None)。"""
        result = await db.execute(
            select(Conversation.project_id).where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none()


# ========================================================================
# 项目级读取(有界合并;跨会话不比较 sequence)
# ========================================================================


async def merged_project_summaries(
    db: AsyncSession,
    artifact_service: Any,
    project_id: uuid.UUID,
    *,
    current_conversation_id: uuid.UUID | None = None,
    max_other_conversations: int = _MAX_OTHER_CONVERSATIONS,
) -> str:
    """项目创作上下文的会话记忆(M-02)。

    按会话分别取最新累计摘要;当前会话优先,其余会话按 Artifact 创建时间
    取最近若干条并标明来源会话。跨会话不比较消息 sequence——
    不得任选一条冒充项目全局记忆。
    """
    page = await artifact_service.list_by_project(
        db,
        project_id,
        artifact_type=ArtifactType.CONVERSATION_SUMMARY,
        offset=0,
        limit=100,
    )
    items = cast("list[dict[str, Any]]", page["items"])
    by_conversation: dict[str, dict[str, Any]] = {}
    for item in items:
        conv = str((item.get("content") or {}).get("conversation_id", ""))
        if not conv:
            continue
        existing = by_conversation.get(conv)
        # 同一会话内才允许比较 covered_to(sequence 语义仅会话内有效)
        if existing is None or summary_sort_key(item) > summary_sort_key(existing):
            by_conversation[conv] = item

    current: dict[str, Any] | None = None
    if current_conversation_id is not None:
        current = by_conversation.pop(str(current_conversation_id), None)

    # 会话间排序只用 Artifact 时间(跨会话 sequence 不可比)
    others = sorted(
        by_conversation.values(),
        key=lambda it: str(it.get("created_at", "")),
        reverse=True,
    )[:max_other_conversations]

    parts: list[str] = []
    if current is not None:
        parts.append(
            f"(当前会话记忆,覆盖至第 {current['content'].get('covered_to_sequence')} 条) "
            f"{current['content'].get('summary', '')}"
        )
    for item in others:
        parts.append(
            f"(其他会话记忆,会话 {item['content'].get('conversation_id', '')[:8]}) "
            f"{item['content'].get('summary', '')}"
        )
    return "\n".join(parts)


async def catch_up_project_summaries(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    agent: Any,
    max_conversations: int = 20,
) -> int:
    """创作 Run 进入时补齐项目的累计摘要缺口(M-02 §8.1)。

    后台 best-effort 调度可能留缺口(进程重启/失败/跳过阈值整数倍);
    本入口对项目内会话逐个 catch_up(enforce_threshold=False——
    属于后台创作过程,允许等待)。返回补做的摘要数。
    """
    from sqlalchemy import select as _select

    from app.application.artifact_service import ArtifactService
    from app.prompts.loader import PromptLoader

    conv_ids = (await db.execute(
        _select(Conversation.id)
        .where(Conversation.project_id == project_id)
        .order_by(Conversation.updated_at.desc())
        .limit(max_conversations)
    )).scalars().all()
    if not conv_ids:
        return 0
    manager = ConversationSummaryManager(agent, PromptLoader(), ArtifactService())
    made = 0
    for conv_id in conv_ids:
        artifact = await manager.catch_up(
            db, conv_id, enforce_threshold=False
        )
        if artifact is not None:
            made += 1
    return made


async def latest_project_summary_text(
    db: AsyncSession,
    artifact_service: Any,
    project_id: uuid.UUID,
) -> str:
    """write_episode 的项目记忆入口(G-02 兼容签名)。

    M-02 起改为有界合并(不再跨会话取 covered_to 最大的一条)。
    """
    return await merged_project_summaries(db, artifact_service, project_id)
