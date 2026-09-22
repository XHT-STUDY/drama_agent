"""J-03 AgentCommandPlannerSkill 单元测试。"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agents.base import BaseAgent
from app.domain.agent_command import ActiveArtifactContext
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


# ========================================================================
# W1-03：明确目标直达与集数解析
# ========================================================================


class TestExplicitTargetDirectW103:
    """明确对象/集数的修改请求不再因缺少活动上下文而澄清。"""

    @staticmethod
    def _input(request: str, *, episodes: int = 10, active: Any = None) -> Any:
        from app.domain.agent_planner import AgentPlannerInput

        return AgentPlannerInput(
            user_request=request,
            project_title="测试",
            target_episode_count=episodes,
            available_intents=[
                "create_script", "explain", "evaluate", "revise_script", "revise_outline",
            ],
            active_context=active,
        )

    def test_extract_episode_numbers_arabic_and_chinese(self) -> None:
        from app.domain.agent_planner import extract_episode_numbers

        assert extract_episode_numbers("修改第3集剧本") == [3]
        assert extract_episode_numbers("改第三集") == [3]
        assert extract_episode_numbers("第二十五集怎么样") == [25]
        assert extract_episode_numbers("EP12 和 ep3") == [12, 3]
        assert extract_episode_numbers("第十集") == [10]
        assert extract_episode_numbers("随便改改") == []

    def test_explicit_episode_without_active_passes_preflight(self) -> None:
        """明确第 3 集、无活动上下文 → 不澄清（服务端解析目标）。"""
        from app.skills.agent_command_planner import _preflight_clarification

        assert _preflight_clarification(self._input("修改第3集剧本")) is None
        assert _preflight_clarification(self._input("改第三集")) is None

    def test_explicit_object_without_active_passes_preflight(self) -> None:
        from app.skills.agent_command_planner import _preflight_clarification

        assert _preflight_clarification(self._input("修改大纲，节奏快一点")) is None

    def test_explicit_episode_beats_mismatched_active(self) -> None:
        """当前看第 2 集但明确说改第 3 集 → 明确目标优先，不澄清。"""
        from app.domain.agent_command import ActiveArtifactContext
        from app.skills.agent_command_planner import _preflight_clarification

        active = ActiveArtifactContext(
            artifact_id=uuid4(),
            artifact_type="script_draft",
            episode_number=2,
        )
        assert _preflight_clarification(self._input("修改第3集", active=active)) is None

    def test_multi_episode_revision_clarifies(self) -> None:
        """一次改多集不偷偷选第一集——追问选择。"""
        from app.skills.agent_command_planner import _preflight_clarification

        output = _preflight_clarification(self._input("修改第2集和第5集"))
        assert output is not None
        assert output.turn_type == "clarification"
        assert "一次修改一个目标" in (output.clarification_question or "")

    def test_pure_reference_without_active_still_clarifies(self) -> None:
        """仅"改这里"且无上下文 → 仍澄清（指代无法服务端解析）。"""
        from app.skills.agent_command_planner import _preflight_clarification

        output = _preflight_clarification(self._input("帮我改一下这里"))
        assert output is not None
        assert output.turn_type == "clarification"


# ========================================================================
# IR-3：正则收敛 + 目标一致性校验（§8.2 / §8.5）
# ========================================================================


class TestPreflightRegexScopeIR3:
    """多集数不再一律判多目标：大纲内条目引用是单对象编辑。"""

    @staticmethod
    def _input(request: str) -> AgentPlannerInput:
        return AgentPlannerInput(
            user_request=request,
            project_title="测试",
            target_episode_count=10,
            available_intents=[
                "create_script", "explain", "evaluate", "revise_script", "revise_outline",
            ],
        )

    def test_outline_merge_with_two_episodes_is_single_target(self) -> None:
        from app.skills.agent_command_planner import _preflight_clarification

        assert _preflight_clarification(self._input("修改大纲，第2集和第3集合并冲突线")) is None
        assert _preflight_clarification(self._input("调整大纲，第 2 集和第 3 集合并冲突线")) is None

    def test_bare_multi_episode_revision_still_clarifies(self) -> None:
        from app.skills.agent_command_planner import _preflight_clarification

        output = _preflight_clarification(self._input("修改第2集和第5集"))
        assert output is not None and output.turn_type == "clarification"

    def test_two_outline_like_objects_still_clarify(self) -> None:
        from app.skills.agent_command_planner import _preflight_clarification

        # 裸"改"不命中修订词表（归模型），用命中词表的复合对象表达
        output = _preflight_clarification(self._input("修改大纲同时修改设定"))
        assert output is not None and output.turn_type == "clarification"

    def test_reasonable_pacing_change_is_not_conflict(self) -> None:
        """§8.8 验收："前慢后快"不被确定性正则误判为冲突。"""
        from app.skills.agent_command_planner import _preflight_clarification

        assert _preflight_clarification(self._input("第2集前半段慢点铺，后半段冲突加快")) is None
        assert _preflight_clarification(self._input("前5集铺垫节奏放慢，后5集加速收线")) is None
        # 真互斥仍然拦截
        output = _preflight_clarification(self._input("既要快节奏又要慢节奏"))
        assert output is not None and output.turn_type == "clarification"


class TestTargetResolutionIR3:
    """resolve_plan_target：文本明确 > 活动上下文 > 模型推断。"""

    @staticmethod
    def _active(ep: int = 2, artifact_type: str = "script_draft") -> ActiveArtifactContext:
        return ActiveArtifactContext(
            artifact_id=uuid4(), artifact_type=artifact_type, episode_number=ep
        )

    def test_explicit_episode_beats_model_and_context(self) -> None:
        from app.domain.agent_planner import resolve_plan_target

        target, kind = resolve_plan_target(
            user_request="修改第3集，反派动机改清楚",
            active_context=self._active(ep=2),
            intent="revise_script",
            model_target=PlannerTarget(target_type="script", episode_number=2),
            target_episode_count=10,
        )
        assert kind == "episode_normalized"
        assert target is not None and target.episode_number == 3

    def test_context_reference_normalizes_to_active_context(self) -> None:
        from app.domain.agent_planner import resolve_plan_target

        target, kind = resolve_plan_target(
            user_request="这里对白太生硬，润色一下",
            active_context=self._active(ep=5),
            intent="revise_script",
            model_target=PlannerTarget(target_type="script", episode_number=9),
            target_episode_count=10,
        )
        assert kind == "context_normalized"
        assert target is not None and target.episode_number == 5

    def test_outline_text_with_revise_script_intent_is_mismatch(self) -> None:
        from app.domain.agent_planner import resolve_plan_target

        target, kind = resolve_plan_target(
            user_request="修改大纲，结尾改团圆",
            active_context=None,
            intent="revise_script",
            model_target=PlannerTarget(target_type="script", episode_number=1),
            target_episode_count=10,
        )
        assert (target, kind) == (None, "object_mismatch")

    def test_outline_episode_references_are_not_target_episodes(self) -> None:
        """revise_outline 文本集数是大纲条目引用，不做集数规范化。"""
        from app.domain.agent_planner import resolve_plan_target

        target, kind = resolve_plan_target(
            user_request="大纲第9集的reveal提前到第7集",
            active_context=None,
            intent="revise_outline",
            model_target=PlannerTarget(target_type="outline"),
            target_episode_count=10,
        )
        assert kind is None
        assert target is not None and target.episode_number is None

    def test_no_conflict_returns_model_target(self) -> None:
        from app.domain.agent_planner import resolve_plan_target

        model_target = PlannerTarget(target_type="script", episode_number=3)
        target, kind = resolve_plan_target(
            user_request="修改第3集剧本",
            active_context=None,
            intent="revise_script",
            model_target=model_target,
            target_episode_count=10,
        )
        assert kind is None and target is model_target

    def test_out_of_range_explicit_episode_not_normalized(self) -> None:
        """越界集数不规范化（preflight 已在模型前拦截，这里防御性跳过）。"""
        from app.domain.agent_planner import resolve_plan_target

        target, kind = resolve_plan_target(
            user_request="修改第15集",
            active_context=None,
            intent="revise_script",
            model_target=PlannerTarget(target_type="script", episode_number=2),
            target_episode_count=10,
        )
        assert kind is None  # 越界交由 preflight/上游处理，不在此扩大执行范围


@pytest.mark.asyncio
class TestReconcileInSkillIR3:
    """Skill 集成：模型输出被服务端规范化或拒绝。"""

    @staticmethod
    def _full_input(request: str) -> AgentPlannerInput:
        return AgentPlannerInput(
            user_request=request,
            target_episode_count=10,
            available_intents=[
                "create_script", "explain", "evaluate", "revise_script", "revise_outline",
            ],
        )

    @staticmethod
    def _register_model(llm: FakeLLM, output: AgentPlannerOutput) -> None:
        llm.register("agent_command_planner", output)

    async def test_model_wrong_episode_normalized_to_text(
        self, agent: BaseAgent, loader: PromptLoader, llm: FakeLLM
    ) -> None:
        self._register_model(
            llm,
            AgentPlannerOutput(
                turn_type="plan",
                intent="revise_script",
                target=PlannerTarget(target_type="script", episode_number=2),
                steps=[PlannerStep(title="修订", description="按约束修订第2集")],
            ),
        )
        result = await AgentCommandPlannerSkill().execute(
            {
                "input": self._full_input("修改第3集剧本"),
                "agent": agent,
                "prompt_loader": loader,
            }
        )
        assert result.turn_type == "plan"
        assert result.target is not None and result.target.episode_number == 3

    async def test_outline_text_with_script_plan_becomes_clarification(
        self, agent: BaseAgent, loader: PromptLoader, llm: FakeLLM
    ) -> None:
        self._register_model(
            llm,
            AgentPlannerOutput(
                turn_type="plan",
                intent="revise_script",
                target=PlannerTarget(target_type="script", episode_number=1),
                steps=[PlannerStep(title="修订", description="改第1集")],
            ),
        )
        result = await AgentCommandPlannerSkill().execute(
            {
                "input": self._full_input("修改大纲，结尾改成悲剧"),
                "agent": agent,
                "prompt_loader": loader,
            }
        )
        assert result.turn_type == "clarification"
        assert result.clarification_question
