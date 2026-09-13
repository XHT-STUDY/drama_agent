from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

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
from app.domain.agent_planner import (
    TARGET_OUTLINE_RE,
    TARGET_STORY_BIBLE_RE,
    ExplanationSourceText,
)
from app.domain.context import TaskKind
from app.memory.context_builder import ContextBuilder, ContextManifest
from app.skills.agent_command_planner import extract_episode_numbers


@dataclass
class ExplanationTarget:
    """解释目标的元信息（W1-03）。"""

    artifact_id: str
    type: str
    episode: int
    version: int
    checksum: str


@dataclass
class ExplanationContext:
    """解释上下文（W1-03）：目标元信息 + 已读原文（复用 domain 的
    ExplanationSourceText，不再镜像第二份结构）。

    status：ok=可解释；narrow=正文超预算且未选定单场（提示缩小范围，
    不假装读完）；no_text=目标无正文（如只有标题的空剧本）。
    """

    status: Literal["ok", "narrow", "no_text"]
    question: str
    target: ExplanationTarget
    sources: list[ExplanationSourceText] = field(default_factory=list)


def _story_bible_text(artifact: Artifact) -> str:
    c = artifact.content or {}
    lines = [
        f"title: {c.get('title', '')}",
        f"logline: {c.get('logline', '')}",
        f"world_setting: {c.get('world_setting', '')}",
        f"protagonist: {(c.get('protagonist') or {}).get('name', '')}",
        f"antagonist: {(c.get('antagonist') or {}).get('name', '')}",
    ]
    locked = c.get("locked_facts") or []
    if locked:
        lines.append("locked_facts: " + "；".join(str(x) for x in locked[:10]))
    loops = c.get("story_loops") or c.get("loops") or []
    for loop in loops[:5]:
        if isinstance(loop, dict):
            lines.append(
                f"loop: {loop.get('title', '')} — {loop.get('description', '')}"
            )
    return "\n".join(lines)


def _episode_outline_text(artifact: Artifact, episode: int | None) -> str:
    c = artifact.content or {}
    episodes = c.get("episodes") or []
    lines = [f"arc_summary: {c.get('arc_summary', '')}"]
    for item in episodes:
        if not isinstance(item, dict):
            continue
        if episode is None or item.get("episode_number") == episode:
            lines.append(
                f"E{item.get('episode_number', '?')} {item.get('title', '')}: "
                f"{item.get('objective', '')}"
            )
    return "\n".join(lines)


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

    async def validate_active_context(
        self,
        db: AsyncSession,
        project: Project,
        active_context: ActiveArtifactContext,
    ) -> None:
        """供 Turn 事务 A 预检活动上下文（复用 build 的全部校验规则）。

        在持久化 AgentTurn 与 user 消息之前拒绝非法活动 Artifact,
        避免留下永远无法完成的 Turn。
        """
        await self._load_active(db, project, active_context)

    async def build_explanation_context(
        self,
        db: AsyncSession,
        project: Project,
        user_request: str,
        active_context: ActiveArtifactContext | None = None,
    ) -> ExplanationContext | None:
        """为内容性解释组装"已读原文"（W1-03）。

        目标优先级（固定）：文本明确指定的对象/集数 > 活动上下文 >
        无目标（返回 None，调用方回落项目级答复）。活动上下文的历史
        版本允许只读解释（不做 latest/stale 检查——写作路径才有该约束）。

        读取在本调用（只读事务）内完成；调用方在提交/关闭事务后再调模型。
        正文预算沿用 agent_context_budget_tokens（按字符数保守估算）；
        超预算且未选定单场时返回 narrow——提示作者缩小范围，不截断
        关键正文后假装读完。
        """
        from app.tools.text_evidence import scene_texts_from_content

        request = user_request.strip()
        repo = ArtifactRepository(db)
        episodes = extract_episode_numbers(request)

        target: Artifact | None = None
        scene_filter: int | None = None
        # 1) 文本显式对象/集数优先（不匹配的活动上下文被忽略）
        if TARGET_OUTLINE_RE.search(request):
            target = await repo.get_latest_valid(project.id, "episode_outline_set", 1)
        elif TARGET_STORY_BIBLE_RE.search(request):
            target = await repo.get_latest_valid(project.id, "story_bible", 1)
        elif episodes:
            target = await repo.get_latest_valid(project.id, "script_draft", episodes[0])
        # 2) 活动上下文（含历史版本——只读解释允许）
        if target is None and active_context is not None:
            target = await self._load_active(db, project, active_context)
            scene_filter = active_context.scene_number

        if target is None:
            return None

        sources: list[ExplanationSourceText] = []
        index = 0
        budget_chars = self.settings.agent_context_budget_tokens
        if target.type == "script_draft":
            texts = scene_texts_from_content(target.content or {})
            if not texts:
                return ExplanationContext(
                    status="no_text",
                    question=request,
                    target=self._target_meta(target),
                )
            total_chars = sum(len(t) for t in texts.values())
            if scene_filter is not None and scene_filter in texts:
                scenes = {scene_filter: texts[scene_filter]}
            elif total_chars > budget_chars:
                return ExplanationContext(
                    status="narrow",
                    question=request,
                    target=self._target_meta(target),
                )
            else:
                scenes = texts
            for scene_number in sorted(scenes):
                index += 1
                sources.append(
                    ExplanationSourceText(
                        source_index=index,
                        kind="script_scene",
                        label=f"第 {target.episode_number} 集 · 第 {scene_number} 场",
                        scene_number=scene_number,
                        text=scenes[scene_number],
                    )
                )
        elif target.type == "episode_outline_set":
            index += 1
            sources.append(
                ExplanationSourceText(
                    source_index=index,
                    kind="outline",
                    label="分集大纲",
                    text=_episode_outline_text(target, episodes[0] if episodes else None),
                )
            )
        else:
            index += 1
            sources.append(
                ExplanationSourceText(
                    source_index=index,
                    kind="story_bible",
                    label="故事设定",
                    text=_story_bible_text(target),
                )
            )

        # 辅助来源：解释剧本时附设定（小体积，帮助回答"为什么"）；
        # 总量仍受预算约束——超限时优先丢辅助来源，正文永不因辅助内容被截
        if target.type == "script_draft":
            sb = await repo.get_latest_valid(project.id, "story_bible", 1)
            if sb is not None:
                aux_text = _story_bible_text(sb)
                used_chars = sum(len(src.text) for src in sources)
                if used_chars + len(aux_text) <= budget_chars:
                    index += 1
                    sources.append(
                        ExplanationSourceText(
                            source_index=index,
                            kind="story_bible",
                            label="故事设定（辅助）",
                            text=aux_text,
                        )
                    )

        return ExplanationContext(
            status="ok",
            question=request,
            target=self._target_meta(target),
            sources=sources,
        )

    @staticmethod
    def _target_meta(artifact: Artifact) -> ExplanationTarget:
        return ExplanationTarget(
            artifact_id=str(artifact.id),
            type=artifact.type,
            episode=artifact.episode_number,
            version=artifact.version,
            checksum=artifact.checksum or "",
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
        # W1-03：场景锚点仅对剧本合法，且必须存在于该确切版本
        if active.scene_number is not None:
            if artifact.type != "script_draft":
                raise InvalidActiveContextError("scene_number 仅对剧本 Artifact 合法")
            scenes = (artifact.content or {}).get("scenes") or []
            if not any(
                isinstance(sc, dict) and sc.get("scene_number") == active.scene_number
                for sc in scenes
            ):
                raise InvalidActiveContextError(
                    f"第 {active.scene_number} 场不存在于该剧本版本"
                )
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
