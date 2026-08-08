"""Evaluation Workflow 测试 (E-04).

测试范围:
- 独立 evaluation workflow 对已写剧本逐集评估
- 评估报告绑定正确集与版本
- 低分集 → needs_revision_decision=True
- 高分集 → needs_revision_decision=False

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
from app.workflows.evaluation import build_evaluation_workflow
from app.workflows.state import CreationState

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"


def _load_golden(name: str) -> dict[str, Any]:
    return json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _low_score_report() -> EvaluationReport:
    """构造低分评估报告（触发 needs_revision_decision）。"""
    data = _load_golden("evaluation_report_valid")
    data["dimension_scores"]["conflict_intensity"] = 40
    return EvaluationReport.model_validate(data)


async def _seed_script(
    db: Any,
    artifact_svc: ArtifactService,
    project_id: uuid.UUID,
    episode: int,
) -> str:
    """创建 outline/story_bible/script 三个 Artifact，返回 script id。"""
    outline = await artifact_svc.create_validated_artifact(
        db, project_id=project_id,
        artifact_type="episode_outline_set",
        content=_load_golden("outline_set_valid"),
    )
    sb = await artifact_svc.create_validated_artifact(
        db, project_id=project_id,
        artifact_type="story_bible",
        content=_load_golden("story_bible_valid"),
    )
    script_content = _load_golden("script_draft_valid")
    script_content["episode_number"] = episode  # golden 固定为 1，seed 时校正
    script = await artifact_svc.create_validated_artifact(
        db, project_id=project_id,
        artifact_type="script_draft",
        episode_number=episode,
        content=script_content,
        source_artifact_ids=[
            {"artifact_id": str(outline.id), "version": outline.version, "relation": "derived_from"},
            {"artifact_id": str(sb.id), "version": sb.version, "relation": "references"},
        ],
    )
    # workflow conftest 的 db_session 用 session.begin() 管理事务，此处仅 flush 不 commit
    await db.flush()
    return str(script.id)


def _make_initial_state(
    project_id: uuid.UUID,
    run_id: str,
    script_artifact_ids: dict[str, str],
) -> CreationState:
    """构造 evaluation workflow 初始状态。"""
    return CreationState(
        run_id=run_id,
        project_id=str(project_id),
        action="evaluate",
        script_artifact_ids=script_artifact_ids,
        evaluation_artifact_ids={},
        needs_revision_decision=False,
        status="running",
        completed_nodes=[],
        input_hashes={},
        prompt_versions={},
    )


@pytest.mark.workflow
@pytest.mark.asyncio
class TestEvaluationWorkflow:
    """独立评估工作流。"""

    async def test_low_score_enters_revision_decision(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        artifact_service: ArtifactService,
        fake_llm: FakeLLM,
    ) -> None:
        """低分集 → 评估完成并标记 needs_revision_decision。"""
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        run = await run_svc.create_run(db, project_id=test_project, action="evaluate")
        await run_svc.transition_status(db, run.id, "running")

        # 覆盖为低分 fixture
        fake_llm.register("evaluate_episode", _low_score_report())

        script_id = await _seed_script(db, artifact_service, test_project, 1)
        initial_state = _make_initial_state(
            test_project, str(run.id), {"1": script_id}
        )

        workflow = build_evaluation_workflow()
        final_state = await workflow.ainvoke(initial_state, workflow_config)

        assert final_state["needs_revision_decision"] is True
        assert "1" in final_state["evaluation_artifact_ids"]
        eval_aid = uuid.UUID(final_state["evaluation_artifact_ids"]["1"])
        report = await artifact_service.get_version(db, eval_aid)
        assert report.type == "evaluation_report"
        assert report.episode_number == 1
        # 绑定被评估的剧本版本
        assert report.content["script_artifact_id"] == script_id
        assert "evaluate_episodes" in final_state["completed_nodes"]

    async def test_high_score_no_revision(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        artifact_service: ArtifactService,
    ) -> None:
        """高分集 → needs_revision_decision=False。"""
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        run = await run_svc.create_run(db, project_id=test_project, action="evaluate")
        await run_svc.transition_status(db, run.id, "running")

        script_id = await _seed_script(db, artifact_service, test_project, 1)
        initial_state = _make_initial_state(
            test_project, str(run.id), {"1": script_id}
        )

        workflow = build_evaluation_workflow()
        final_state = await workflow.ainvoke(initial_state, workflow_config)

        assert final_state["needs_revision_decision"] is False
        assert "1" in final_state["evaluation_artifact_ids"]

    async def test_evaluates_multiple_episodes_sorted(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        artifact_service: ArtifactService,
    ) -> None:
        """多集评估，报告绑定各自的集号与剧本版本。"""
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        run = await run_svc.create_run(db, project_id=test_project, action="evaluate")
        await run_svc.transition_status(db, run.id, "running")

        script_1 = await _seed_script(db, artifact_service, test_project, 1)
        script_2 = await _seed_script(db, artifact_service, test_project, 2)
        script_3 = await _seed_script(db, artifact_service, test_project, 3)
        initial_state = _make_initial_state(
            test_project,
            str(run.id),
            {"1": script_1, "2": script_2, "3": script_3},
        )

        workflow = build_evaluation_workflow()
        final_state = await workflow.ainvoke(initial_state, workflow_config)

        assert len(final_state["evaluation_artifact_ids"]) == 3
        # 每集评估绑定各自的剧本
        for ep, sid in (("1", script_1), ("2", script_2), ("3", script_3)):
            eval_aid = uuid.UUID(final_state["evaluation_artifact_ids"][ep])
            report = await artifact_service.get_version(db, eval_aid)
            assert report.episode_number == int(ep)
            assert report.content["script_artifact_id"] == sid
