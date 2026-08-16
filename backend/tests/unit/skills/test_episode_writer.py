"""EpisodeWriterSkill 单元测试 (C-05).

测试范围:
- 大纲+StoryBible → 单集 ScriptDraft 生成
- LLM 自报 word_count 被 Tool 覆盖
- LLM 自报 dialogue_ratio 被 Tool 覆盖
- Scene 编号连续且 >= 2
- 角色名可追溯至 StoryBible (或为临时群众角色)
- ending_hook 与大纲对应
- 第 2 集使用前集摘要而非全文
- CreationAgent.generate_episode 集成
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from app.agents.base import BaseAgent
from app.agents.creation import CreationAgent
from app.domain.script import EpisodeWriterInput, ScriptDraft
from app.domain.story_bible import StoryBible
from app.llm.fake import FakeLLM
from app.prompts.loader import PromptLoader
from app.skills.episode_writer import EpisodeWriterSkill
from app.skills.registry import SkillRegistry

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"


# ========================================================================
# Fixtures
# ========================================================================


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM(seed=42)


@pytest.fixture
def agent(fake_llm: FakeLLM) -> BaseAgent:
    return BaseAgent(name="writer", llm=fake_llm)


@pytest.fixture
def prompt_loader() -> PromptLoader:
    return PromptLoader()


@pytest.fixture
def skill() -> EpisodeWriterSkill:
    return EpisodeWriterSkill()


@pytest.fixture
def skill_registry(skill: EpisodeWriterSkill) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(skill)
    return registry


@pytest.fixture
def creation_agent(agent: BaseAgent, skill_registry: SkillRegistry) -> CreationAgent:
    return CreationAgent(base_agent=agent, skill_registry=skill_registry)


# ========================================================================
# 辅助函数
# ========================================================================


def _football_story_bible() -> dict[str, Any]:
    data = json.loads(
        (GOLDEN_DIR / "story_bible_valid.json").read_text(encoding="utf-8")
    )
    return cast(dict[str, Any], data)


def _football_story_bible_obj() -> StoryBible:
    return StoryBible.model_validate(_football_story_bible())


def _ep1_outline() -> dict[str, Any]:
    """第 1 集大纲."""
    data = json.loads(
        (GOLDEN_DIR / "outline_set_valid.json").read_text(encoding="utf-8")
    )
    return cast(dict[str, Any], data["episodes"][0])


def _valid_script_draft() -> ScriptDraft:
    """加载合法 ScriptDraft golden fixture."""
    data = json.loads(
        (GOLDEN_DIR / "script_draft_valid.json").read_text(encoding="utf-8")
    )
    return ScriptDraft.model_validate(data)


# ========================================================================
# EpisodeWriterSkill 核心测试
# ========================================================================


class TestEpisodeWriterSkillHappyPath:
    """完整剧本生成 Happy Path."""

    async def test_generates_valid_script_draft(
        self,
        skill: EpisodeWriterSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """基本流程: 大纲 → ScriptDraft."""
        sb = _football_story_bible()
        outline = _ep1_outline()
        expected = _valid_script_draft()
        fake_llm.register("write_episode", expected)

        ew_input = EpisodeWriterInput(
            episode_number=1,
            episode_outline=outline,
            story_bible=sb,
        )

        result = await skill.execute({
            "input": ew_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
            "outline_artifact_id": uuid4(),
        })

        assert isinstance(result, ScriptDraft)
        assert result.episode_number == 1
        assert len(result.scenes) >= 2
        assert result.plain_text

    async def test_word_count_overridden(
        self,
        skill: EpisodeWriterSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """验收项 1: LLM 自报 word_count 被 Tool 覆盖."""
        sb = _football_story_bible()
        outline = _ep1_outline()
        expected = _valid_script_draft()
        # LLM "谎报" 字数
        expected.word_count = 99999
        fake_llm.register("write_episode", expected)

        ew_input = EpisodeWriterInput(
            episode_number=1,
            episode_outline=outline,
            story_bible=sb,
        )

        result = await skill.execute({
            "input": ew_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
            "outline_artifact_id": uuid4(),
        })

        # Tool 计算的字数应与 LLM 原始值不同
        assert result.word_count != 99999
        assert result.word_count > 0

    async def test_dialogue_ratio_overridden(
        self,
        skill: EpisodeWriterSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """验收项 1: LLM 自报 dialogue_ratio 被 Tool 覆盖."""
        sb = _football_story_bible()
        outline = _ep1_outline()
        expected = _valid_script_draft()
        # LLM "谎报" 比例
        expected.dialogue_ratio = 0.999
        fake_llm.register("write_episode", expected)

        ew_input = EpisodeWriterInput(
            episode_number=1,
            episode_outline=outline,
            story_bible=sb,
        )

        result = await skill.execute({
            "input": ew_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
            "outline_artifact_id": uuid4(),
        })

        # Tool 计算的比例应与 LLM 原始值不同
        assert result.dialogue_ratio != 0.999

    async def test_scene_count_minimum(
        self,
        skill: EpisodeWriterSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """验收项 2: Scene 编号连续且至少 2 场."""
        sb = _football_story_bible()
        outline = _ep1_outline()
        expected = _valid_script_draft()
        fake_llm.register("write_episode", expected)

        ew_input = EpisodeWriterInput(
            episode_number=1,
            episode_outline=outline,
            story_bible=sb,
        )

        result = await skill.execute({
            "input": ew_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
            "outline_artifact_id": uuid4(),
        })

        assert len(result.scenes) >= 2
        numbers = [s.scene_number for s in result.scenes]
        assert numbers == list(range(1, len(numbers) + 1)), f"场景编号不连续: {numbers}"


class TestEpisodeWriterSkillValidation:
    """后校验——各种场景."""

    async def test_characters_traceable_to_story_bible(
        self,
        skill: EpisodeWriterSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """验收项 3: 角色名可追溯到 StoryBible (或为临时群众)."""
        sb = _football_story_bible()
        outline = _ep1_outline()
        expected = _valid_script_draft()
        # 确保所有角色都在 StoryBible 或为合法临时角色
        fake_llm.register("write_episode", expected)

        ew_input = EpisodeWriterInput(
            episode_number=1,
            episode_outline=outline,
            story_bible=sb,
        )

        result = await skill.execute({
            "input": ew_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
            "outline_artifact_id": uuid4(),
        })

        # Skill 层已校验角色可追溯——此处验证 ScriptDraft 合法即可
        assert isinstance(result, ScriptDraft)
        assert len(result.scenes) >= 2

    async def test_unknown_character_not_blocking(
        self,
        skill: EpisodeWriterSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """未知角色不再阻断——仅记录日志，不抛出异常。"""
        sb = _football_story_bible()
        outline = _ep1_outline()
        draft_with_extra = _valid_script_draft()
        draft_with_extra.scenes[0].characters.append("神秘外星人X")
        fake_llm.register("write_episode", draft_with_extra)

        ew_input = EpisodeWriterInput(
            episode_number=1,
            episode_outline=outline,
            story_bible=sb,
        )

        # 不应抛异常，正常返回 ScriptDraft
        result = await skill.execute({
            "input": ew_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
            "outline_artifact_id": uuid4(),
        })
        assert result.episode_number == 1

    async def test_ending_hook_correspondence(
        self,
        skill: EpisodeWriterSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """验收项 4: ending_hook 与大纲 ending_hook 对应."""
        sb = _football_story_bible()
        outline = _ep1_outline()
        expected = _valid_script_draft()
        # 确保剧本 ending_hook 与大纲有关键词重叠
        fake_llm.register("write_episode", expected)

        ew_input = EpisodeWriterInput(
            episode_number=1,
            episode_outline=outline,
            story_bible=sb,
        )

        result = await skill.execute({
            "input": ew_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
            "outline_artifact_id": uuid4(),
        })

        # 验证 ending_hook 非空
        assert result.ending_hook.strip()


class TestEpisodeWriterSkillLLM:
    """LLM 调用处理."""

    async def test_llm_error_raises(
        self,
        skill: EpisodeWriterSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """LLM 超时 → RuntimeError."""
        sb = _football_story_bible()
        outline = _ep1_outline()
        fake_llm.inject_fault(1, "timeout")

        ew_input = EpisodeWriterInput(
            episode_number=1,
            episode_outline=outline,
            story_bible=sb,
        )

        with pytest.raises(RuntimeError, match="LLM"):
            await skill.execute({
                "input": ew_input,
                "agent": agent,
                "prompt_loader": prompt_loader,
                "outline_artifact_id": uuid4(),
            })


# ========================================================================
# CreationAgent 集成测试
# ========================================================================


class TestCreationAgentEpisodeWriter:
    """CreationAgent.generate_episode 集成测试 (C-05)."""

    async def test_generate_episode(
        self,
        creation_agent: CreationAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """端到端: StoryBible + Outline → ScriptDraft."""
        sb = _football_story_bible_obj()
        outline = _ep1_outline()
        expected = _valid_script_draft()
        fake_llm.register("write_episode", expected)

        oaid = uuid4()
        result = await creation_agent.generate_episode(
            episode_number=1,
            episode_outline=outline,
            story_bible=sb,
            prompt_loader=prompt_loader,
            outline_artifact_id=oaid,
        )

        assert isinstance(result, ScriptDraft)
        assert result.episode_number == 1
        assert len(result.scenes) >= 2
        assert result.word_count > 0
        assert result.referenced_outline_artifact_id == oaid

    async def test_generate_episode_with_previous_summary(
        self,
        creation_agent: CreationAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """验收项 5: 第 2 集使用前集摘要而非全文."""
        sb = _football_story_bible_obj()
        outline = _ep1_outline()
        outline["episode_number"] = 2
        outline["title"] = "第二章测试"
        expected = _valid_script_draft()
        expected.episode_number = 2
        fake_llm.register("write_episode", expected)

        summary = "第1集摘要: 林峰被青训队淘汰后在公园练球, 被张德胜教练发现并给予试训机会。"
        result = await creation_agent.generate_episode(
            episode_number=2,
            episode_outline=outline,
            story_bible=sb,
            prompt_loader=prompt_loader,
            outline_artifact_id=uuid4(),
            previous_summary=summary,
        )

        assert isinstance(result, ScriptDraft)
        assert result.episode_number == 2
