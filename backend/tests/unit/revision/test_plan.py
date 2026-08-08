"""修订计划单元测试 (F-01).

覆盖验收:
- 从 issue 生成 RevisionOperation（绑定 issue_ids、目标场景与 preserve）
- plan 不允许无来源 issue 的空泛任务（filter_grounded_operations）
- locked_facts 写入计划（服务端权威覆盖）
- max_change_ratio 默认 0.35
- RevisionPlanSkill: 权威字段覆盖 / 场景号钳制 / LLM 失实兜底 / LLM 失败抛出
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from app.agents.base import BaseAgent
from app.domain.enums import EvaluationDimension
from app.domain.evaluation import EvaluationIssue, EvaluationReport
from app.domain.revision import (
    RevisionOperation,
    RevisionPlan,
    RevisionPlanInput,
    filter_grounded_operations,
    operations_from_issues,
)
from app.domain.script import ScriptDraft
from app.llm.fake import FakeLLM
from app.prompts.loader import PromptLoader
from app.skills.revision_plan import (
    RevisionPlanSkill,
    RevisionPlanValidationError,
)

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"
_SID = UUID("00000000-0000-0000-0000-000000000010")
_EID = UUID("00000000-0000-0000-0000-000000000020")


def _load_golden(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8")))


def _script_draft() -> ScriptDraft:
    return ScriptDraft.model_validate(_load_golden("script_draft_valid.json"))


def _make_issue(
    issue_id: str,
    dimension: EvaluationDimension = EvaluationDimension.CONFLICT_INTENSITY,
    scene_number: int | None = 1,
) -> EvaluationIssue:
    return EvaluationIssue(
        issue_id=issue_id,
        dimension=dimension,
        severity="medium",
        scene_number=scene_number,
        evidence="教练的语气里带着公式化的遗憾",
        diagnosis=f"{issue_id} 的测试诊断",
        suggestion=f"针对 {issue_id} 的具体改进建议",
    )


def _make_report(issues: list[EvaluationIssue] | None = None) -> EvaluationReport:
    issues = issues if issues is not None else [_make_issue("iss_001")]
    return EvaluationReport(
        episode_number=1,
        script_artifact_id=_SID,
        rubric_version="1.0.0",
        dimension_scores=dict.fromkeys(EvaluationDimension, 70),
        overall_score=55.0,
        issues=issues,
        need_revision=True,
    )


def _locked_facts() -> list[str]:
    return ["林峰的核心天赋是战术视野，不是超能力", "陈浩是林峰在青训时期的前队友"]


# ========================================================================
# 纯函数:operations_from_issues
# ========================================================================


class TestOperationsFromIssues:
    """issue → RevisionOperation 确定性生成。"""

    def test_each_issue_maps_to_operation(self) -> None:
        """每个 issue 映射为一个 operation，字段齐全。"""
        issues = [
            _make_issue("iss_001"),
            _make_issue("iss_002", EvaluationDimension.PACING, scene_number=2),
            _make_issue("iss_003", EvaluationDimension.PAYOFF_DENSITY, scene_number=None),
        ]
        ops = operations_from_issues(issues)
        assert len(ops) == 3
        assert [op.issue_ids for op in ops] == [["iss_001"], ["iss_002"], ["iss_003"]]
        assert [op.target_scene_number for op in ops] == [1, 2, None]
        assert all(op.instruction for op in ops)

    def test_operation_id_sequential(self) -> None:
        """operation_id 顺序编号 op_001 / op_002。"""
        ops = operations_from_issues([_make_issue("iss_001"), _make_issue("iss_002")])
        assert [op.operation_id for op in ops] == ["op_001", "op_002"]

    def test_preserve_contains_locked_facts(self) -> None:
        """preserve 写入锁定事实（不可修改内容）。"""
        ops = operations_from_issues([_make_issue("iss_001")], locked_facts=_locked_facts())
        assert ops[0].preserve == _locked_facts()

    def test_instruction_is_suggestion(self) -> None:
        """instruction 取 issue.suggestion（可执行建议）。"""
        ops = operations_from_issues([_make_issue("iss_001")])
        assert ops[0].instruction == "针对 iss_001 的具体改进建议"

    def test_empty_issues_returns_empty(self) -> None:
        """空 issue 列表返回空 operations。"""
        assert operations_from_issues([]) == []


# ========================================================================
# 纯函数:filter_grounded_operations
# ========================================================================


class TestFilterGroundedOperations:
    """剔除无来源 issue 的空泛任务（F-01 验收）。"""

    def _report_with_issues(self) -> EvaluationReport:
        return _make_report([_make_issue("iss_001"), _make_issue("iss_002")])

    def _op(self, operation_id: str, issue_ids: list[str]) -> RevisionOperation:
        return RevisionOperation(
            operation_id=operation_id,
            target_scene_number=1,
            issue_ids=issue_ids,
            instruction="修订指令",
        )

    def test_keeps_grounded_operations(self) -> None:
        """全部 issue_ids 来自报告的 operation 被保留。"""
        report = self._report_with_issues()
        ops = [
            self._op("op_a", ["iss_001"]),
            self._op("op_b", ["iss_002", "iss_001"]),
        ]
        assert filter_grounded_operations(ops, report) == ops

    def test_drops_unknown_issue_ids(self) -> None:
        """引用报告之外 issue_id 的 operation 被剔除。"""
        report = self._report_with_issues()
        ops = [
            self._op("op_a", ["iss_001"]),
            self._op("op_b", ["iss_zzz"]),
        ]
        grounded = filter_grounded_operations(ops, report)
        assert [op.operation_id for op in grounded] == ["op_a"]

    def test_drops_empty_issue_ids(self) -> None:
        """空 issue_ids 的 operation 视为无来源被剔除。"""
        report = self._report_with_issues()
        ops = [
            self._op("op_a", []),
            self._op("op_b", ["iss_001"]),
        ]
        grounded = filter_grounded_operations(ops, report)
        assert [op.operation_id for op in grounded] == ["op_b"]

    def test_drops_partially_ungrounded(self) -> None:
        """issue_ids 混有未知 ID 的 operation 整体剔除（须全部有据可依）。"""
        report = self._report_with_issues()
        ops = [self._op("op_a", ["iss_001", "iss_zzz"])]
        assert filter_grounded_operations(ops, report) == []

    def test_empty_operations_returns_empty(self) -> None:
        """空 operations 返回空。"""
        assert filter_grounded_operations([], self._report_with_issues()) == []


# ========================================================================
# RevisionPlan Schema 校验
# ========================================================================


class TestRevisionPlanSchema:
    """RevisionPlan 结构校验。"""

    def test_empty_operations_invalid(self) -> None:
        """空 operations 的 RevisionPlan 校验失败（至少一个 operation）。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RevisionPlan(
                episode_number=1,
                source_script_artifact_id=_SID,
                source_evaluation_artifact_id=_EID,
                operations=[],
            )

    def test_max_change_ratio_default(self) -> None:
        """max_change_ratio 默认 0.35。"""
        plan = RevisionPlan(
            episode_number=1,
            source_script_artifact_id=_SID,
            source_evaluation_artifact_id=_EID,
            operations=[self._op("op_001", ["iss_001"])],
        )
        assert plan.max_change_ratio == 0.35

    def test_extra_field_forbidden(self) -> None:
        """禁止额外字段（严格 Schema）。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RevisionPlan.model_validate(
                {
                    "episode_number": 1,
                    "source_script_artifact_id": str(_SID),
                    "source_evaluation_artifact_id": str(_EID),
                    "operations": [
                        {
                            "operation_id": "op_001",
                            "issue_ids": ["iss_001"],
                            "instruction": "修订指令",
                        }
                    ],
                    "extra_field": "x",
                }
            )

    @staticmethod
    def _op(operation_id: str, issue_ids: list[str]) -> RevisionOperation:
        return RevisionOperation(
            operation_id=operation_id,
            target_scene_number=1,
            issue_ids=issue_ids,
            instruction="修订指令",
        )


# ========================================================================
# RevisionPlanSkill（FakeLLM）
# ========================================================================


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM(seed=42)


@pytest.fixture
def agent(fake_llm: FakeLLM) -> BaseAgent:
    return BaseAgent(name="reviser", llm=fake_llm)


@pytest.fixture
def prompt_loader() -> PromptLoader:
    return PromptLoader()


@pytest.fixture
def skill() -> RevisionPlanSkill:
    return RevisionPlanSkill()


def _plan_input() -> RevisionPlanInput:
    return RevisionPlanInput(
        episode_number=1,
        source_script_artifact_id=_SID,
        source_evaluation_artifact_id=_EID,
        script_draft=_script_draft(),
        evaluation_report=_make_report(),
        locked_facts=_locked_facts(),
        max_change_ratio=0.35,
    )


def _register_plan(agent: BaseAgent, plan: RevisionPlan) -> None:
    cast(FakeLLM, agent.llm).register("revision_plan", plan)


async def _execute(
    skill: RevisionPlanSkill,
    agent: BaseAgent,
    prompt_loader: PromptLoader,
) -> RevisionPlan:
    return await skill.execute(
        {
            "input": _plan_input(),
            "agent": agent,
            "prompt_loader": prompt_loader,
        }
    )


class TestRevisionPlanSkill:
    """RevisionPlanSkill 行为测试（FakeLLM，确定性）。"""

    async def test_authoritative_fields_override(
        self,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        skill: RevisionPlanSkill,
    ) -> None:
        """episode_number / source ids / locked_facts / max_change_ratio 由服务端权威覆盖。"""
        llm_plan = RevisionPlan(
            episode_number=9,  # LLM 自报错误集号
            source_script_artifact_id=uuid4(),
            source_evaluation_artifact_id=uuid4(),
            operations=[
                RevisionOperation(
                    operation_id="op_001",
                    target_scene_number=1,
                    issue_ids=["iss_001"],
                    instruction="加强冲突",
                    expected_effect="冲突强度提升",
                )
            ],
            locked_facts=["LLM 编造的事实"],
            max_change_ratio=0.9,
        )
        _register_plan(agent, llm_plan)
        plan = await _execute(skill, agent, prompt_loader)

        assert plan.episode_number == 1
        assert plan.source_script_artifact_id == _SID
        assert plan.source_evaluation_artifact_id == _EID
        assert plan.locked_facts == _locked_facts()
        assert plan.max_change_ratio == 0.35

    async def test_grounded_operations_kept(
        self,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        skill: RevisionPlanSkill,
    ) -> None:
        """LLM 计划中有据可依的 operation 被保留。"""
        llm_plan = RevisionPlan(
            episode_number=1,
            source_script_artifact_id=_SID,
            source_evaluation_artifact_id=_EID,
            operations=[
                RevisionOperation(
                    operation_id="op_good", target_scene_number=1,
                    issue_ids=["iss_001"], instruction="增强冲突",
                ),
                RevisionOperation(
                    operation_id="op_ghost", target_scene_number=2,
                    issue_ids=["iss_ghost"], instruction="凭空任务",
                ),
            ],
        )
        _register_plan(agent, llm_plan)
        plan = await _execute(skill, agent, prompt_loader)

        assert [op.operation_id for op in plan.operations] == ["op_good"]

    async def test_fallback_when_llm_ungrounded(
        self,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        skill: RevisionPlanSkill,
    ) -> None:
        """LLM 计划全部失实时回退为确定性生成（仍保证有据可依）。"""
        llm_plan = RevisionPlan(
            episode_number=1,
            source_script_artifact_id=_SID,
            source_evaluation_artifact_id=_EID,
            operations=[
                RevisionOperation(
                    operation_id="op_ghost", target_scene_number=2,
                    issue_ids=["iss_ghost"], instruction="凭空任务",
                )
            ],
        )
        _register_plan(agent, llm_plan)
        plan = await _execute(skill, agent, prompt_loader)

        # 兜底:从报告 issue 生成
        assert len(plan.operations) == 1
        assert plan.operations[0].issue_ids == ["iss_001"]

    async def test_scene_number_clamped(
        self,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        skill: RevisionPlanSkill,
    ) -> None:
        """scene_number 超出现有场次范围时降级为 null。"""
        llm_plan = RevisionPlan(
            episode_number=1,
            source_script_artifact_id=_SID,
            source_evaluation_artifact_id=_EID,
            operations=[
                RevisionOperation(
                    operation_id="op_001", target_scene_number=99,
                    issue_ids=["iss_001"], instruction="加强冲突",
                )
            ],
        )
        _register_plan(agent, llm_plan)
        plan = await _execute(skill, agent, prompt_loader)

        assert plan.operations[0].target_scene_number is None

    async def test_locked_facts_written_into_preserve(
        self,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        skill: RevisionPlanSkill,
    ) -> None:
        """locked_facts 写入计划（验收项）。"""
        llm_plan = RevisionPlan(
            episode_number=1,
            source_script_artifact_id=_SID,
            source_evaluation_artifact_id=_EID,
            operations=[
                RevisionOperation(
                    operation_id="op_001", target_scene_number=1,
                    issue_ids=["iss_001"], instruction="加强冲突",
                )
            ],
        )
        _register_plan(agent, llm_plan)
        plan = await _execute(skill, agent, prompt_loader)

        assert plan.locked_facts == _locked_facts()
        assert plan.operations[0].preserve == _locked_facts()

    async def test_llm_failure_raises(
        self,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        skill: RevisionPlanSkill,
    ) -> None:
        """LLM 调用失败时抛出 RuntimeError。"""
        cast(FakeLLM, agent.llm).inject_fault(1, "timeout")
        with pytest.raises(RuntimeError):
            await _execute(skill, agent, prompt_loader)

    async def test_report_without_issues_raises(
        self,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        skill: RevisionPlanSkill,
    ) -> None:
        """报告无任何 issue 且 LLM 计划失实时无法兜底，抛出校验错误。"""
        # 覆盖输入:报告无 issue + LLM 计划也失实
        llm_plan = RevisionPlan(
            episode_number=1,
            source_script_artifact_id=_SID,
            source_evaluation_artifact_id=_EID,
            operations=[
                RevisionOperation(
                    operation_id="op_ghost", target_scene_number=2,
                    issue_ids=["iss_ghost"], instruction="凭空任务",
                )
            ],
        )
        _register_plan(agent, llm_plan)

        report = _make_report(issues=[])
        plan_input = _plan_input().model_copy(update={"evaluation_report": report})
        with pytest.raises(RevisionPlanValidationError):
            await skill.execute(
                {
                    "input": plan_input,
                    "agent": agent,
                    "prompt_loader": prompt_loader,
                }
            )
