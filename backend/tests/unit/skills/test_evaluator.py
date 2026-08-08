"""EvaluationSkill 单元测试 (E-02).

测试范围:
- 剧本/大纲/StoryBible → EvaluationReport 生成
- overall_score / need_revision 由服务端确定性回填（覆盖 LLM 自报）
- 低于 70 的维度自动补 issue
- evidence 超长截断
- scene_number 超范围降级为 null
- script_artifact_id / rubric_version 正确绑定
- EvaluationAgent 集成
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from app.agents.base import BaseAgent
from app.agents.evaluation import EvaluationAgent
from app.domain.enums import EvaluationDimension
from app.domain.evaluation import EvaluationInput, EvaluationReport
from app.domain.script import ScriptDraft
from app.llm.fake import FakeLLM
from app.prompts.loader import PromptLoader
from app.skills.evaluator import EvaluationSkill
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
    return BaseAgent(name="evaluator", llm=fake_llm)


@pytest.fixture
def prompt_loader() -> PromptLoader:
    return PromptLoader()


@pytest.fixture
def skill() -> EvaluationSkill:
    return EvaluationSkill()


def _load_golden(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8")))


def _fake_llm(agent: BaseAgent) -> FakeLLM:
    """将 BaseAgent 内的 LLM 视为 FakeLLM（测试专用）。"""
    return cast(FakeLLM, agent.llm)


def _register_report(agent: BaseAgent, report: EvaluationReport) -> None:
    """向 FakeLLM 注册 evaluate_episode fixture。"""
    _fake_llm(agent).register("evaluate_episode", report)


def _script_draft() -> ScriptDraft:
    return ScriptDraft.model_validate(_load_golden("script_draft_valid.json"))


def _evaluation_input() -> EvaluationInput:
    story_bible = _load_golden("story_bible_valid.json")
    outline_set = _load_golden("outline_set_valid.json")
    ep1 = next(e for e in outline_set["episodes"] if e["episode_number"] == 1)
    return EvaluationInput(
        episode_number=1,
        script_draft=_script_draft(),
        episode_outline=ep1,
        story_bible=story_bible,
    )


def _golden_report() -> EvaluationReport:
    """从 golden 构造 LLM 返回的报告。"""
    data = _load_golden("evaluation_report_valid.json")
    data["script_artifact_id"] = "00000000-0000-0000-0000-000000000099"
    return EvaluationReport.model_validate(data)


# ========================================================================
# 测试
# ========================================================================


class TestEvaluationSkill:
    """核心评估流程。"""

    async def test_full_evaluation(
        self, agent: BaseAgent, prompt_loader: PromptLoader, skill: EvaluationSkill
    ) -> None:
        """FakeLLM 返回报告后，服务端回填确定性指标。"""
        # FakeLLM 注册 evaluate_episode fixture
        _register_report(agent, _golden_report())

        script_aid = uuid4()
        report = await skill.execute({
            "input": _evaluation_input(),
            "agent": agent,
            "prompt_loader": prompt_loader,
            "script_artifact_id": script_aid,
        })

        assert isinstance(report, EvaluationReport)
        assert report.episode_number == 1
        # golden overall=77.6 → 服务端按默认权重重算 77.3（证明不被 LLM 自报带偏）
        assert report.overall_score == 77.3
        # golden need_revision=true → 服务端规则判定 False（无 high issue，总分达标）
        assert report.need_revision is False
        assert report.script_artifact_id == script_aid
        assert report.rubric_version  # 从 rubric 读取
        assert len(report.dimension_scores) == 9

    async def test_dimension_scores_preserved(
        self, agent: BaseAgent, prompt_loader: PromptLoader, skill: EvaluationSkill
    ) -> None:
        """维度分来自模型，不被服务端改写。"""
        _register_report(agent, _golden_report())
        report = await skill.execute({
            "input": _evaluation_input(),
            "agent": agent,
            "prompt_loader": prompt_loader,
            "script_artifact_id": uuid4(),
        })
        assert report.dimension_scores[EvaluationDimension.OPENING_HOOK] == 82


class TestLowDimensionAutoFill:
    """低于 70 的维度自动补 issue。"""

    async def test_low_dimension_gets_issue(
        self, agent: BaseAgent, prompt_loader: PromptLoader, skill: EvaluationSkill
    ) -> None:
        """opening_hook 50 分且无对应 issue → 自动补一条。"""
        golden = _golden_report()
        golden.dimension_scores[EvaluationDimension.OPENING_HOOK] = 50
        # 移除所有 opening_hook 相关的 issue（golden 原本没有）
        golden.issues = [
            i for i in golden.issues
            if i.dimension is not EvaluationDimension.OPENING_HOOK
        ]
        _register_report(agent, golden)

        report = await skill.execute({
            "input": _evaluation_input(),
            "agent": agent,
            "prompt_loader": prompt_loader,
            "script_artifact_id": uuid4(),
        })

        opening_issues = [
            i for i in report.issues
            if i.dimension is EvaluationDimension.OPENING_HOOK
        ]
        assert opening_issues, "低分维度必须有一条对应 issue"
        assert any(i.issue_id.startswith("auto_low_") for i in opening_issues)

    async def test_high_score_dimension_not_auto_filled(
        self, agent: BaseAgent, prompt_loader: PromptLoader, skill: EvaluationSkill
    ) -> None:
        """高分维度无 issue 时不自动补。"""
        golden = _golden_report()
        _register_report(agent, golden)
        report = await skill.execute({
            "input": _evaluation_input(),
            "agent": agent,
            "prompt_loader": prompt_loader,
            "script_artifact_id": uuid4(),
        })
        auto = [i for i in report.issues if i.issue_id.startswith("auto_low_")]
        assert auto == []


class TestEvidenceAndScene:
    """evidence 与 scene_number 规范化。"""

    async def test_evidence_clamped(
        self, agent: BaseAgent, prompt_loader: PromptLoader, skill: EvaluationSkill
    ) -> None:
        """evidence 超长截断至 200 字。"""
        golden = _golden_report()
        golden.issues[0].evidence = "长" * 300
        _register_report(agent, golden)

        report = await skill.execute({
            "input": _evaluation_input(),
            "agent": agent,
            "prompt_loader": prompt_loader,
            "script_artifact_id": uuid4(),
        })
        assert len(report.issues[0].evidence) <= 200

    async def test_scene_number_out_of_range_downgraded(
        self, agent: BaseAgent, prompt_loader: PromptLoader, skill: EvaluationSkill
    ) -> None:
        """scene_number 超范围降级为 null。"""
        golden = _golden_report()
        golden.issues[0].scene_number = 99  # 剧本只有 2 场
        _register_report(agent, golden)

        report = await skill.execute({
            "input": _evaluation_input(),
            "agent": agent,
            "prompt_loader": prompt_loader,
            "script_artifact_id": uuid4(),
        })
        assert report.issues[0].scene_number is None


class TestLLMFailure:
    """LLM 故障路径。"""

    async def test_llm_failure_raises(
        self, agent: BaseAgent, prompt_loader: PromptLoader, skill: EvaluationSkill
    ) -> None:
        """FakeLLM 注入超时 → RuntimeError。"""
        _fake_llm(agent).inject_fault(1, "timeout")
        with pytest.raises(RuntimeError):
            await skill.execute({
                "input": _evaluation_input(),
                "agent": agent,
                "prompt_loader": prompt_loader,
                "script_artifact_id": uuid4(),
            })


class TestEvaluationAgent:
    """EvaluationAgent 集成。"""

    @pytest.fixture
    def eval_agent(self, agent: BaseAgent, skill: EvaluationSkill) -> EvaluationAgent:
        registry = SkillRegistry()
        registry.register(skill)
        return EvaluationAgent(base_agent=agent, skill_registry=registry)

    async def test_agent_evaluate_episode(
        self, agent: BaseAgent, prompt_loader: PromptLoader, eval_agent: EvaluationAgent
    ) -> None:
        """Agent 封装调用返回合法报告。"""
        _register_report(agent, _golden_report())
        script = _script_draft()
        ep1 = next(
            e for e in _load_golden("outline_set_valid.json")["episodes"]
            if e["episode_number"] == 1
        )
        report = await eval_agent.evaluate_episode(
            episode_number=1,
            script_draft=script,
            episode_outline=ep1,
            story_bible=_load_golden("story_bible_valid.json"),
            prompt_loader=prompt_loader,
            script_artifact_id=uuid4(),
        )
        assert report.episode_number == 1
        assert report.overall_score > 0
