"""CreationAgent — 创作阶段 Agent (C-03, C-04, C-05).

职责:
- 组合 NormalizedRequirement 与 RAG 上下文
- 调用各创作 Skill: StoryBible → Outline → Episode Writer
- 不自行决定控制流——控制流由 LangGraph Workflow 决定

模块边界:
- Agent 只负责包装 Skill 调用、注入上下文
- 不直接访问 ORM、不操作前端
"""

from __future__ import annotations

import logging
from typing import Any, cast
from uuid import UUID

from app.agents.base import BaseAgent
from app.domain.outline import EpisodeOutlineSet, OutlineInput
from app.domain.requirement import NormalizedRequirement
from app.domain.script import EpisodeWriterInput, ScriptDraft
from app.domain.story_bible import StoryBible, StoryBibleInput
from app.prompts.loader import PromptLoader
from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class CreationAgent:
    """创作 Agent——组合 Planner/Writer 角色能力。

    内部使用 BaseAgent 进行 LLM 调用，
    通过 SkillRegistry 调用已注册的创作 Skill。

    使用方法:
        agent = CreationAgent(base_agent=base, skill_registry=registry)
        bible = await agent.generate_story_bible(requirement, prompt_loader)
    """

    def __init__(
        self,
        base_agent: BaseAgent,
        skill_registry: SkillRegistry,
    ) -> None:
        """初始化 CreationAgent。

        Args:
            base_agent: 已配置 LLM 的 BaseAgent 实例
            skill_registry: 包含 StoryBibleSkill 等已注册 Skill 的注册表
        """
        self._agent = base_agent
        self._skills = skill_registry

    # ---- StoryBible (C-03) ----

    async def generate_story_bible(
        self,
        normalized_requirement: NormalizedRequirement,
        prompt_loader: PromptLoader,
        *,
        rag_context: str = "",
    ) -> StoryBible:
        """从归一化需求生成 StoryBible。

        Args:
            normalized_requirement: C-02 生成的归一化需求
            prompt_loader: Prompt 模板加载器
            rag_context: 知识库检索片段 (MVP 可为空)

        Returns:
            校验通过的 StoryBible

        Raises:
            RuntimeError: LLM 调用失败
            StoryBibleValidationError: 输出不满足质量门禁
        """
        # 构造输入
        sb_input = StoryBibleInput(
            normalized_requirement=normalized_requirement.model_dump(),
            rag_context=rag_context,
        )

        logger.info(
            "开始生成 StoryBible: title=%s genre=%s",
            normalized_requirement.title,
            normalized_requirement.genre,
        )

        # 调用 Skill
        skill = self._skills.get("story_bible")
        context: dict[str, Any] = {
            "input": sb_input,
            "agent": self._agent,
            "prompt_loader": prompt_loader,
        }
        result = cast(StoryBible, await skill.execute(context))

        logger.info(
            "StoryBible 生成完成: protagonist=%s antagonist=%s supporting=%d",
            result.protagonist.name,
            result.antagonist.name,
            len(result.supporting_characters),
        )

        return result

    # ---- Outline (C-04) ----

    async def generate_outline(
        self,
        story_bible: StoryBible,
        prompt_loader: PromptLoader,
        *,
        rag_context: str = "",
        outline_count: int = 10,
    ) -> EpisodeOutlineSet:
        """从 StoryBible 生成 10 集分集大纲。

        Args:
            story_bible: C-03 生成的 StoryBible
            prompt_loader: Prompt 模板加载器
            rag_context: 知识库检索片段 (MVP 可为空)
            outline_count: 目标集数 (MVP 固定 10)

        Returns:
            校验通过的 EpisodeOutlineSet

        Raises:
            RuntimeError: LLM 调用失败
            OutlineValidationError: 结构校验失败
        """
        ol_input = OutlineInput(
            story_bible=story_bible.model_dump(),
            rag_context=rag_context,
            outline_count=outline_count,
        )

        logger.info(
            "开始生成分集大纲: title=%s count=%d",
            story_bible.title,
            outline_count,
        )

        skill = self._skills.get("outline")
        context: dict[str, Any] = {
            "input": ol_input,
            "agent": self._agent,
            "prompt_loader": prompt_loader,
        }
        result = cast(EpisodeOutlineSet, await skill.execute(context))

        logger.info(
            "分集大纲生成完成: episodes=%d arc_summary=%.60s...",
            len(result.episodes),
            result.arc_summary,
        )

        return result

    # ---- Episode Writer (C-05) ----

    async def generate_episode(
        self,
        episode_number: int,
        episode_outline: dict[str, Any],
        story_bible: StoryBible,
        prompt_loader: PromptLoader,
        *,
        outline_artifact_id: UUID,
        previous_summary: str = "",
        continuity_state: str = "",
        rag_context: str = "",
    ) -> ScriptDraft:
        """撰写单集剧本草稿。

        Args:
            episode_number: 目标集号 (1-based)
            episode_outline: 本集大纲 (dict)
            story_bible: StoryBible
            prompt_loader: Prompt 模板加载器
            outline_artifact_id: 关联的分集大纲 Artifact ID
            previous_summary: 前集摘要 (第2集起传入, 非全文)
            continuity_state: 连续性状态快照
            rag_context: 知识库检索片段

        Returns:
            校验通过的 ScriptDraft (word_count/dialogue_ratio 已被工具覆盖)

        Raises:
            RuntimeError: LLM 调用失败
            EpisodeWriterValidationError: 结构校验失败
        """
        ew_input = EpisodeWriterInput(
            episode_number=episode_number,
            episode_outline=episode_outline,
            story_bible=story_bible.model_dump(),
            previous_summary=previous_summary,
            continuity_state=continuity_state,
            rag_context=rag_context,
        )

        logger.info(
            "开始撰写第 %d 集剧本: title=%s",
            episode_number,
            episode_outline.get("title", ""),
        )

        skill = self._skills.get("write_episode")
        context: dict[str, Any] = {
            "input": ew_input,
            "agent": self._agent,
            "prompt_loader": prompt_loader,
            "outline_artifact_id": outline_artifact_id,
        }
        result = cast(ScriptDraft, await skill.execute(context))

        logger.info(
            "第 %d 集剧本完成: scenes=%d word_count=%d dialogue_ratio=%.2f",
            result.episode_number,
            len(result.scenes),
            result.word_count,
            result.dialogue_ratio,
        )

        return result
