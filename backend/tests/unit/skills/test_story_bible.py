"""StoryBibleSkill 单元测试 (C-03).

测试范围:
- 足球需求生成合法 StoryBible
- 主角/反派/配角字段完整性
- locked_facts >= 3
- 角色 ID 稳定性与唯一性
- 同名角色拒绝
- 空 visible_goal 拒绝
- story_rules / open_loops 最小数量
- LLM 故障处理
- CreationAgent.generate_story_bible 集成
"""

from __future__ import annotations

import json

import pytest

from app.agents.base import BaseAgent
from app.agents.creation import CreationAgent
from app.domain.requirement import NormalizedRequirement
from app.domain.story_bible import StoryBible, StoryBibleInput
from app.llm.fake import FakeLLM
from app.prompts.loader import PromptLoader
from app.skills.registry import SkillRegistry
from app.skills.story_bible import StoryBibleSkill, StoryBibleValidationError

# ========================================================================
# Fixtures
# ========================================================================


@pytest.fixture
def fake_llm() -> FakeLLM:
    """创建独立 FakeLLM."""
    return FakeLLM(seed=42)


@pytest.fixture
def agent(fake_llm: FakeLLM) -> BaseAgent:
    """创建使用 FakeLLM 的 BaseAgent."""
    return BaseAgent(name="planner", llm=fake_llm)


@pytest.fixture
def prompt_loader() -> PromptLoader:
    """加载项目真实的 PromptLoader."""
    return PromptLoader()


@pytest.fixture
def skill() -> StoryBibleSkill:
    """创建 StoryBibleSkill 实例."""
    return StoryBibleSkill()


@pytest.fixture
def skill_registry(skill: StoryBibleSkill) -> SkillRegistry:
    """创建已注册 StoryBibleSkill 的 SkillRegistry."""
    registry = SkillRegistry()
    registry.register(skill)
    return registry


@pytest.fixture
def creation_agent(agent: BaseAgent, skill_registry: SkillRegistry) -> CreationAgent:
    """创建 CreationAgent."""
    return CreationAgent(base_agent=agent, skill_registry=skill_registry)


# ========================================================================
# 辅助函数
# ========================================================================


def _football_requirement() -> NormalizedRequirement:
    """返回足球 Idea 的归一化需求 (与 C-02 golden 一致)."""
    return NormalizedRequirement(
        title="足球少年之逆袭人生",
        logline="一个被青训队抛弃的足球少年, 凭借隐藏天赋逆袭进入职业赛场.",
        genre="都市/体育/逆袭",
        tone=["爽文", "热血", "励志"],
        audience="18-35岁男性",
        target_episode_count=10,
        episode_duration_seconds=120,
        protagonist_seed="被青训队抛弃的19岁足球少年林峰, 拥有被忽视的战术视野天赋",
        conflict_seed="林峰进入低级别球队后被前青训队友和资本势力联手打压",
        must_have=["主角逆袭高光时刻", "每集结尾追更钩子"],
        must_avoid=["主角主动使用暴力"],
        source_type="idea",
        assumptions=["默认以当代都市为背景"],
        open_questions=["是否需要加入爱情线?"],
    )


def _valid_story_bible() -> StoryBible:
    """返回与 golden fixture 一致的合法 StoryBible."""
    gold_path = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "golden" / "story_bible_valid.json"
    )
    data = json.loads(gold_path.read_text(encoding="utf-8"))
    return StoryBible.model_validate(data)


# ========================================================================
# StoryBibleSkill 核心测试
# ========================================================================


