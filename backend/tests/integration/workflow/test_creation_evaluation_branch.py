"""Creation Workflow 自动评估分支测试 (E-04, F-05).

测试范围:
- creation_workflow 写完 3 集后自动进入评估
- 高分 → 评估后走 finalize（status=completed）
- 低分 → 评估后进入自动修订分支（select_revision → revise → continuity_check
  → re_evaluate），修订 1 轮后仍有低分集则停在 needs_review（不 finalize）

全部使用 FakeLLM。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.application.artifact_service import ArtifactService
from app.domain.evaluation import EvaluationReport
from app.llm.fake import FakeLLM
from app.workflows.creation import build_creation_workflow
from app.workflows.state import CreationState

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"


def _load_golden(name: str) -> dict[str, Any]:
    return json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _low_score_report() -> EvaluationReport:
    """构造低分评估报告（触发 needs_revision_decision）。"""
    data = _load_golden("evaluation_report_valid")
    data["dimension_scores"]["conflict_intensity"] = 40
    return EvaluationReport.model_validate(data)


def _make_initial_state(project_id: str, run_id: str) -> CreationState:
    """创建完整 creation workflow 初始状态。"""
    return CreationState(
        run_id=run_id,
        project_id=project_id,
        action="create_script",
        requirement_artifact_id=None,
        story_bible_artifact_id=None,
        outline_set_artifact_id=None,
        script_artifact_ids={},
        evaluation_artifact_ids={},
        needs_revision_decision=False,
        continuity_state_text="",
        current_episode=1,
        status="running",
        needs_user_input=False,
        error_node=None,
        error_detail=None,
        completed_nodes=[],
        input_hashes={},
        prompt_versions={},
    )


@pytest.mark.workflow
@pytest.mark.asyncio
class TestCreationEvaluationBranch:
    """creation → 自动评估分支。"""

    async def test_high_score_finalizes(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        artifact_service: ArtifactService,
    ) -> None:
        """评估高分 → 自动走 finalize，工作流 completed。"""
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        run = await run_svc.create_run(db, project_id=test_project, action="create_script")
        await run_svc.transition_status(db, run.id, "running")
        project_id = str(test_project)
        run_id = str(run.id)

        initial_state = _make_initial_state(project_id, run_id)
        workflow = build_creation_workflow()
        final_state = await workflow.ainvoke(initial_state, workflow_config)

        # 3 集评估报告生成
        assert len(final_state["evaluation_artifact_ids"]) == 3
        assert final_state["needs_revision_decision"] is False
        # 评估后走 finalize
        assert final_state["status"] == "completed"
        assert "evaluate_episodes" in final_state["completed_nodes"]
        assert "finalize" in final_state["completed_nodes"]

    async def test_low_score_enters_auto_revision(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        artifact_service: ArtifactService,
        fake_llm: FakeLLM,
    ) -> None:
        """评估低分 → 进入自动修订分支，修订 1 轮后仍低分则停在 needs_review。"""
        fake_llm.register("evaluate_episode", _low_score_report())
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        run = await run_svc.create_run(db, project_id=test_project, action="create_script")
        await run_svc.transition_status(db, run.id, "running")
        project_id = str(test_project)
        run_id = str(run.id)

        initial_state = _make_initial_state(project_id, run_id)
        workflow = build_creation_workflow()
        final_state = await workflow.ainvoke(initial_state, workflow_config)

        # 评估完成但标记需修订
        assert len(final_state["evaluation_artifact_ids"]) == 3
        assert final_state["needs_revision_decision"] is True
        # 进入自动修订分支：选定最低分集并完成修订 / 连续性检查 / 重评
        assert "select_revision" in final_state["completed_nodes"]
        assert "revise" in final_state["completed_nodes"]
        assert "continuity_check" in final_state["completed_nodes"]
        assert "re_evaluate" in final_state["completed_nodes"]
        assert final_state["revision_round"] == 1
        assert final_state["revision_candidate_episode"] == 1
        # 修订 1 轮后重评仍低分 → 不 finalize，停在人工复核点
        assert "finalize" not in final_state["completed_nodes"]
        assert final_state.get("status") not in ("completed", "failed")
