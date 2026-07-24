"""RequirementSkill 单元测试 (C-02).

测试范围:
- 足球 Idea 生成合法 NormalizedRequirement
- 缺主角信息 → NeedsUserInput
- 缺核心冲突 → NeedsUserInput
- target_episode_count 范围校验
- must_have 保留
- source_type 处理
- 非关键缺省生成 assumptions
"""

from __future__ import annotations

import pytest

from app.agents.base import BaseAgent
from app.domain.requirement import NeedsUserInput, NormalizedRequirement, RequirementInput
from app.llm.fake import FakeLLM
from app.prompts.loader import PromptLoader
from app.skills.requirement import (
    RequirementSkill,
    _has_conflict_info,
    _has_protagonist_info,
)

# ========================================================================
# Fixtures
# ========================================================================


@pytest.fixture
def fake_llm() -> FakeLLM:
    """创建 FakeLLM 实例 (每次测试独立)."""
    return FakeLLM(seed=42)


@pytest.fixture
def agent(fake_llm: FakeLLM) -> BaseAgent:
    """创建使用 FakeLLM 的 BaseAgent."""
    return BaseAgent(name="normalizer", llm=fake_llm)


@pytest.fixture
def prompt_loader() -> PromptLoader:
    """加载项目真实的 PromptLoader."""
    return PromptLoader()


@pytest.fixture
def skill() -> RequirementSkill:
    """创建 RequirementSkill 实例."""
    return RequirementSkill()


# ========================================================================
# 辅助函数: 构造足球 Idea 的 NormalizedRequirement fixture
# ========================================================================


def _football_result() -> NormalizedRequirement:
    """返回一个合法的足球 Idea 归一化结果 (golden 等价物)."""
    return NormalizedRequirement(
        title="足球少年之逆袭人生",
        logline="一个被青训队抛弃的足球少年, 凭借隐藏天赋逆袭进入职业赛场, 在强敌压迫中不断突破自我.",
        genre="都市/体育/逆袭",
        tone=["爽文", "热血", "励志"],
        audience="18-35岁男性",
        target_episode_count=10,
        episode_duration_seconds=120,
        protagonist_seed="被青训队抛弃的19岁足球少年林峰, 拥有被忽视的战术视野天赋",
        conflict_seed="林峰进入低级别球队后被前青训队友和资本势力联手打压",
        must_have=["主角逆袭高光时刻", "每集结尾追更钩子", "至少一场完整比赛描写"],
        must_avoid=["主角主动使用暴力", "涉及真实球队名称"],
        source_type="idea",
        assumptions=["默认以当代都市为背景", "默认职业联赛体系参考中超结构"],
        open_questions=["是否需要加入爱情线?", "逆袭的最终目标是否为入选国家队?"],
    )


# ========================================================================
# 关键词检测单元测试
# ========================================================================


class TestKeywordDetection:
    """主角/冲突关键词检测函数测试."""

    def test_detect_protagonist_keywords(self) -> None:
        """常见主角关键词能被检测."""
        for kw in ["主角", "少年", "总裁", "神医", "穿越者"]:
            assert _has_protagonist_info(f"这是一个关于{kw}的故事"), f"未检测到关键词 '{kw}'"

    def test_detect_conflict_keywords(self) -> None:
        """常见冲突关键词能被检测."""
        for kw in ["逆袭", "复仇", "觉醒", "重生"]:
            assert _has_conflict_info(f"一个{kw}的故事"), f"未检测到关键词 '{kw}'"

    def test_no_protagonist_info(self) -> None:
        """纯题材描述不含主角信息."""
        assert not _has_protagonist_info("一个发生在都市的爱情故事")

    def test_no_conflict_info(self) -> None:
        """纯场景描述不含冲突信息."""
        assert not _has_conflict_info("两个人在咖啡店聊天")


# ========================================================================
# RequirementSkill 核心测试
# ========================================================================


