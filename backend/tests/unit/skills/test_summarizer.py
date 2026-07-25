"""SummarizerSkill 单元测试 (C-06).

测试范围:
- 从 ScriptDraft 生成 EpisodeSummary
- SummaryOutput 校验
- 角色状态变化提取
- 伏笔开启/回收提取
- 时间线事件提取
- LLM 故障处理
- 辅助转换函数
"""

from __future__ import annotations

import pytest

from app.agents.base import BaseAgent
from app.domain.continuity import EpisodeSummary
from app.domain.summary import SummaryInput, SummaryOutput
from app.llm.fake import FakeLLM
from app.prompts.loader import PromptLoader
from app.skills.summarizer import (
    SummarizerSkill,
    SummarizerValidationError,
    extract_new_story_loops,
    extract_timeline_events,
    summary_output_to_episode_summary,
)

# ========================================================================
# Fixtures
# ========================================================================


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM(seed=42)


@pytest.fixture
def agent(fake_llm: FakeLLM) -> BaseAgent:
    return BaseAgent(name="summarizer", llm=fake_llm)


@pytest.fixture
def prompt_loader() -> PromptLoader:
    return PromptLoader()


@pytest.fixture
def skill() -> SummarizerSkill:
    return SummarizerSkill()


@pytest.fixture
def valid_summary_output_data() -> dict[str, object]:
    """合法的 SummaryOutput 测试数据。"""
    return {
        "episode_number": 1,
        "summary": "林峰被青训队开除后偶遇赵指导，重新燃起希望。",
        "key_events": ["林峰被陈教练开除", "林峰偶遇赵指导"],
        "ending_state": "林峰决心重新开始，加入业余球队",
        "character_changes": [
            {
                "character_id": "char_lin_feng",
                "name": "林峰",
                "field": "emotional_state",
                "changes": "从沮丧变为充满希望",
            },
        ],
        "new_loops": [
            {
                "loop_id": "loop_003",
                "description": "赵指导的真实身份和目的",
            },
        ],
        "resolved_loops": [],  # 第 1 集无回收
        "timeline_events": [
            {
                "event_id": "tl_1_001",
                "description": "林峰在训练中迟到被陈教练批评",
                "order_in_episode": 1,
            },
            {
                "event_id": "tl_1_002",
                "description": "陈教练宣布开除林峰",
                "order_in_episode": 2,
            },
            {
                "event_id": "tl_1_003",
                "description": "林峰在公园偶遇赵指导",
                "order_in_episode": 3,
            },
        ],
    }


# ========================================================================
# 正常流程
# ========================================================================