class TestStoryBibleSkillHappyPath:
    """足球需求 → StoryBible 完整 Happy Path."""

    async def test_generates_valid_story_bible(
        self,
        skill: StoryBibleSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """验收项 1: 主角、反派、至少一个配角字段完整."""
        req = _football_requirement()
        expected = _valid_story_bible()
        fake_llm.register("story_bible", expected)

        sb_input = StoryBibleInput(
            normalized_requirement=req.model_dump(),
        )

        result = await skill.execute({
            "input": sb_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        assert isinstance(result, StoryBible)
        # 主角字段
        assert result.protagonist.name
        assert result.protagonist.character_id
        assert result.protagonist.visible_goal
        assert result.protagonist.traits
        assert result.protagonist.strengths
        assert result.protagonist.flaws
        # 反派字段
        assert result.antagonist.name
        assert result.antagonist.character_id
        assert result.antagonist.visible_goal
        # 至少一个配角
        assert len(result.supporting_characters) >= 1
        for char in result.supporting_characters:
            assert char.name
            assert char.character_id
            assert char.visible_goal

    async def test_locked_facts_minimum(
        self,
        skill: StoryBibleSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """验收项 2: locked_facts 至少 3 条."""
        req = _football_requirement()
        expected = _valid_story_bible()
        fake_llm.register("story_bible", expected)

        sb_input = StoryBibleInput(
            normalized_requirement=req.model_dump(),
        )

        result = await skill.execute({
            "input": sb_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        assert len(result.locked_facts) >= 3

    async def test_character_ids_stable(
        self,
        skill: StoryBibleSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """验收项 3: 角色 ID 以 char_ 开头, 可在 fixture 中引用."""
        req = _football_requirement()
        expected = _valid_story_bible()
        fake_llm.register("story_bible", expected)

        sb_input = StoryBibleInput(
            normalized_requirement=req.model_dump(),
        )

        result = await skill.execute({
            "input": sb_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        all_chars = [result.protagonist, result.antagonist] + result.supporting_characters
        for char in all_chars:
            assert char.character_id.startswith("char_"), (
                f"角色 '{char.name}' ID='{char.character_id}' 不以 'char_' 开头"
            )

        # 验证 ID 唯一且可引用
        all_ids = [c.character_id for c in all_chars]
        assert len(all_ids) == len(set(all_ids)), f"角色 ID 重复: {all_ids}"

    async def test_story_rules_and_open_loops(
        self,
        skill: StoryBibleSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """story_rules >= 3, open_loops >= 1."""
        req = _football_requirement()
        expected = _valid_story_bible()
        fake_llm.register("story_bible", expected)

        sb_input = StoryBibleInput(
            normalized_requirement=req.model_dump(),
        )

        result = await skill.execute({
            "input": sb_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        assert len(result.story_rules) >= 3, f"story_rules 只有 {len(result.story_rules)} 条"
        assert len(result.open_loops) >= 1, f"open_loops 只有 {len(result.open_loops)} 条"


class TestStoryBibleSkillValidation:
    """后校验——各种失败场景."""

    async def test_rejects_missing_locked_facts(
        self,
        skill: StoryBibleSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """locked_facts < 3 → StoryBibleValidationError."""
        req = _football_requirement()
        bad = _valid_story_bible()
        bad.locked_facts = ["只有一条"]
        fake_llm.register("story_bible", bad)

        sb_input = StoryBibleInput(
            normalized_requirement=req.model_dump(),
        )

        with pytest.raises(StoryBibleValidationError, match="locked_facts"):
            await skill.execute({
                "input": sb_input,
                "agent": agent,
                "prompt_loader": prompt_loader,
            })

    async def test_rejects_empty_traits(
        self,
        skill: StoryBibleSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """角色 traits 为空 → Skill 层校验失败 (Pydantic 允许空 list)."""
        req = _football_requirement()
        bad = _valid_story_bible()
        bad.protagonist.traits = []  # Pydantic 不拒绝, 但 Skill 要求至少一个
        fake_llm.register("story_bible", bad)

        sb_input = StoryBibleInput(
            normalized_requirement=req.model_dump(),
        )

        with pytest.raises(StoryBibleValidationError, match="traits"):
            await skill.execute({
                "input": sb_input,
                "agent": agent,
                "prompt_loader": prompt_loader,
            })

    async def test_rejects_duplicate_character_names(
        self,
        skill: StoryBibleSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """同名角色 → 校验失败."""
        req = _football_requirement()
        bad = _valid_story_bible()
        # 把反派名改成和主角一样
        bad.antagonist.name = bad.protagonist.name
        # 同时需要不同的 character_id 才能通过 Pydantic 校验
        fake_llm.register("story_bible", bad)

        sb_input = StoryBibleInput(
            normalized_requirement=req.model_dump(),
        )

        with pytest.raises(StoryBibleValidationError, match="同名角色"):
            await skill.execute({
                "input": sb_input,
                "agent": agent,
                "prompt_loader": prompt_loader,
            })

    async def test_rejects_duplicate_character_ids(
        self,
        skill: StoryBibleSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """重复 character_id → 校验失败.

        注: Pydantic model_validator 已检查主角/反派和配角 ID 重复,
        此测试确保 Skill 层也做了保护.
        """
        req = _football_requirement()
        bad = _valid_story_bible()
        # 配角使用与主角相同的 ID
        if bad.supporting_characters:
            bad.supporting_characters[0].character_id = bad.protagonist.character_id
        fake_llm.register("story_bible", bad)

        sb_input = StoryBibleInput(
            normalized_requirement=req.model_dump(),
        )

        # Pydantic 校验会捕获主角/反派 ID 冲突, Skill 捕获配角冲突
        with pytest.raises((StoryBibleValidationError, ValueError)):
            await skill.execute({
                "input": sb_input,
                "agent": agent,
                "prompt_loader": prompt_loader,
            })

    async def test_rejects_unstable_character_id(
        self,
        skill: StoryBibleSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """角色 ID 不以 char_ 开头 → 校验失败."""
        req = _football_requirement()
        bad = _valid_story_bible()
        bad.protagonist.character_id = "random_id_123"
        # 需要确保不重复
        fake_llm.register("story_bible", bad)

        sb_input = StoryBibleInput(
            normalized_requirement=req.model_dump(),
        )

        with pytest.raises(StoryBibleValidationError, match="char_"):
            await skill.execute({
                "input": sb_input,
                "agent": agent,
                "prompt_loader": prompt_loader,
            })


class TestStoryBibleSkillLLMFailure:
    """LLM 调用失败处理."""

    async def test_llm_error_raises(
        self,
        skill: StoryBibleSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """LLM 超时 → RuntimeError."""
        req = _football_requirement()
        fake_llm.inject_fault(1, "timeout")

        sb_input = StoryBibleInput(
            normalized_requirement=req.model_dump(),
        )

        with pytest.raises(RuntimeError, match="LLM"):
            await skill.execute({
                "input": sb_input,
                "agent": agent,
                "prompt_loader": prompt_loader,
            })


# ========================================================================
# CreationAgent 集成测试
# ========================================================================


class TestCreationAgent:
    """CreationAgent.generate_story_bible 集成测试 (C-03)."""

    async def test_generate_story_bible(
        self,
        creation_agent: CreationAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """CreationAgent 端到端流程: 需求 → StoryBible."""
        req = _football_requirement()
        expected = _valid_story_bible()
        fake_llm.register("story_bible", expected)

        result = await creation_agent.generate_story_bible(
            normalized_requirement=req,
            prompt_loader=prompt_loader,
        )

        assert isinstance(result, StoryBible)
        assert result.title == expected.title
        assert result.protagonist.character_id.startswith("char_")
        assert result.antagonist.character_id.startswith("char_")
        assert len(result.supporting_characters) >= 1
        assert len(result.locked_facts) >= 3

    async def test_generate_story_bible_with_rag(
        self,
        creation_agent: CreationAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """带 RAG 上下文的 StoryBible 生成."""
        req = _football_requirement()
        expected = _valid_story_bible()
        fake_llm.register("story_bible", expected)

        result = await creation_agent.generate_story_bible(
            normalized_requirement=req,
            prompt_loader=prompt_loader,
            rag_context="参考知识: 中超联赛结构, 青训体系标准",
        )

        assert isinstance(result, StoryBible)
        assert len(result.locked_facts) >= 3


# ========================================================================
# StoryBibleInput 模型测试
# ========================================================================


class TestStoryBibleInput:
    """StoryBibleInput 模型基本测试."""

    def test_create_with_requirement(self) -> None:
        """可以用 dict 和 RAG 上下文创建."""
        req = _football_requirement()
        inp = StoryBibleInput(
            normalized_requirement=req.model_dump(),
            rag_context="测试上下文",
        )
        assert inp.rag_context == "测试上下文"
        assert inp.normalized_requirement["title"] == req.title

    def test_default_rag_context(self) -> None:
        """RAG 上下文默认为空字符串."""
        req = _football_requirement()
        inp = StoryBibleInput(
            normalized_requirement=req.model_dump(),
        )
        assert inp.rag_context == ""
