"""W1-03 ArtifactExplainerSkill 单元测试。

验证契约：引文必须在给定原文中可溯源（归一化匹配，与评估共用算法），
伪造/跨场引文的处理，以及无有效引文时的降级信号。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.base import BaseAgent
from app.domain.agent_planner import (
    ArtifactExplanationInput,
    ArtifactExplanationOutput,
    ExplanationCitationOutput,
    ExplanationSourceText,
)
from app.llm.fake import FakeLLM
from app.prompts.loader import PromptLoader
from app.skills.artifact_explainer import ArtifactExplainerSkill

_SCENE_2_TEXT = "夜已深，公园空无一人。林峰独自对着墙壁反复传球。你在青训营待了几年？……五年。从十四岁到现在。"
_SCENE_1_TEXT = "教练宣布淘汰名单。林峰，你知道的，职业足球很残酷。"


def _input(question: str = "这场为什么突然翻脸") -> ArtifactExplanationInput:
    return ArtifactExplanationInput(
        question=question,
        target_label="第 3 集 v2",
        sources=[
            ExplanationSourceText(
                source_index=1, kind="script_scene", label="第 3 集 · 第 1 场",
                scene_number=1, text=_SCENE_1_TEXT,
            ),
            ExplanationSourceText(
                source_index=2, kind="script_scene", label="第 3 集 · 第 2 场",
                scene_number=2, text=_SCENE_2_TEXT,
            ),
            ExplanationSourceText(
                source_index=3, kind="story_bible", label="故事设定（辅助）",
                text="林峰是一名被青训队抛弃的足球少年，拥有战术视野天赋。",
            ),
        ],
    )


def _skill_with(output: ArtifactExplanationOutput) -> tuple[ArtifactExplainerSkill, BaseAgent]:
    llm = FakeLLM(seed=42)
    llm.register("artifact_explainer", output)
    return ArtifactExplainerSkill(), BaseAgent(name="planner", llm=llm)


@pytest.mark.unit
class TestArtifactExplainer:
    async def test_valid_scene_citation_verified(self) -> None:
        """指定场次的逐字引文（标点差异可容忍）→ 验证通过。"""
        skill, agent = _skill_with(
            ArtifactExplanationOutput(
                answer="这一场是林峰被淘汰后的不甘爆发。",
                citations=[
                    ExplanationCitationOutput(
                        source_index=2, scene_number=2,
                        quote="……五年。从十四岁到现在。",
                    ),
                ],
            )
        )
        result = await skill.execute(
            {"input": _input(), "agent": agent, "prompt_loader": PromptLoader()}
        )
        assert result.citations == [
            {"source_index": 2, "scene_number": 2, "quote": "……五年。从十四岁到现在。"}
        ]
        assert result.dropped_citations == 0

    async def test_forged_citation_dropped(self) -> None:
        """伪造引文（原文不存在）→ 剔除并计数；不进入验证结果。"""
        skill, agent = _skill_with(
            ArtifactExplanationOutput(
                answer="编造的解释。",
                citations=[
                    ExplanationCitationOutput(
                        source_index=2, scene_number=2,
                        quote="这句话根本不在剧本里，是模型编的",
                    ),
                ],
            )
        )
        result = await skill.execute(
            {"input": _input(), "agent": agent, "prompt_loader": PromptLoader()}
        )
        assert result.citations == []
        assert result.dropped_citations == 1

    async def test_cross_scene_citation_corrected(self) -> None:
        """引文标错场次但原文在其他场存在 → 纠正场次号（与评估溯源一致）。"""
        skill, agent = _skill_with(
            ArtifactExplanationOutput(
                answer="冲突从这里开始。",
                citations=[
                    ExplanationCitationOutput(
                        source_index=1, scene_number=1,
                        quote="职业足球很残酷",
                    ),
                ],
            )
        )
        result = await skill.execute(
            {"input": _input(), "agent": agent, "prompt_loader": PromptLoader()}
        )
        # 引文在第 1 场（source_index=1 的文本）——标注正确，无需纠正
        assert result.citations[0]["scene_number"] == 1

    async def test_story_bible_citation_no_scene(self) -> None:
        """非剧本来源的引文：整段归一化匹配，无场次。"""
        skill, agent = _skill_with(
            ArtifactExplanationOutput(
                answer="人物设定支撑这一动机。",
                citations=[
                    ExplanationCitationOutput(
                        source_index=3, quote="战术视野天赋",
                    ),
                ],
            )
        )
        result = await skill.execute(
            {"input": _input(), "agent": agent, "prompt_loader": PromptLoader()}
        )
        assert result.citations == [
            {"source_index": 3, "scene_number": None, "quote": "战术视野天赋"}
        ]

    async def test_unknown_source_index_dropped(self) -> None:
        """模型引用不存在的 source_index → 剔除。"""
        skill, agent = _skill_with(
            ArtifactExplanationOutput(
                answer="……",
                citations=[
                    ExplanationCitationOutput(
                        source_index=99, scene_number=1, quote="职业足球很残酷",
                    ),
                ],
            )
        )
        result = await skill.execute(
            {"input": _input(), "agent": agent, "prompt_loader": PromptLoader()}
        )
        assert result.citations == []
        assert result.dropped_citations == 1

    async def test_duplicate_quotes_counted_once(self) -> None:
        """同一条引文重复出现只保留一条。"""
        skill, agent = _skill_with(
            ArtifactExplanationOutput(
                answer="……",
                citations=[
                    ExplanationCitationOutput(
                        source_index=1, scene_number=1, quote="职业足球很残酷"
                    ),
                    ExplanationCitationOutput(
                        source_index=1, scene_number=1, quote="职业足球很残酷。"
                    ),
                ],
            )
        )
        result = await skill.execute(
            {"input": _input(), "agent": agent, "prompt_loader": PromptLoader()}
        )
        assert len(result.citations) == 1
        assert result.dropped_citations == 1

    def test_output_schema_rejects_artifact_ids(self) -> None:
        """模型输出契约不含 Artifact ID 字段（extra=forbid 拒绝越权）。"""
        with pytest.raises(ValidationError):
            ArtifactExplanationOutput.model_validate(
                {
                    "answer": "x",
                    "citations": [
                        {
                            "source_index": 1,
                            "quote": "x",
                            "artifact_id": "00000000-0000-0000-0000-000000000001",
                        }
                    ],
                }
            )
