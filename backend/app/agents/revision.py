"""RevisionAgent — 修订阶段 Agent (F-02).

职责:
- 组合原稿、修订计划、StoryBible、当前集大纲与连续性状态
- 调用 ReviserSkill 生成完整新稿与 operation 执行记录
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
from app.domain.revision import (
    RevisionPlan,
    RevisionResult,
    RevisionTaskInput,
)
from app.domain.script import ScriptDraft
from app.prompts.loader import PromptLoader
from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class RevisionAgent:
    """修订 Agent——包装 Reviser 角色能力。

    内部使用 BaseAgent 进行 LLM 调用，
    通过 SkillRegistry 调用已注册的 ReviserSkill。
    """

    def __init__(
        self,
        base_agent: BaseAgent,
        skill_registry: SkillRegistry,
    ) -> None:
        """初始化 RevisionAgent。

        Args:
            base_agent: 已配置 LLM 的 BaseAgent 实例
            skill_registry: 包含 ReviserSkill 的注册表
        """
        self._agent = base_agent
        self._skills = skill_registry

    async def revise_episode(
        self,
        *,
        script_draft: ScriptDraft,
        revision_plan: RevisionPlan,
        story_bible: dict[str, Any],
        episode_outline: dict[str, Any],
        source_revision_plan_artifact_id: UUID,
        prompt_loader: PromptLoader,
        continuity_state: str = "",
    ) -> RevisionResult:
        """按修订计划对单集剧本执行局部改写。

        Args:
            script_draft: 原稿剧本
            revision_plan: 修订计划（含原稿/评估来源与操作列表）
            story_bible: StoryBible（dict）
            episode_outline: 当前集大纲（dict）
            source_revision_plan_artifact_id: 修订计划 Artifact ID
            prompt_loader: Prompt 模板加载器
            continuity_state: 当前连续性状态文本快照

        Returns:
            权威字段已覆盖、指标已重算、执行记录全覆盖的 RevisionResult

        Raises:
            RuntimeError: LLM 调用失败
            ReviserValidationError: 修订结果结构校验失败
        """
        task_input = RevisionTaskInput(
            episode_number=script_draft.episode_number,
            script_draft=script_draft,
            revision_plan=revision_plan,
            story_bible=story_bible,
            episode_outline=episode_outline,
            continuity_state=continuity_state,
            source_revision_plan_artifact_id=source_revision_plan_artifact_id,
        )

        logger.info(
            "开始修订第 %d 集剧本: title=%s operations=%d",
            task_input.episode_number,
            script_draft.title,
            len(revision_plan.operations),
        )

        skill = self._skills.get("revise_episode")
        context: dict[str, Any] = {
            "input": task_input,
            "agent": self._agent,
            "prompt_loader": prompt_loader,
        }
        result = cast(RevisionResult, await skill.execute(context))

        logger.info(
            "第 %d 集修订完成: scenes=%d executions=%d",
            result.script_draft.episode_number,
            len(result.script_draft.scenes),
            len(result.operation_executions),
        )

        return result
