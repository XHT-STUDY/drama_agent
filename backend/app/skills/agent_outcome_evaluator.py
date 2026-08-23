"""AgentOutcomeEvaluatorSkill — 语义约束判断与后续意图建议（J-09）。

只处理确定性校验器无法判断的部分：
- 用户语义约束是否在产出中满足（constraint_judgments）;
- 一次白名单后续意图建议（recommended_next_action，不含执行句柄）。

模型不得改变 goal_status / score_delta / evidence_artifact_ids——
AgentOutcomeService 合并时忽略模型的越权输出（Schema 本身就不含这些字段）。
"""

from __future__ import annotations

import logging
from typing import Any, cast

from app.agents.base import BaseAgent
from app.core.errors import AppError
from app.domain.agent_command import (
    AgentOutcomeEvaluatorInput,
    AgentOutcomeEvaluatorOutput,
)
from app.prompts.loader import PromptLoader
from app.skills.protocol import Skill, SkillMetadata

logger = logging.getLogger(__name__)

# 后续建议允许的意图白名单（explain 是只读意图，不构成后续计划）
_RECOMMENDABLE_INTENTS = frozenset(
    {"create_script", "evaluate", "revise_script", "revise_outline"}
)


class InvalidOutcomeRecommendationError(AppError):
    """Evaluator 建议了白名单之外的意图。"""

    status_code = 422
    code = "INVALID_OUTCOME_RECOMMENDATION"


class AgentOutcomeEvaluatorSkill(Skill):
    """判断语义约束是否达成，并建议一次白名单后续意图。"""

    metadata = SkillMetadata(
        name="agent_outcome_evaluator",
        version="1.0",
        description="判断用户语义约束是否达成并建议一次白名单后续意图",
    )

    async def execute(self, context: dict[str, Any]) -> AgentOutcomeEvaluatorOutput:
        """执行评估。

        context 必需键:
            input: AgentOutcomeEvaluatorInput
            agent: BaseAgent
            prompt_loader: PromptLoader
        """
        raw = context["input"]
        eval_input = (
            raw
            if isinstance(raw, AgentOutcomeEvaluatorInput)
            else AgentOutcomeEvaluatorInput.model_validate(raw)
        )
        agent: BaseAgent = context["agent"]
        prompt_loader: PromptLoader = context["prompt_loader"]

        template = prompt_loader.get("agent_outcome_evaluator")
        rendered = template.render(
            goal=eval_input.goal,
            intent=eval_input.intent,
            run_status=eval_input.run_status,
            deterministic_goal_status=eval_input.deterministic_goal_status,
            user_constraints=(
                "\n".join(f"- {c}" for c in eval_input.user_constraints)
                or "(无语义约束，无需判断)"
            ),
            evidence_summary=eval_input.evidence_summary or "(无)",
        )

        result = await agent.generate_structured(
            AgentOutcomeEvaluatorOutput,
            [{"role": "user", "content": rendered}],
            prompt_name="agent_outcome_evaluator",
            temperature=0.1,
            max_tokens=1200,
        )
        if result.error_code or result.parsed is None:
            raise InvalidOutcomeRecommendationError(
                detail=f"Outcome Evaluator 输出无效: {result.error_code or 'INVALID_OUTPUT'}"
            )
        output = cast(AgentOutcomeEvaluatorOutput, result.parsed)

        recommendation = output.recommended_next_action
        if recommendation is not None and recommendation.intent not in _RECOMMENDABLE_INTENTS:
            raise InvalidOutcomeRecommendationError(
                detail=f"Outcome Evaluator 建议的意图不在白名单中: {recommendation.intent}"
            )
        return output
