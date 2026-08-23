"""AgentOutcomeEvaluatorSkill 单元测试（J-09）。

- golden 输出通过 Schema 与意图白名单校验;
- 白名单外意图被拒绝;
- 输出 Schema 不含 goal_status/score/evidence——模型无从越权。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from app.agents.base import BaseAgent
from app.domain.agent_command import (
    AgentOutcomeEvaluatorInput,
    AgentOutcomeEvaluatorOutput,
    OutcomeRecommendation,
)
from app.llm.fake import FakeLLM
from app.prompts.loader import PromptLoader
from app.skills.agent_outcome_evaluator import (
    AgentOutcomeEvaluatorSkill,
    InvalidOutcomeRecommendationError,
)

GOLDEN = (
    Path(__file__).resolve().parents[2] / "golden" / "agent_outcome_partial.json"
)


def _input() -> AgentOutcomeEvaluatorInput:
    return AgentOutcomeEvaluatorInput(
        goal="按用户要求修订第 3 集剧本并重评",
        intent="revise_script",
        run_status="completed",
        deterministic_goal_status="achieved",
        user_constraints=["第 3 集增加正面冲突", "保持整体轻松基调"],
        evidence_summary="Run 终态: completed\n评分变化: +2.5",
    )


@pytest.mark.unit
@pytest.mark.asyncio
class TestAgentOutcomeEvaluatorSkill:
    async def test_golden_output_passes_whitelist(self) -> None:
        llm = FakeLLM(seed=42)
        llm.register(
            "agent_outcome_evaluator",
            AgentOutcomeEvaluatorOutput.model_validate(
                cast(dict[str, Any], json.loads(GOLDEN.read_text(encoding="utf-8")))
            ),
        )
        output = await AgentOutcomeEvaluatorSkill().execute(
            {
                "input": _input(),
                "agent": BaseAgent(name="planner", llm=llm),
                "prompt_loader": PromptLoader(),
            }
        )
        assert len(output.constraint_judgments) == 2
        assert output.recommended_next_action is not None
        assert output.recommended_next_action.intent == "revise_script"

    async def test_intent_outside_whitelist_is_rejected(self) -> None:
        llm = FakeLLM(seed=42)
        llm.register(
            "agent_outcome_evaluator",
            AgentOutcomeEvaluatorOutput(
                constraint_judgments=[],
                recommended_next_action=OutcomeRecommendation(
                    intent="explain",  # 只读意图不构成后续计划
                    episode_number=None,
                    reason="-",
                ),
            ),
        )
        with pytest.raises(InvalidOutcomeRecommendationError):
            await AgentOutcomeEvaluatorSkill().execute(
                {
                    "input": _input(),
                    "agent": BaseAgent(name="planner", llm=llm),
                    "prompt_loader": PromptLoader(),
                }
            )

    async def test_output_schema_has_no_authoritative_fields(self) -> None:
        """输出 Schema 不含确定性字段——模型无法越权改写结论。"""
        fields = set(AgentOutcomeEvaluatorOutput.model_fields)
        assert "goal_status" not in fields
        assert "score_delta" not in fields
        assert "evidence_artifact_ids" not in fields