class TestRequirementSkillFootball:
    """足球 Idea 场景——完整 Happy Path."""

    @pytest.fixture
    def input_data(self) -> RequirementInput:
        """足球 Idea 输入."""
        return RequirementInput(
            user_input=(
                "写一个关于被青训队抛弃的足球少年的逆袭故事。"
                "主角利用被忽视的战术天赋进入职业赛场, "
                "在前队友和资本势力的打压下不断突破, 最终证明自己。"
            ),
            source_type="idea",
            target_episode_count=10,
            episode_duration_seconds=120,
        )

    async def test_football_idea_returns_normalized_requirement(
        self,
        skill: RequirementSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
        input_data: RequirementInput,
    ) -> None:
        """验收项 1: 足球 Idea 生成合法 NormalizedRequirement."""
        # 注册 fake fixture
        expected = _football_result()
        fake_llm.register("normalize_requirement", expected)

        result = await skill.execute({
            "input": input_data,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        assert isinstance(result, NormalizedRequirement)
        assert result.title == expected.title
        assert result.genre == expected.genre
        assert "足球" in result.logline or "足球" in result.protagonist_seed
        assert len(result.must_have) >= 1
        # source_type 由 Skill 传入 LLM, 期望输出一致
        assert result.source_type == "idea"

    async def test_football_idea_preserves_must_have(
        self,
        skill: RequirementSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
        input_data: RequirementInput,
    ) -> None:
        """验收项 4: 原始用户要求中的 must_have 不丢失."""
        expected = _football_result()
        # 验证 golden 数据的 must_have
        expected.must_have = [
            "主角逆袭高光时刻", "每集结尾追更钩子", "至少一场完整比赛描写",
        ]
        fake_llm.register("normalize_requirement", expected)

        result = await skill.execute({
            "input": input_data,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        assert isinstance(result, NormalizedRequirement)
        for item in expected.must_have:
            assert item in result.must_have, f"must_have 项 '{item}' 丢失"


class TestRequirementSkillBlocking:
    """关键信息缺失 → NeedsUserInput 阻断测试."""

    @pytest.fixture
    def prompt_loader(self) -> PromptLoader:
        return PromptLoader()

    async def test_missing_protagonist_returns_needs_user_input(
        self,
        skill: RequirementSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        """验收项 2: 缺主角信息 → NeedsUserInput, 不调用 LLM."""
        req_input = RequirementInput(
            user_input="写一个好看的短剧",
            source_type="idea",
        )
        result = await skill.execute({
            "input": req_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        assert isinstance(result, NeedsUserInput)
        assert "protagonist_seed" in result.missing_fields
        assert len(result.questions) >= 1

    async def test_missing_conflict_returns_needs_user_input(
        self,
        skill: RequirementSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        """验收项 2: 缺核心冲突 → NeedsUserInput, 不调用 LLM."""
        req_input = RequirementInput(
            user_input="一个总裁和他的秘书的故事",
            source_type="idea",
        )
        result = await skill.execute({
            "input": req_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        assert isinstance(result, NeedsUserInput)
        assert "conflict_seed" in result.missing_fields

    async def test_very_short_input_blocked(
        self,
        skill: RequirementSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        """极短输入 (< 最小长度) 直接阻断."""
        req_input = RequirementInput(
            user_input="短剧",
            source_type="idea",
        )
        result = await skill.execute({
            "input": req_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        assert isinstance(result, NeedsUserInput)
        assert any("短" in q or "过短" in q or "长度" in q
                   for mf in result.missing_fields for q in [mf])


class TestRequirementSkillTargetEpisodeCount:
    """target_episode_count 范围校验."""

    async def test_out_of_range_fixed(
        self,
        skill: RequirementSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """验收项 3: LLM 输出越界的 target_episode_count 被强制修正."""
        req_input = RequirementInput(
            user_input="一个被退婚的少年逆袭修仙的故事, 主角觉醒上古血脉一路碾压对手",
            source_type="idea",
            target_episode_count=10,
        )

        bad_result = _football_result()
        bad_result.target_episode_count = 999  # 越界值
        fake_llm.register("normalize_requirement", bad_result)

        result = await skill.execute({
            "input": req_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        assert isinstance(result, NormalizedRequirement)
        # 应被修正为输入指定的值
        assert result.target_episode_count == 10

    async def test_zero_count_fixed(
        self,
        skill: RequirementSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """LLM 输出 target_episode_count=0 被修正 (ge=1 约束由 Pydantic 保证, Skill 修复 >=1 但越界的值)."""
        req_input = RequirementInput(
            user_input="一个被退婚的少年逆袭修仙的故事",
            source_type="idea",
            target_episode_count=10,
        )

        bad_result = _football_result()
        bad_result.target_episode_count = 200  # 超出 le=100 范围
        fake_llm.register("normalize_requirement", bad_result)

        result = await skill.execute({
            "input": req_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        assert isinstance(result, NormalizedRequirement)
        # 应被修正为输入指定的值
        assert result.target_episode_count == 10


class TestRequirementSkillSourceType:
    """source_type 处理测试."""

    async def test_outline_source_type(
        self,
        skill: RequirementSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """outline 类型的输入也能正常处理."""
        req_input = RequirementInput(
            user_input=(
                "第一集: 少年被退婚羞辱, 意外觉醒上古血脉。"
                "第二集: 苦修一年后打败当初的未婚妻家族。"
                "这是一个逆袭修仙大纲, 主角是穿越重生的废柴少年。"
            ),
            source_type="outline",
        )

        expected = _football_result()
        expected.source_type = "outline"
        fake_llm.register("normalize_requirement", expected)

        result = await skill.execute({
            "input": req_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        assert isinstance(result, NormalizedRequirement)
        assert result.source_type == "outline"
        assert result.target_episode_count == 10


class TestRequirementSkillAssumptions:
    """非关键缺省 → assumptions 生成测试."""

    async def test_assumptions_generated_for_defaults(
        self,
        skill: RequirementSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """非关键字段 (受众、时长等) 由 LLM 填入 assumptions."""
        req_input = RequirementInput(
            user_input="一个被退婚的少年逆袭修仙的故事, 主角觉醒上古血脉一路碾压对手",
            source_type="idea",
        )

        expected = _football_result()
        expected.audience = None
        expected.assumptions = ["受众默认为18-35岁男性", "单集时长默认120秒"]
        fake_llm.register("normalize_requirement", expected)

        result = await skill.execute({
            "input": req_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        assert isinstance(result, NormalizedRequirement)
        assert len(result.assumptions) >= 1
        # 假设中应记录非关键默认值
        assert any(
            "受众" in a or "时长" in a or "背景" in a
            for a in result.assumptions
        )


class TestRequirementSkillLLMFailure:
    """LLM 调用失败处理."""

    async def test_llm_error_raises(
        self,
        skill: RequirementSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """LLM 返回错误时 Skill 抛出 RuntimeError."""
        req_input = RequirementInput(
            user_input="一个被退婚的少年逆袭修仙的故事, 主角觉醒上古血脉",
            source_type="idea",
        )
        fake_llm.inject_fault(1, "timeout")

        with pytest.raises(RuntimeError, match="LLM"):
            await skill.execute({
                "input": req_input,
                "agent": agent,
                "prompt_loader": prompt_loader,
            })
