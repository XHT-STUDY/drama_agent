"""评估明细（DimensionAssessment）与溯源校验单元测试 — 可解释性 v2.

覆盖验收项：
- Rubric v2 加载：5 档锚点、分带无缝覆盖 0-100、可观察信号；
- 分带 clamp：维度分越界时按所定位档位拉回分带内（以档位为准）；
- matched_anchor 权威回填：以 Rubric 原文为准，不采信模型转述；
- evidence 溯源校验三分支：命中 / 跨场自动纠正 / 无法溯源标记未验证；
- 全集性证据（scene_number=null）与全文匹配；
- overall_score 使用 clamp 后的维度分计算；
- 旧版报告（无 dimension_assessments）向后兼容，不触发任何矩阵校验。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from app.agents.base import BaseAgent
from app.domain.enums import (
    DEFAULT_EVALUATION_WEIGHTS as _DEFAULT_WEIGHTS,
)
from app.domain.enums import (
    EvaluationDimension,
)
from app.domain.evaluation import (
    EvaluationInput,
    EvaluationReport,
    clamp_score_to_band,
    compute_overall_score,
)
from app.domain.rubric import Rubric, load_rubric
from app.domain.script import ScriptDraft
from app.llm.fake import FakeLLM
from app.prompts.loader import PromptLoader
from app.skills.evaluator import EvaluationSkill

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"


# ========================================================================
# Fixtures
# ========================================================================


def _load_golden(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8")),
    )


def _evaluation_input() -> EvaluationInput:
    """golden 剧本 + 大纲 + 设定（剧本含 2 场，供溯源校验使用）。"""
    script = ScriptDraft.model_validate(_load_golden("script_draft_valid.json"))
    story_bible = _load_golden("story_bible_valid.json")
    outline_set = _load_golden("outline_set_valid.json")
    ep1 = next(
        e for e in outline_set["episodes"] if e["episode_number"] == 1
    )
    return EvaluationInput(
        episode_number=1,
        script_draft=script,
        episode_outline=ep1,
        story_bible=story_bible,
    )


def _v2_report() -> EvaluationReport:
    """从 v2 golden 构造 LLM 返回的报告（含评估明细）。"""
    data = _load_golden("evaluation_report_v2_valid.json")
    data["script_artifact_id"] = "00000000-0000-0000-0000-000000000099"
    return EvaluationReport.model_validate(data)


@pytest.fixture
def agent() -> BaseAgent:
    return BaseAgent(name="evaluator", llm=FakeLLM(seed=42))


@pytest.fixture
def skill() -> EvaluationSkill:
    return EvaluationSkill()


async def _run(agent: BaseAgent, skill: EvaluationSkill) -> EvaluationReport:
    """注册 v2 fixture 并执行一次完整评估。"""
    cast(FakeLLM, agent.llm).register("evaluate_episode", _v2_report())
    return await skill.execute(
        {
            "input": _evaluation_input(),
            "agent": agent,
            "prompt_loader": PromptLoader(),
            "script_artifact_id": uuid4(),
        }
    )


# ========================================================================
# Rubric v2：分带与信号
# ========================================================================


def _sample_dimensions() -> list[dict[str, Any]]:
    """构造 9 维合法定义（用于分带校验用例，隔离于分带问题本身）。"""
    return [
        {
            "dimension": dim.value,
            "label": dim.value,
            "weight": weight,
            "description": f"{dim.value} 说明",
            "anchors": {i: f"{i}档" for i in range(1, 6)},
        }
        for dim, weight in _DEFAULT_WEIGHTS.items()
    ]


def _raw_rubric(bands: dict[int, list[int]]) -> dict[str, Any]:
    """构造指定分带的 Rubric dict（维度部分合法）。"""
    return {
        "version": "t",
        "score_bands": bands,
        "dimensions": _sample_dimensions(),
    }


class TestRubricV2:
    """Rubric v2 数据结构。"""

    def test_load_v2_rubric(self) -> None:
        """默认加载 mvp_v2.yaml，版本 2.0.0，含信号定义。"""
        rubric = load_rubric()
        assert rubric.version == "2.0.0"
        assert len(rubric.dimensions) == 9
        assert all(spec.signals for spec in rubric.dimensions)

    def test_score_bands_cover_0_100(self) -> None:
        """分带无缝覆盖 0-100。"""
        rubric = load_rubric()
        assert rubric.score_band(1) == (0, 44)
        assert rubric.score_band(2) == (45, 59)
        assert rubric.score_band(3) == (60, 74)
        assert rubric.score_band(4) == (75, 89)
        assert rubric.score_band(5) == (90, 100)

    def test_score_band_invalid_level(self) -> None:
        """查询不存在的档位抛 KeyError。"""
        rubric = load_rubric()
        with pytest.raises(KeyError):
            rubric.score_band(6)

    def test_anchors_text_contains_bands_and_signals(self) -> None:
        """Prompt 注入文本包含分带映射与可观察信号。"""
        text = load_rubric().anchors_text()
        assert "档位与分数区间映射" in text
        assert "1档=0-44分" in text
        assert "可观察信号" in text

    def test_band_gap_rejected(self) -> None:
        """分带存在间隙时校验失败。"""
        raw = _raw_rubric(
            {1: [0, 44], 2: [45, 59], 3: [60, 74], 4: [75, 89], 5: [91, 100]}
        )
        with pytest.raises(Exception) as exc:
            Rubric.model_validate(raw)
        assert "间隙或重叠" in str(exc.value)

    def test_band_missing_level_rejected(self) -> None:
        """分带缺少档位时校验失败。"""
        raw = _raw_rubric({1: [0, 44], 2: [45, 59], 3: [60, 74], 4: [75, 89]})
        with pytest.raises(Exception) as exc:
            Rubric.model_validate(raw)
        assert "缺少档位" in str(exc.value)

    def test_band_overlap_rejected(self) -> None:
        """分带重叠时校验失败。"""
        raw = _raw_rubric(
            {1: [0, 50], 2: [45, 59], 3: [60, 74], 4: [75, 89], 5: [90, 100]}
        )
        with pytest.raises(Exception) as exc:
            Rubric.model_validate(raw)
        assert "间隙或重叠" in str(exc.value)


class TestClampScoreToBand:
    """分带 clamp 确定性函数。"""

    def test_in_band_unchanged(self) -> None:
        """分带内分数不变。"""
        assert clamp_score_to_band(66, 3, (60, 74)) == 66

    def test_above_band_clamped_down(self) -> None:
        """高于分带上界拉回上界。"""
        assert clamp_score_to_band(95, 3, (60, 74)) == 74

    def test_below_band_clamped_up(self) -> None:
        """低于分带下界拉回下界。"""
        assert clamp_score_to_band(10, 4, (75, 89)) == 75

    def test_boundary_exact(self) -> None:
        """边界值保持不变。"""
        assert clamp_score_to_band(60, 3, (60, 74)) == 60
        assert clamp_score_to_band(74, 3, (60, 74)) == 74


# ========================================================================
# 评估明细归一化（skill 级）
# ========================================================================


class TestAssessmentNormalization:
    """v2 报告走完整评估流程后的矩阵门禁。"""

    async def test_rubric_version_bound(
        self, agent: BaseAgent, skill: EvaluationSkill
    ) -> None:
        """报告绑定 v2 Rubric 版本（不采信 LLM 自报的 1.0.0）。"""
        report = await _run(agent, skill)
        assert report.rubric_version == "2.0.0"

    async def test_band_clamp_applied(
        self, agent: BaseAgent, skill: EvaluationSkill
    ) -> None:
        """visualizability 95 分定位 3 档（60-74）→ clamp 至 74。"""
        report = await _run(agent, skill)
        assert (
            report.dimension_scores[EvaluationDimension.VISUALIZABILITY] == 74
        )

    async def test_in_band_scores_unchanged(
        self, agent: BaseAgent, skill: EvaluationSkill
    ) -> None:
        """分带内的分数不被改写。"""
        report = await _run(agent, skill)
        assert report.dimension_scores[EvaluationDimension.OPENING_HOOK] == 82
        assert report.dimension_scores[EvaluationDimension.CONFLICT_INTENSITY] == 52

    async def test_matched_anchor_backfilled_from_rubric(
        self, agent: BaseAgent, skill: EvaluationSkill
    ) -> None:
        """matched_anchor 以 Rubric 原文回填。"""
        report = await _run(agent, skill)
        rubric = load_rubric()
        for dim, assessment in report.dimension_assessments.items():
            spec = rubric.dimension_spec(dim)
            assert assessment.matched_anchor == spec.anchors[assessment.level]

    async def test_all_nine_dimensions_have_assessments(
        self, agent: BaseAgent, skill: EvaluationSkill
    ) -> None:
        """评估明细覆盖全部 9 个维度。"""
        report = await _run(agent, skill)
        assert set(report.dimension_assessments) == set(EvaluationDimension)

    async def test_overall_uses_clamped_scores(
        self, agent: BaseAgent, skill: EvaluationSkill
    ) -> None:
        """总分用 clamp 后的维度分计算（服务端权威）。"""
        report = await _run(agent, skill)
        expected = compute_overall_score(
            report.dimension_scores, load_rubric().weights()
        )
        assert report.overall_score == expected

    async def test_rationale_present(
        self, agent: BaseAgent, skill: EvaluationSkill
    ) -> None:
        """每个维度都有档位定位理由。"""
        report = await _run(agent, skill)
        assert all(
            a.rationale.strip() for a in report.dimension_assessments.values()
        )


class TestEvidenceProvenance:
    """evidence 溯源校验三分支（引用均来自 v2 golden）。"""

    async def test_verified_quote(
        self, agent: BaseAgent, skill: EvaluationSkill
    ) -> None:
        """引用与所引场次原文匹配 → verified=True。"""
        report = await _run(agent, skill)
        cites = report.dimension_assessments[
            EvaluationDimension.OPENING_HOOK
        ].evidence
        assert cites[0].scene_number == 1
        assert cites[0].verified is True

    async def test_cross_scene_drift_corrected(
        self, agent: BaseAgent, skill: EvaluationSkill
    ) -> None:
        """引用实际在第 2 场却标注第 1 场 → 自动纠正场次号。"""
        report = await _run(agent, skill)
        cites = report.dimension_assessments[
            EvaluationDimension.PAYOFF_DENSITY
        ].evidence
        assert cites[0].scene_number == 2
        assert cites[0].verified is True

    async def test_unverifiable_quote_marked(
        self, agent: BaseAgent, skill: EvaluationSkill
    ) -> None:
        """全文都匹配不到的引用 → verified=False（软校验不阻断）。"""
        report = await _run(agent, skill)
        cites = report.dimension_assessments[
            EvaluationDimension.MAIN_CLARITY
        ].evidence
        assert cites[0].verified is False

    async def test_full_text_evidence_for_null_scene(
        self, agent: BaseAgent, skill: EvaluationSkill
    ) -> None:
        """scene_number=null 的全集性证据与全文匹配 → verified=True。"""
        report = await _run(agent, skill)
        cites = report.dimension_assessments[
            EvaluationDimension.COMPLIANCE_SAFETY
        ].evidence
        assert cites[0].scene_number is None
        assert cites[0].verified is True

    async def test_overlong_quote_clamped(
        self, agent: BaseAgent, skill: EvaluationSkill
    ) -> None:
        """证据引用超过 200 字截断。"""
        report = await _run(agent, skill)
        for assessment in report.dimension_assessments.values():
            for cite in assessment.evidence:
                assert len(cite.quote) <= 200


# ========================================================================
# 向后兼容
# ========================================================================


class TestBackwardCompatibility:
    """旧版报告（无 dimension_assessments）不受矩阵校验影响。"""

    async def test_v1_report_without_assessments(
        self, agent: BaseAgent, skill: EvaluationSkill
    ) -> None:
        """旧 fixture 走原流程，维度分不被 clamp。"""
        data = _load_golden("evaluation_report_valid.json")
        data["script_artifact_id"] = "00000000-0000-0000-0000-000000000099"
        cast(FakeLLM, agent.llm).register(
            "evaluate_episode", EvaluationReport.model_validate(data)
        )
        report = await skill.execute(
            {
                "input": _evaluation_input(),
                "agent": agent,
                "prompt_loader": PromptLoader(),
                "script_artifact_id": uuid4(),
            }
        )
        assert report.dimension_assessments == {}
        # 旧报告无矩阵数据，维度分保持模型原值
        assert report.dimension_scores[EvaluationDimension.OPENING_HOOK] == 82
        # 但仍绑定当前 Rubric 版本
        assert report.rubric_version == "2.0.0"

    def test_incomplete_assessments_rejected(self) -> None:
        """评估明细缺失维度时 Pydantic 校验失败。"""
        data = _load_golden("evaluation_report_v2_valid.json")
        del data["dimension_assessments"]["pacing"]
        with pytest.raises(Exception) as exc:
            EvaluationReport.model_validate(data)
        assert "全部 9 个维度" in str(exc.value)
