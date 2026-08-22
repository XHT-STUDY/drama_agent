"""J-03 AgentCommandPlannerSkill 单元测试。"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agents.base import BaseAgent
from app.domain.agent_planner import (
    AgentPlannerInput,
    AgentPlannerOutput,
    PlannerStep,
    PlannerTarget,
)
from app.llm.fake import FakeLLM
from app.prompts.loader import PromptLoader
from app.skills.agent_command_planner import (
    AgentCommandPlannerSkill,
    InvalidPlannerOutputError,
    requires_confirmation,
)


@pytest.fixture
def loader() -> PromptLoader:
    return PromptLoader()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def agent(llm: FakeLLM) -> BaseAgent:
    return BaseAgent(name="planner", llm=llm)


def _input(request: str, **kwargs: Any) -> AgentPlannerInput:
    return AgentPlannerInput(
        user_request=request,
        target_episode_count=10,
        available_intents=["create_script", "explain", "evaluate"],
        **kwargs,
    )


@pytest.mark.asyncio
async def test_ambiguous_revision_returns_single_clarification_question(
    agent: BaseAgent, loader: PromptLoader, llm: FakeLLM
) -> None:
    result = await AgentCommandPlannerSkill().execute(
        {"input": _input("把这里改得更紧张"), "agent": agent, "prompt_loader": loader}
    )

    assert result.turn_type == "clarification"
    assert result.clarification_question
    assert result.clarification_question.count("？") + result.clarification_question.count("?") == 1
    assert llm.get_call_history() == []


@pytest.mark.asyncio
async def test_out_of_range_episode_is_clarified(
    agent: BaseAgent, loader: PromptLoader
) -> None:
    result = await AgentCommandPlannerSkill().execute(
        {"input": _input("评估第11集"), "agent": agent, "prompt_loader": loader}
    )

    assert result.turn_type == "clarification"
    assert "只有 10 集" in (result.clarification_question or "")


@pytest.mark.asyncio
async def test_unresolved_turns_offer_four_legal_examples(
    agent: BaseAgent, loader: PromptLoader
) -> None:
    result = await AgentCommandPlannerSkill().execute(
        {
            "input": _input(
                "修改当前剧本",
                unresolved_turn_count=3,
                active_context=None,
            ),
            "agent": agent,
            "prompt_loader": loader,
        }
    )

    question = result.clarification_question or ""
    assert question.count("；") >= 3
    assert "创建剧本" in question
    assert "解释项目大纲" in question
    assert "评估项目" in question
    assert "评估第1集" in question


@pytest.mark.asyncio
async def test_plan_uses_server_whitelist_and_readable_steps(
    agent: BaseAgent, loader: PromptLoader, llm: FakeLLM
) -> None:
    llm.register(
        "agent_command_planner",
        AgentPlannerOutput(
            turn_type="plan",
            intent="create_script",
            target=PlannerTarget(target_type="project"),
            steps=[PlannerStep(title="整理需求", description="确认项目创作范围")],
            expected_impact=["生成新的创作计划"],
        ),
    )

    result = await AgentCommandPlannerSkill().execute(
        {"input": _input("请创建剧本"), "agent": agent, "prompt_loader": loader}
    )

    assert result.intent == "create_script"
    assert result.steps[0].title == "整理需求"
    assert requires_confirmation(result.intent) is True
    assert requires_confirmation("explain") is False


@pytest.mark.asyncio
async def test_model_cannot_return_unknown_intent(
    agent: BaseAgent, loader: PromptLoader, llm: FakeLLM
) -> None:
    llm.register(
        "agent_command_planner",
        AgentPlannerOutput(
            turn_type="plan",
            intent="delete_database",
            target=PlannerTarget(target_type="project"),
            steps=[PlannerStep(title="删除", description="删除项目")],
        ),
    )

    with pytest.raises(InvalidPlannerOutputError) as exc_info:
        await AgentCommandPlannerSkill().execute(
            {"input": _input("请处理项目"), "agent": agent, "prompt_loader": loader}
        )
    assert exc_info.value.code == "INVALID_OUTPUT"


@pytest.mark.asyncio
async def test_model_cannot_return_artifact_id_or_tool_text(
    agent: BaseAgent, loader: PromptLoader, llm: FakeLLM
) -> None:
    llm.register(
        "agent_command_planner",
        AgentPlannerOutput(
            turn_type="plan",
            intent="create_script",
            target=PlannerTarget(target_type="project"),
            steps=[
                PlannerStep(
                    title="调用工具",
                    description=f"使用 artifact_id {uuid4()} 执行",
                )
            ],
        ),
    )

    with pytest.raises(InvalidPlannerOutputError):
        await AgentCommandPlannerSkill().execute(
            {"input": _input("请创建剧本"), "agent": agent, "prompt_loader": loader}
        )


def test_planner_output_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AgentPlannerOutput.model_validate(
            {"turn_type": "answer", "answer": "ok", "requires_confirmation": True}
        )
