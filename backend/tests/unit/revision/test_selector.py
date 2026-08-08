"""确定性选集单元测试 (F-01).

覆盖验收:
- 选择逻辑不调用 LLM（纯函数，仅依赖传入的评估报告）
- 只从 need_revision=true 中选 overall 最低者
- 同分按 episode_number 选最小集号
- 无 need_revision 时返回 None
"""

from __future__ import annotations

from uuid import UUID

from app.domain.enums import EvaluationDimension
from app.domain.evaluation import (
    EvaluationIssue,
    EvaluationReport,
    compute_overall_score,
)
from app.domain.revision import select_revision_candidate

_SID = UUID("00000000-0000-0000-0000-000000000010")


def _make_report(
    episode_number: int,
    overall: float,
    need_revision: bool,
    *,
    high_issue: bool = False,
) -> EvaluationReport:
    """构造指定集号/总分/修订标志的评估报告。

    dimension_scores 按 overall 反推平均分，保持字段合法。
    """
    base = round(overall, 1)
    dimension_scores = {
        dim: int(base) for dim in EvaluationDimension
    }
    issues: list[EvaluationIssue] = []
    if high_issue:
        issues.append(
            EvaluationIssue(
                issue_id=f"iss_{episode_number:03d}",
                dimension=EvaluationDimension.CONFLICT_INTENSITY,
                severity="high",
                scene_number=1,
                evidence="测试证据",
                diagnosis="测试诊断",
                suggestion="增加冲突",
            )
        )
    return EvaluationReport(
        episode_number=episode_number,
        script_artifact_id=_SID,
        rubric_version="1.0.0",
        dimension_scores=dimension_scores,
        overall_score=compute_overall_score(dimension_scores),
        issues=issues,
        need_revision=need_revision,
    )


class TestSelectRevisionCandidate:
    """select_revision_candidate 确定性选集测试。"""

    def test_empty_reports_returns_none(self) -> None:
        """空报告列表返回 None。"""
        assert select_revision_candidate([]) is None

    def test_all_pass_returns_none(self) -> None:
        """无 need_revision 时返回 None。"""
        reports = [
            _make_report(1, 82.0, False),
            _make_report(2, 78.0, False),
            _make_report(3, 88.0, False),
        ]
        assert select_revision_candidate(reports) is None

    def test_single_candidate_selected(self) -> None:
        """只有一个待修订集时选它。"""
        reports = [
            _make_report(1, 82.0, False),
            _make_report(2, 65.0, True),
            _make_report(3, 88.0, False),
        ]
        selected = select_revision_candidate(reports)
        assert selected is not None
        assert selected.episode_number == 2

    def test_lowest_overall_selected(self) -> None:
        """多个待修订集时选 overall 最低者。"""
        reports = [
            _make_report(1, 70.0, True),
            _make_report(2, 55.0, True),
            _make_report(3, 72.0, True),
        ]
        selected = select_revision_candidate(reports)
        assert selected is not None
        assert selected.episode_number == 2

    def test_tie_breaks_by_smallest_episode(self) -> None:
        """三集同分时选择 episode_number 最小者（TEST_PLAN 场景 3）。"""
        reports = [
            _make_report(1, 60.0, True),
            _make_report(2, 60.0, True),
            _make_report(3, 60.0, True),
        ]
        selected = select_revision_candidate(reports)
        assert selected is not None
        assert selected.episode_number == 1

    def test_high_issue_triggers_need_revision(self) -> None:
        """high 严重问题即使总分高也进入候选（need_revision 规则联动）。"""
        reports = [
            _make_report(1, 82.0, True, high_issue=True),
            _make_report(2, 78.0, False),
        ]
        selected = select_revision_candidate(reports)
        assert selected is not None
        assert selected.episode_number == 1

    def test_candidate_keeps_script_artifact_id(self) -> None:
        """选中的报告保留原稿 Artifact ID（供修订服务追溯原稿）。"""
        report = _make_report(2, 50.0, True)
        selected = select_revision_candidate([report])
        assert selected is not None
        assert selected.script_artifact_id == _SID

    def test_is_pure_function(self) -> None:
        """纯函数：不改变输入列表、不调用 LLM。"""
        reports = [
            _make_report(1, 60.0, True),
            _make_report(2, 80.0, False),
        ]
        snapshot = [r.model_dump() for r in reports]
        select_revision_candidate(reports)
        assert [r.model_dump() for r in reports] == snapshot
