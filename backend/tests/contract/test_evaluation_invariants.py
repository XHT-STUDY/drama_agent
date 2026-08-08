"""评估一致性与 Golden 回归测试 (E-05).

对高/中/低三个固定剧本 case 验证评估不变量：
- 报告结构完整，可被 EvaluationReport 解析
- overall_score / need_revision 由服务端确定性规则回填（非 LLM 自报）
- 低分维度（<70）必有对应 issue
- 报告区分"模型判断"（dimension_scores）与"确定性指标"（overall/need_revision）
- FakeLLM 回归完全确定（同一 case 两次评估结果一致）
- 每个 case 的预期分支与 case.expected 一致

全部使用 FakeLLM，不访问真实 LLM。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from app.agents.base import BaseAgent
from app.domain.enums import EvaluationDimension
from app.domain.evaluation import (
    EvaluationInput,
    EvaluationReport,
    compute_need_revision,
    compute_overall_score,
)
from app.domain.rubric import load_rubric
from app.domain.script import ScriptDraft
from app.llm.fake import FakeLLM
from app.prompts.loader import PromptLoader
from app.skills.evaluator import EvaluationSkill

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"
CASES_DIR = GOLDEN_DIR / "evaluation_cases"

# 各质量档次的维度分（模型判断）
_CASE_SCORES: dict[str, dict[str, int]] = {
    "high": {dim.value: 85 for dim in EvaluationDimension},
    "medium": {dim.value: 78 for dim in EvaluationDimension},
    "low": {dim.value: 45 for dim in EvaluationDimension},
}
_CASE_SCORES["high"]["compliance_safety"] = 95
_CASE_SCORES["low"]["compliance_safety"] = 55


def _load_case(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((CASES_DIR / f"{name}.json").read_text(encoding="utf-8")))


def _case_names() -> list[str]:
    return ["high", "medium", "low"]


def _make_report_fixture(case_name: str, script_artifact_id: str) -> EvaluationReport:
    """构造匹配 case 质量档次的 LLM 报告（维度分为模型判断）。"""
    scores = {
        EvaluationDimension(dim): int(score)
        for dim, score in _CASE_SCORES[case_name].items()
    }
    issues: list[dict[str, Any]] = []
    if case_name == "low":
        issues.append(
            {
                "issue_id": "iss_conflict",
                "dimension": "conflict_intensity",
                "severity": "high",
                "scene_number": 1,
                "evidence": "林峰安静地吃着早饭",
                "diagnosis": "冲突强度极低，剧情平铺直叙",
                "suggestion": "增加对抗性冲突",
            }
        )
    return EvaluationReport.model_validate(
        {
            "episode_number": 1,
            "script_artifact_id": script_artifact_id,
            "rubric_version": "1.0.0",
            "dimension_scores": scores,
            "strengths": ["测试亮点"],
            "issues": issues,
            "revision_suggestions": [],
            "need_revision": False,
            "risk_flags": [],
        }
    )


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM(seed=42)


@pytest.fixture
def agent(fake_llm: FakeLLM) -> BaseAgent:
    return BaseAgent(name="evaluator", llm=fake_llm)


@pytest.fixture
def prompt_loader() -> PromptLoader:
    return PromptLoader()


@pytest.fixture
def skill() -> EvaluationSkill:
    return EvaluationSkill()


@pytest.mark.contract
class TestEvaluationInvariants:
    """评估不变量回归（对固定 case）。"""

    @pytest.mark.parametrize("case_name", _case_names())
    async def test_structure_complete(
        self,
        case_name: str,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        skill: EvaluationSkill,
    ) -> None:
        """报告结构完整：9 维齐全、字段合法。"""
        report = await _run_evaluation_async(agent, prompt_loader, skill, case_name)
        assert len(report.dimension_scores) == 9
        assert set(report.dimension_scores) == set(EvaluationDimension)
        assert report.rubric_version
        assert 0 <= report.overall_score <= 100

    @pytest.mark.parametrize("case_name", _case_names())
    async def test_overall_is_server_computed(
        self,
        case_name: str,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        skill: EvaluationSkill,
    ) -> None:
        """overall_score 由服务端按 Rubric 权重计算，而非 LLM 自报。"""
        report = await _run_evaluation_async(agent, prompt_loader, skill, case_name)
        expected = compute_overall_score(report.dimension_scores, load_rubric().weights())
        assert report.overall_score == expected

    @pytest.mark.parametrize("case_name", _case_names())
    async def test_need_revision_matches_case_expectation(
        self,
        case_name: str,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        skill: EvaluationSkill,
    ) -> None:
        """need_revision 由服务端规则决定，且与 case 预期分支一致。"""
        report = await _run_evaluation_async(agent, prompt_loader, skill, case_name)
        case = _load_case(case_name)
        assert report.need_revision == case["expected"]["need_revision"]
        assert report.need_revision == compute_need_revision(
            report.overall_score, report.issues, report.dimension_scores
        )

    @pytest.mark.parametrize("case_name", _case_names())
    async def test_low_dimensions_have_issues(
        self,
        case_name: str,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        skill: EvaluationSkill,
    ) -> None:
        """低于 70 的维度必有对应 issue（含服务端自动补全）。"""
        report = await _run_evaluation_async(agent, prompt_loader, skill, case_name)
        covered = {i.dimension for i in report.issues}
        for dim, score in report.dimension_scores.items():
            if score < 70:
                assert dim in covered, f"维度 {dim.value} ({score} 分) 缺少 issue"

    async def test_high_case_no_auto_issue(
        self,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        skill: EvaluationSkill,
    ) -> None:
        """高分 case 不自动补 issue。"""
        report = await _run_evaluation_async(agent, prompt_loader, skill, "high")
        auto = [i for i in report.issues if i.issue_id.startswith("auto_low_")]
        assert auto == []

    async def test_fake_llm_deterministic(
        self,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        skill: EvaluationSkill,
    ) -> None:
        """FakeLLM 下同一 case 两次评估结果完全一致（确定性回归）。"""
        first = await _run_evaluation_async(agent, prompt_loader, skill, "low")
        cast(FakeLLM, agent.llm).reset()
        second = await _run_evaluation_async(agent, prompt_loader, skill, "low")
        assert first.overall_score == second.overall_score
        assert first.dimension_scores == second.dimension_scores

    async def test_low_case_triggers_revision_branch(
        self,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        skill: EvaluationSkill,
    ) -> None:
        """低分 case 触发 need_revision（用于工作流修订分支）。"""
        report = await _run_evaluation_async(agent, prompt_loader, skill, "low")
        assert report.need_revision is True
        assert report.overall_score < 75


async def _run_evaluation_async(
    agent: BaseAgent,
    prompt_loader: PromptLoader,
    skill: EvaluationSkill,
    case_name: str,
) -> EvaluationReport:
    """异步执行一次评估（供 async 测试使用）。"""
    from uuid import UUID, uuid4

    case = _load_case(case_name)
    script = ScriptDraft.model_validate(case["script_draft"])
    sid = str(uuid4())
    cast(FakeLLM, agent.llm).register(
        "evaluate_episode", _make_report_fixture(case_name, sid)
    )
    ev_input = EvaluationInput(
        episode_number=1,
        script_draft=script,
        episode_outline={},
        story_bible={},
    )
    return await skill.execute(
        {
            "input": ev_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
            "script_artifact_id": UUID(sid),
        }
    )