class TestSummarizerSkill:
    """SummarizerSkill 正常流程测试。"""

    async def test_generates_summary_output(
        self,
        skill: SummarizerSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        valid_summary_output_data: dict[str, object],
    ) -> None:
        """使用 FakeLLM 生成合法的 SummaryOutput。"""
        agent.llm.register(  # type: ignore[attr-defined]
            "summarize_episode",
            SummaryOutput.model_validate(valid_summary_output_data),
        )

        sm_input = SummaryInput(
            episode_number=1,
            script_draft={
                "title": "测试剧本",
                "scenes": [{"scene_number": 1, "action": "test"}],
            },
            continuity_state={},
        )

        context = {
            "input": sm_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
        }

        result = await skill.execute(context)

        assert isinstance(result, SummaryOutput)
        assert result.episode_number == 1
        assert len(result.summary) > 0
        assert len(result.key_events) >= 2
        assert len(result.character_changes) > 0
        assert len(result.timeline_events) >= 2

    async def test_extracts_character_changes(
        self,
        skill: SummarizerSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        valid_summary_output_data: dict[str, object],
    ) -> None:
        """角色变化数据完整提取。"""
        agent.llm.register(  # type: ignore[attr-defined]
            "summarize_episode",
            SummaryOutput.model_validate(valid_summary_output_data),
        )

        sm_input = SummaryInput(
            episode_number=1,
            script_draft={"title": "test"},
            continuity_state={},
        )

        result = await skill.execute({
            "input": sm_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        assert len(result.character_changes) == 1
        cc = result.character_changes[0]
        assert cc["character_id"] == "char_lin_feng"
        assert cc["field"] == "emotional_state"

    async def test_extracts_new_loops(
        self,
        skill: SummarizerSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        valid_summary_output_data: dict[str, object],
    ) -> None:
        """新伏笔数据完整提取。"""
        agent.llm.register(  # type: ignore[attr-defined]
            "summarize_episode",
            SummaryOutput.model_validate(valid_summary_output_data),
        )

        sm_input = SummaryInput(
            episode_number=1,
            script_draft={"title": "test"},
            continuity_state={},
        )

        result = await skill.execute({
            "input": sm_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        assert len(result.new_loops) == 1
        assert result.new_loops[0]["loop_id"] == "loop_003"

    async def test_extracts_timeline_events(
        self,
        skill: SummarizerSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        valid_summary_output_data: dict[str, object],
    ) -> None:
        """时间线事件完整提取。"""
        agent.llm.register(  # type: ignore[attr-defined]
            "summarize_episode",
            SummaryOutput.model_validate(valid_summary_output_data),
        )

        sm_input = SummaryInput(
            episode_number=1,
            script_draft={"title": "test"},
            continuity_state={},
        )

        result = await skill.execute({
            "input": sm_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        assert len(result.timeline_events) == 3
        assert result.timeline_events[0]["order_in_episode"] == 1
        assert result.timeline_events[2]["order_in_episode"] == 3

    async def test_resolved_loops_tracked(
        self,
        skill: SummarizerSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        """回收伏笔被正确追踪。"""
        data_with_resolved = {
            "episode_number": 3,
            "summary": "林峰在关键比赛中证明自己，赵指导身份揭晓。",
            "key_events": ["林峰进球", "赵指导身份揭晓"],
            "ending_state": "林峰得到父亲认可",
            "character_changes": [
                {
                    "character_id": "char_lin_feng",
                    "name": "林峰",
                    "field": "emotional_state",
                    "changes": "从不被认可到获得认可",
                },
            ],
            "new_loops": [],
            "resolved_loops": ["loop_001", "loop_003"],
            "timeline_events": [
                {
                    "event_id": "tl_3_001",
                    "description": "林峰进球",
                    "order_in_episode": 1,
                },
            ],
        }
        agent.llm.register(  # type: ignore[attr-defined]
            "summarize_episode",
            SummaryOutput.model_validate(data_with_resolved),
        )

        sm_input = SummaryInput(
            episode_number=3,
            script_draft={"title": "test"},
            continuity_state={},
        )

        result = await skill.execute({
            "input": sm_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        assert len(result.resolved_loops) == 2
        assert "loop_001" in result.resolved_loops
        assert "loop_003" in result.resolved_loops


# ========================================================================
# 校验失败
# ========================================================================


class TestSummarizerValidation:
    """输出校验失败处理。"""

    def test_rejects_empty_summary(self, skill: SummarizerSkill) -> None:
        """summary 为空时抛出校验错误。"""
        # 使用 model_construct 创建对象绕过 Pydantic 校验
        invalid_output = SummaryOutput.model_construct(
            episode_number=1,
            summary="",  # 空摘要
            key_events=["事件 1"],
            ending_state="结束",
            character_changes=[],
            new_loops=[],
            resolved_loops=[],
            timeline_events=[],
        )

        with pytest.raises(SummarizerValidationError, match="summary"):
            skill._validate_output(invalid_output, 1)

    async def test_rejects_mismatched_episode_number(
        self,
        skill: SummarizerSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        """episode_number 不匹配时抛出校验错误。"""
        agent.llm.register(  # type: ignore[attr-defined]
            "summarize_episode",
            SummaryOutput.model_validate({
                "episode_number": 99,  # 期望是 1
                "summary": "摘要",
                "key_events": ["事件 1"],
                "ending_state": "结束",
                "character_changes": [],
                "new_loops": [],
                "resolved_loops": [],
                "timeline_events": [],
            }),
        )

        sm_input = SummaryInput(
            episode_number=1,
            script_draft={"title": "test"},
            continuity_state={},
        )

        with pytest.raises(SummarizerValidationError):
            await skill.execute({
                "input": sm_input,
                "agent": agent,
                "prompt_loader": prompt_loader,
            })

    async def test_rejects_character_change_without_id(
        self,
        skill: SummarizerSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        """character_changes 条目缺少 character_id 时抛出校验错误。"""
        agent.llm.register(  # type: ignore[attr-defined]
            "summarize_episode",
            SummaryOutput.model_validate({
                "episode_number": 1,
                "summary": "摘要",
                "key_events": ["事件"],
                "ending_state": "结束",
                "character_changes": [
                    {"name": "林峰", "changes": "变化"},  # 缺 character_id
                ],
                "new_loops": [],
                "resolved_loops": [],
                "timeline_events": [],
            }),
        )

        sm_input = SummaryInput(
            episode_number=1,
            script_draft={"title": "test"},
            continuity_state={},
        )

        with pytest.raises(SummarizerValidationError, match="character_id"):
            await skill.execute({
                "input": sm_input,
                "agent": agent,
                "prompt_loader": prompt_loader,
            })

    async def test_rejects_new_loop_without_id(
        self,
        skill: SummarizerSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        """new_loops 条目缺少 loop_id 时抛出校验错误。"""
        agent.llm.register(  # type: ignore[attr-defined]
            "summarize_episode",
            SummaryOutput.model_validate({
                "episode_number": 1,
                "summary": "摘要",
                "key_events": ["事件"],
                "ending_state": "结束",
                "character_changes": [],
                "new_loops": [
                    {"description": "没有 ID 的伏笔"},  # 缺 loop_id
                ],
                "resolved_loops": [],
                "timeline_events": [],
            }),
        )

        sm_input = SummaryInput(
            episode_number=1,
            script_draft={"title": "test"},
            continuity_state={},
        )

        with pytest.raises(SummarizerValidationError, match="loop_id"):
            await skill.execute({
                "input": sm_input,
                "agent": agent,
                "prompt_loader": prompt_loader,
            })


# ========================================================================
# LLM 故障处理
# ========================================================================


class TestSummarizerLLMFailure:
    """LLM 调用失败处理。"""

    async def test_raises_on_llm_failure(
        self,
        skill: SummarizerSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        """LLM 返回错误时抛出 RuntimeError。"""
        agent.llm.register(  # type: ignore[attr-defined]
            "summarize_episode",
            None,  # None 表示无效输出
        )

        sm_input = SummaryInput(
            episode_number=1,
            script_draft={"title": "test"},
            continuity_state={},
        )

        with pytest.raises(RuntimeError):
            await skill.execute({
                "input": sm_input,
                "agent": agent,
                "prompt_loader": prompt_loader,
            })


# ========================================================================
# 辅助函数
# ========================================================================


class TestHelperFunctions:
    """辅助转换函数测试。"""

    def test_summary_output_to_episode_summary(self) -> None:
        """SummaryOutput 正确转换为 EpisodeSummary。"""
        output = SummaryOutput(
            episode_number=2,
            summary="测试摘要",
            key_events=["事件 A", "事件 B"],
            ending_state="角色已转变",
        )

        result = summary_output_to_episode_summary(output)

        assert isinstance(result, EpisodeSummary)
        assert result.episode_number == 2
        assert result.summary == "测试摘要"
        assert result.key_events == ["事件 A", "事件 B"]
        assert result.ending_state == "角色已转变"

    def test_extract_new_story_loops(self) -> None:
        """从 SummaryOutput 正确提取 StoryLoop。"""
        output = SummaryOutput(
            episode_number=3,
            summary="摘要",
            key_events=[],
            ending_state="",
            new_loops=[
                {"loop_id": "loop_010", "description": "神秘人身份"},
                {"loop_id": "loop_011", "description": "隐藏的伤病"},
            ],
        )

        loops = extract_new_story_loops(output, 3)

        assert len(loops) == 2
        assert loops[0].loop_id == "loop_010"
        assert loops[0].status == "open"
        assert loops[0].introduced_episode == 3
        assert loops[1].description == "隐藏的伤病"

    def test_extract_timeline_events(self) -> None:
        """从 SummaryOutput 正确提取 TimelineEvent。"""
        output = SummaryOutput(
            episode_number=1,
            summary="摘要",
            key_events=[],
            ending_state="",
            timeline_events=[
                {
                    "event_id": "tl_1_001",
                    "description": "开场事件",
                    "order_in_episode": 1,
                },
                {
                    "event_id": "tl_1_002",
                    "description": "高潮事件",
                    "order_in_episode": 2,
                },
            ],
        )

        events = extract_timeline_events(output, 1)

        assert len(events) == 2
        assert events[0].event_id == "tl_1_001"
        assert events[0].episode_number == 1
        assert events[0].order_in_episode == 1
        assert events[1].description == "高潮事件"

    def test_extract_timeline_events_fallback_ids(self) -> None:
        """缺少 event_id 时使用默认生成的 ID。"""
        output = SummaryOutput(
            episode_number=2,
            summary="摘要",
            key_events=[],
            ending_state="",
            timeline_events=[
                {"description": "事件 1", "order_in_episode": 1},
                {"description": "事件 2", "order_in_episode": 2},
            ],
        )

        events = extract_timeline_events(output, 2)

        assert events[0].event_id == "tl_2_001"
        assert events[1].event_id == "tl_2_002"
