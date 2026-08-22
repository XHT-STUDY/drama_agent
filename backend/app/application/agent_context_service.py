from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import InvalidActiveContextError
from app.db.models.artifact import Artifact
from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.db.models.project import Project
from app.db.repositories.artifacts import ArtifactRepository
from app.domain.agent_command import ActiveArtifactContext
from app.domain.context import TaskKind
from app.memory.context_builder import ContextBuilder, ContextManifest


class AgentContextService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self.settings = settings or Settings(app_env="test")
        self.context_builder = context_builder or ContextBuilder(
            budget_tokens=self.settings.agent_context_budget_tokens
        )

    async def build(
        self,
        db: AsyncSession,
        project: Project,
        conversation: Conversation,
        active_context: ActiveArtifactContext | None = None,
        user_request: str | None = None,
        *,
        expected_artifact_type: str | None = None,
        expected_episode_number: int | None = None,
    ) -> tuple[str, ContextManifest]:
        if conversation.project_id != project.id:
            raise InvalidActiveContextError(
                "conversation 不属于当前 project", code="INVALID_CONVERSATION_CONTEXT"
            )
        messages = await self._recent_messages(db, conversation.id)
        request = (
            user_request if user_request is not None else self._latest_user_request(messages)
        ).strip()
        active: Artifact | None = None
        if active_context is not None:
            active = await self._load_active(db, project, active_context)
            if expected_artifact_type and active.type != expected_artifact_type:
                raise InvalidActiveContextError("活动 Artifact 类型与当前请求目标不一致")
            if expected_episode_number is not None and active.episode_number != expected_episode_number:
                raise InvalidActiveContextError("活动 Artifact 集数与当前请求目标不一致")
        repo = ArtifactRepository(db)
        story_bible = await repo.get_latest_valid(project.id, "story_bible", 1)
        outline = await repo.get_latest_valid(project.id, "episode_outline_set", 1)
        scripts = await repo.list_by_project(project.id, "script_draft", offset=0, limit=1000)
        evaluations = await repo.list_by_project(project.id, "evaluation_report", offset=0, limit=1000)
        summary = await self._latest_summary(db, project.id, conversation.id)
        return self.context_builder.build_for(
            TaskKind.REQUIREMENT,
            system_rules=(
                "这是短剧项目的受限上下文。只使用明确事实；完整剧本正文不在 "
                "Planner 上下文中，需要通过 Artifact 引用。"
            ),
            user_request=request,
            story_bible_outline=self._project_context(
                project, story_bible, outline, scripts, evaluations
            ),
            previous_summary_continuity=self._history_context(messages, summary),
            current_target=self._artifact_summary(active),
            protected_sections={"user_request", "current_target"},
        )

    async def _recent_messages(self, db: AsyncSession, conversation_id: uuid.UUID) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence.desc(), Message.id.desc())
            .limit(max(0, self.settings.agent_recent_message_limit))
        )
        result = await db.execute(stmt)
        return list(reversed(result.scalars().all()))

    async def _latest_summary(
        self, db: AsyncSession, project_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> Artifact | None:
        stmt = (
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.type == "conversation_summary",
                Artifact.status == "valid",
                Artifact.content["conversation_id"].astext == str(conversation_id),
            )
            .order_by(Artifact.version.desc(), Artifact.created_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _load_active(
        self, db: AsyncSession, project: Project, active: ActiveArtifactContext
    ) -> Artifact:
        result = await db.execute(select(Artifact).where(Artifact.id == active.artifact_id))
        artifact = result.scalar_one_or_none()
        if artifact is None or artifact.project_id != project.id:
            raise InvalidActiveContextError("活动 Artifact 不存在或不属于当前项目")
        if artifact.type != active.artifact_type:
            raise InvalidActiveContextError("活动 Artifact 类型不匹配")
        if active.episode_number is not None and artifact.episode_number != active.episode_number:
            raise InvalidActiveContextError("活动 Artifact 集数不匹配")
        if active.version is not None and artifact.version != active.version:
            raise InvalidActiveContextError("活动 Artifact 版本不匹配")
        if active.checksum is not None and artifact.checksum != active.checksum:
            raise InvalidActiveContextError("活动 Artifact checksum 不匹配")
        if artifact.status != "valid":
            raise InvalidActiveContextError("活动 Artifact 不是有效版本")
        return artifact

    @staticmethod
    def _latest_user_request(messages: Iterable[Message]) -> str:
        for message in reversed(list(messages)):
            if message.role == "user" and message.content.strip():
                return message.content
        return ""

    @staticmethod
    def _project_context(
        project: Project, story_bible: Artifact | None, outline: Artifact | None,
        scripts: list[Artifact], evaluations: list[Artifact]
    ) -> str:
        lines = [
            f"项目: {project.title or '未命名项目'} "
            f"(project_id={project.id}, 目标集数={project.target_episode_count})"
        ]
        if story_bible is None:
            lines.append("StoryBible: 尚未生成")
        else:
            c = story_bible.content or {}
            p = (c.get("protagonist") or {}).get("name", "")
            a = (c.get("antagonist") or {}).get("name", "")
            lines.append(
                f"StoryBible: artifact_id={story_bible.id}, version={story_bible.version}, "
                f"title={c.get('title', '')}, genre={c.get('genre', '')}, logline={c.get('logline', '')}, "
                f"protagonist={p}, antagonist={a}, locked_facts={_compact(c.get('locked_facts') or [])}"
            )
        if outline is None:
            lines.append("分集大纲: 尚未生成")
        else:
            c = outline.content or {}
            episodes = c.get("episodes") or []
            index = "; ".join(
                f"E{x.get('episode_number', '?')}: {x.get('title', '')} / {x.get('objective', '')}"
                for x in episodes if isinstance(x, dict)
            )
            lines.append(
                f"分集大纲: artifact_id={outline.id}, version={outline.version}, "
                f"episodes={len(episodes)}, arc_summary={c.get('arc_summary', '')}; index={index}"
            )
        lines.append("剧集索引: " + _episode_index(scripts, _script_line))
        lines.append("评估索引: " + _episode_index(evaluations, _evaluation_line))
        return "\n".join(lines)

    @staticmethod
    def _history_context(messages: list[Message], summary: Artifact | None) -> str:
        lines: list[str] = []
        if summary is not None:
            c = summary.content or {}
            lines.append(
                f"会话摘要: {c.get('summary', '')} "
                f"(覆盖 {c.get('covered_from_sequence', '?')}-{c.get('covered_to_sequence', '?')})"
            )
        if messages:
            lines.append("最近消息（最多配置的条数）:")
            lines.extend(f"[{m.sequence}] {m.role}: {m.content}" for m in messages)
        return "\n".join(lines)

    @staticmethod
    def _artifact_summary(artifact: Artifact | None) -> str:
        if artifact is None:
            return ""
        c = artifact.content or {}
        prefix = (
            f"活动 Artifact: id={artifact.id}, type={artifact.type}, "
            f"episode={artifact.episode_number}, version={artifact.version}"
        )
        if artifact.type == "script_draft":
            return prefix + "\n" + "\n".join(
                [
                    f"title={c.get('title', '')}",
                    f"scene_count={len(c.get('scenes') or [])}",
                    f"word_count={c.get('word_count', 0)}",
                    f"dialogue_ratio={c.get('dialogue_ratio', 0)}",
                    "plain_text=omitted",
                ]
            )
        if artifact.type == "evaluation_report":
            return prefix + "\n" + "\n".join(
                [
                    f"overall_score={c.get('overall_score', 0)}",
                    f"need_revision={c.get('need_revision', False)}",
                    f"issue_count={len(c.get('issues') or [])}",
                ]
            )
        if artifact.type == "story_bible":
            return prefix + "\n" + "\n".join(
                [
                    f"title={c.get('title', '')}",
                    f"logline={c.get('logline', '')}",
                    f"locked_facts={_compact(c.get('locked_facts') or [])}",
                ]
            )
        if artifact.type == "episode_outline_set":
            return prefix + f"\nepisodes={len(c.get('episodes') or [])}"
        return prefix + f"\nkeys={sorted(c)[:20]}"


def _compact(values: list[Any]) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _episode_index(artifacts: list[Artifact], builder: Any) -> str:
    latest: dict[int, Artifact] = {}
    for artifact in artifacts:
        if artifact.status != "valid":
            continue
        current = latest.get(artifact.episode_number)
        if current is None or artifact.version > current.version:
            latest[artifact.episode_number] = artifact
    if not latest:
        return "暂无"
    return "; ".join(builder(n, latest[n]) for n in sorted(latest))


def _script_line(episode: int, artifact: Artifact) -> str:
    c = artifact.content or {}
    return (
        f"E{episode:02d} v{artifact.version} "
        f"words={c.get('word_count', 0)} "
        f"scenes={len(c.get('scenes') or [])}"
    )


def _evaluation_line(episode: int, artifact: Artifact) -> str:
    c = artifact.content or {}
    return (
        f"E{episode:02d} v{artifact.version} "
        f"score={c.get('overall_score', 0)} "
        f"need_revision={c.get('need_revision', False)}"
    )
