"""EvaluationAgent — 评估阶段 Agent (E-02).

职责:
- 组合被评估剧本、本集大纲、StoryBible 与客观特征
- 调用 EvaluationSkill 生成 EvaluationReport
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
from app.domain.evaluation import EvaluationInput, EvaluationReport
from app.domain.script import ScriptDraft
from app.prompts.loader import PromptLoader
from app.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class EvaluationAgent:
    """评估 Agent——包装 Evaluator 角色能力。

    内部使用 BaseAgent 进行 LLM 调用，
    通过 SkillRegistry 调用已注册的 EvaluationSkill。
    """

    def __init__(
        self,
        base_agent: BaseAgent,
        skill_registry: SkillRegistry,
    ) -> None:
        """初始化 EvaluationAgent。

        Args:
            base_agent: 已配置 LLM 的 BaseAgent 实例
            skill_registry: 包含 EvaluationSkill 的注册表
        """
        self._agent = base_agent
        self._skills = skill_registry

    async def evaluate_episode(
        self,
        *,
        episode_number: int,
        script_draft: ScriptDraft,
        episode_outline: dict[str, Any],
        story_bible: dict[str, Any],
        prompt_loader: PromptLoader,
        script_artifact_id: UUID,
        script_features: dict[str, Any] | None = None,
    ) -> EvaluationReport:
        """对单集剧本执行结构化评估。

        Args:
            episode_number: 被评估的集号
            script_draft: 待评估的剧本
            episode_outline: 本集大纲（dict）
            story_bible: StoryBible（dict）
            prompt_loader: Prompt 模板加载器
            script_artifact_id: 被评估的 Script Artifact ID
            script_features: 客观辅助特征（可选，缺省由 Skill 计算）

        Returns:
            服务端已回填 overall_score / need_revision 的 EvaluationReport

        Raises:
            RuntimeError: LLM 调用失败
        """
        ev_input = EvaluationInput(
            episode_number=episode_number,
            script_draft=script_draft,
            episode_outline=episode_outline,
            story_bible=story_bible,
            script_features=script_features or {},
        )

        logger.info(
            "开始评估第 %d 集剧本: title=%s",
            episode_number,
            script_draft.title,
        )

        skill = self._skills.get("evaluate_episode")
        context: dict[str, Any] = {
            "input": ev_input,
            "agent": self._agent,
            "prompt_loader": prompt_loader,
            "script_artifact_id": script_artifact_id,
        }
        report = cast(EvaluationReport, await skill.execute(context))

        logger.info(
            "第 %d 集评估完成: overall=%.1f need_revision=%s issues=%d",
            report.episode_number,
            report.overall_score,
            report.need_revision,
            len(report.issues),
        )

        return report
