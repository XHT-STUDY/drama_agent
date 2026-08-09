"""Revision Workflow 测试 (F-05).

测试范围（中途播种：completed_nodes 预置 6 个前序节点 + 真实 Artifact，
直接跑修订分支 select_revision → revise → continuity_check → re_evaluate）:
1. happy path: 低分集被修订，新稿通过连续性并提升 valid，重评高分 → completed
2. 修订轮次上限 MAX=1 生效: 重评仍低分 → 停在 needs_review（不 finalize）
3. 重试不会重复增加 revision_round / 不会生成额外版本
4. 连续性检查失败 → 转人工复核，候选稿保持 draft（不重评、不完成）
5. 重评显著下降（>5 分）→ 转人工复核
6. 全部通过 → 不进入修订分支，直接 finalize
7. 独立 build_revision_workflow 可单独跑通修订分支

全部使用 FakeLLM。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.application.artifact_service import ArtifactService
from app.artifacts.store import ArtifactStore
from app.domain.evaluation import (
    DEFAULT_EVALUATION_WEIGHTS,
    EvaluationReport,
    compute_overall_score,
)
from app.llm.fake import FakeLLM
from app.workflows.creation import build_creation_workflow
from app.workflows.revision import build_revision_workflow
from app.workflows.state import CreationState

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"

# 修订分支前 6 个已完成节点（中途播种：让 creation 图跳过它们直达修订分支）
_PRE_NODES = [
    "normalize", "retrieve", "story_bible", "outline",
    "write_episodes", "evaluate_episodes",
]

# fake_llm 评估 fixture 的占位 script_artifact_id（合法 UUID 即可，
# EvaluationSkill 会权威覆盖为真实新稿 ID）
_PLACEHOLDER_SCRIPT_ID = "00000000-0000-0000-0000-000000000099"


def _load_golden(name: str) -> dict[str, Any]:
    return json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _report_content(
    *,
    episode: int,
    script_id: str,
    dim_overrides: dict[str, int] | None = None,
    need_revision: bool | None = None,
) -> dict[str, Any]:
    """构造评估报告内容：维度可选覆盖，overall 按权重计算，need_revision 可显式指定。"""
    data = _load_golden("evaluation_report_valid")
    dims = dict(data["dimension_scores"])
    if dim_overrides:
        dims.update(dim_overrides)
    content = dict(data)
    content["episode_number"] = episode
    content["script_artifact_id"] = script_id
    content["dimension_scores"] = dims
    content["overall_score"] = compute_overall_score(dims, DEFAULT_EVALUATION_WEIGHTS)
    if need_revision is not None:
        content["need_revision"] = need_revision
    return content


def _high_content(episode: int, script_id: str) -> dict[str, Any]:
    """高分报告（overall=77.3，无需修订）。"""
    return _report_content(episode=episode, script_id=script_id, need_revision=False)


def _low_content(episode: int, script_id: str) -> dict[str, Any]:
    """低分报告（conflict_intensity=40 → overall=73.2，需修订）。"""
    return _report_content(
        episode=episode, script_id=script_id,
        dim_overrides={"conflict_intensity": 40}, need_revision=True,
    )


def _very_low_content(episode: int, script_id: str) -> dict[str, Any]:
    """极低分报告（overall=52.9，用于触发"修订后显著下降"）。"""
    return _report_content(
        episode=episode, script_id=script_id,
        dim_overrides={
            "conflict_intensity": 20,
            "ending_hook": 20,
            "compliance_safety": 40,
            "character_appeal": 30,
        },
        need_revision=True,
    )


async def _seed_full_project(
    db: Any,
    artifact_svc: ArtifactService,
    project_id: uuid.UUID,
    *,
    ep1_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """播种 story_bible / outline / 3 集剧本 / 3 份评估报告。

    默认 ep1 低分、ep2/3 高分；ep1_report 可覆盖（用于全高分场景）。
    Returns:
        {"story_bible": str, "outline": str, "script_artifact_ids": dict, "evaluation_artifact_ids": dict}
    """
    sb = await artifact_svc.create_validated_artifact(
        db, project_id=project_id, artifact_type="story_bible",
        content=_load_golden("story_bible_valid"),
    )
    outline = await artifact_svc.create_validated_artifact(
        db, project_id=project_id, artifact_type="episode_outline_set",
        content=_load_golden("outline_set_valid"),
    )
    script_ids: dict[str, str] = {}
    for ep in (1, 2, 3):
        content = _load_golden("script_draft_valid")
        content["episode_number"] = ep
        s = await artifact_svc.create_validated_artifact(
            db, project_id=project_id, artifact_type="script_draft",
            episode_number=ep, content=content,
            source_artifact_ids=[
                {"artifact_id": str(outline.id), "version": outline.version, "relation": "derived_from"},
                {"artifact_id": str(sb.id), "version": sb.version, "relation": "references"},
            ],
        )
        script_ids[str(ep)] = str(s.id)

    eval_ids: dict[str, str] = {}
    for ep in (1, 2, 3):
        if ep == 1:
            # 默认 ep1 低分（触发修订），ep1_report 显式传入时覆盖
            content = ep1_report if ep1_report else _low_content(1, script_ids[str(ep)])
        else:
            content = _high_content(ep, script_ids[str(ep)])
        ev = await artifact_svc.create_validated_artifact(
            db, project_id=project_id, artifact_type="evaluation_report",
            episode_number=ep, content=content,
            source_artifact_ids=[
                {"artifact_id": script_ids[str(ep)], "version": 0, "relation": "derived_from"},
            ],
        )
        eval_ids[str(ep)] = str(ev.id)

    await db.flush()
    return {
        "story_bible": str(sb.id),
        "outline": str(outline.id),
        "script_artifact_ids": script_ids,
        "evaluation_artifact_ids": eval_ids,
    }


async def _create_running_run(
    db: Any, run_svc: Any, project_id: uuid.UUID,
) -> tuple[str, str]:
    run = await run_svc.create_run(db, project_id=project_id, action="create_script")
    await run_svc.transition_status(db, run.id, "running")
    return str(run.project_id), str(run.id)


def _make_mid_seed_state(
    project_id: str,
    run_id: str,
    seed: dict[str, Any],
) -> CreationState:
    """构造中途播种状态：预置 6 个前序节点 + 评估决策，直接进入修订分支。"""
    return CreationState(
        run_id=run_id,
        project_id=project_id,
        action="create_script",
        story_bible_artifact_id=seed["story_bible"],
        outline_set_artifact_id=seed["outline"],
        script_artifact_ids=seed["script_artifact_ids"],
        evaluation_artifact_ids=seed["evaluation_artifact_ids"],
        needs_revision_decision=True,
        continuity_state_text="",
        revision_round=0,
        revision_candidate_episode=None,
        revision_plan_artifact_id=None,
        needs_manual_review=False,
        needs_manual_review_reason=None,
        current_episode=3,
        status="running",
        needs_user_input=False,
        error_node=None,
        error_detail=None,
        completed_nodes=list(_PRE_NODES),
        input_hashes={},
        prompt_versions={},
    )


@pytest.mark.workflow
@pytest.mark.asyncio
class TestRevisionWorkflow:
    """修订分支（内联于 creation 图）。"""

    async def test_happy_path_revises_selected_episode_only(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        artifact_service: ArtifactService,
        fake_llm: FakeLLM,
    ) -> None:
        """低分集被修订，新稿通过连续性并提升 valid，重评高分 → completed。"""
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        project_id, run_id = await _create_running_run(db, run_svc, test_project)
        seed = await _seed_full_project(db, artifact_service, test_project)

        # 重评走高分 → needs_revision_decision=False → finalize
        fake_llm.register(
            "evaluate_episode",
            EvaluationReport.model_validate(_high_content(1, _PLACEHOLDER_SCRIPT_ID)),
        )

        final_state = await build_creation_workflow().ainvoke(
            _make_mid_seed_state(project_id, run_id, seed), workflow_config,
        )

        assert final_state["revision_round"] == 1
        assert final_state["revision_candidate_episode"] == 1
        assert "select_revision" in final_state["completed_nodes"]
        assert "revise" in final_state["completed_nodes"]
        assert "continuity_check" in final_state["completed_nodes"]
        assert "re_evaluate" in final_state["completed_nodes"]
        assert "finalize" in final_state["completed_nodes"]
        assert final_state["needs_manual_review"] is False
        assert final_state["status"] == "completed"

        store = ArtifactStore()
        # 仅 ep1 有 2 个版本（原稿 + 修订稿），ep2/3 各 1 个
        assert len(await store.list_versions(db, test_project, "script_draft", 1)) == 2
        assert len(await store.list_versions(db, test_project, "script_draft", 2)) == 1
        assert len(await store.list_versions(db, test_project, "script_draft", 3)) == 1

        # 修订稿已提升为 valid（成为 ep1 最新 valid）
        new_draft_id = final_state["script_artifact_ids"]["1"]
        latest = await store.get_latest(db, test_project, "script_draft", 1)
        assert str(latest.id) == new_draft_id
        assert latest.status == "valid"

        # 新评估绑定新稿
        new_eval_id = final_state["evaluation_artifact_ids"]["1"]
        new_eval = await artifact_service.get_version(db, uuid.UUID(new_eval_id))
        assert new_eval.content["script_artifact_id"] == new_draft_id

        # 原稿与原评估仍可查（不可变，未被覆盖）
        orig_script_id = seed["script_artifact_ids"]["1"]
        orig = await artifact_service.get_version(db, uuid.UUID(orig_script_id))
        assert orig.status == "valid"
        orig_eval = await artifact_service.get_version(db, uuid.UUID(seed["evaluation_artifact_ids"]["1"]))
        assert orig_eval.content["overall_score"] == 73.2

    async def test_round_budget_max_1_effective(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        artifact_service: ArtifactService,
        fake_llm: FakeLLM,
    ) -> None:
        """重评仍低分 → 修订轮次用满（round=1 非 2），停在 needs_review。"""
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        project_id, run_id = await _create_running_run(db, run_svc, test_project)
        seed = await _seed_full_project(db, artifact_service, test_project)

        # 重评仍低分（FakeLLM 无逐调用序列 → 全流程同 fixture）
        fake_llm.register(
            "evaluate_episode",
            EvaluationReport.model_validate(_low_content(1, _PLACEHOLDER_SCRIPT_ID)),
        )

        final_state = await build_creation_workflow().ainvoke(
            _make_mid_seed_state(project_id, run_id, seed), workflow_config,
        )

        assert final_state["revision_round"] == 1  # 不进入第二轮
        assert final_state["needs_revision_decision"] is True
        assert "re_evaluate" in final_state["completed_nodes"]
        assert "finalize" not in final_state["completed_nodes"]
        assert final_state.get("status") not in ("completed", "failed")

    async def test_retry_does_not_double_increment(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        artifact_service: ArtifactService,
        fake_llm: FakeLLM,
    ) -> None:
        """happy path 后用 final_state 重新 ainvoke → 不重复自增、不新增版本。"""
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        project_id, run_id = await _create_running_run(db, run_svc, test_project)
        seed = await _seed_full_project(db, artifact_service, test_project)
        fake_llm.register(
            "evaluate_episode",
            EvaluationReport.model_validate(_high_content(1, _PLACEHOLDER_SCRIPT_ID)),
        )

        workflow = build_creation_workflow()
        final_state = await workflow.ainvoke(
            _make_mid_seed_state(project_id, run_id, seed), workflow_config,
        )
        assert final_state["revision_round"] == 1

        # 重试：所有节点因 completed_nodes 幂等跳过
        retried = await workflow.ainvoke(final_state, workflow_config)

        assert retried["revision_round"] == 1
        assert retried["evaluation_artifact_ids"] == final_state["evaluation_artifact_ids"]
        store = ArtifactStore()
        assert len(await store.list_versions(db, test_project, "script_draft", 1)) == 2

    async def test_continuity_failure_never_completes(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        artifact_service: ArtifactService,
        fake_llm: FakeLLM,
    ) -> None:
        """连续性检查失败 → 转人工复核，候选稿保持 draft，不重评不完成。"""
        from app.domain.revision import ContinuitySemanticCheck

        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        project_id, run_id = await _create_running_run(db, run_svc, test_project)
        seed = await _seed_full_project(db, artifact_service, test_project)

        fake_llm.register(
            "continuity_semantic_check",
            ContinuitySemanticCheck.model_validate(_load_golden("continuity_semantic_check_fail")),
        )

        final_state = await build_creation_workflow().ainvoke(
            _make_mid_seed_state(project_id, run_id, seed), workflow_config,
        )

        assert final_state["needs_manual_review"] is True
        assert final_state["needs_manual_review_reason"]
        assert "re_evaluate" not in final_state["completed_nodes"]
        assert "finalize" not in final_state["completed_nodes"]
        assert final_state.get("status") not in ("completed", "failed")

        # 候选稿保持 draft（诊断版本，未提升）；原稿仍是 ep1 最新 valid
        store = ArtifactStore()
        candidate = await artifact_service.get_version(
            db, uuid.UUID(final_state["script_artifact_ids"]["1"])
        )
        assert candidate.status == "draft"
        latest = await store.get_latest(db, test_project, "script_draft", 1)
        assert str(latest.id) == seed["script_artifact_ids"]["1"]
        assert latest.status == "valid"

    async def test_score_drop_over_5_marks_manual_review(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        artifact_service: ArtifactService,
        fake_llm: FakeLLM,
    ) -> None:
        """重评较原稿下降超过 5 分 → 转人工复核。"""
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        project_id, run_id = await _create_running_run(db, run_svc, test_project)
        seed = await _seed_full_project(db, artifact_service, test_project)

        # 重评极低分（73.2 → 52.9，下降 20.3 > 5）
        fake_llm.register(
            "evaluate_episode",
            EvaluationReport.model_validate(_very_low_content(1, _PLACEHOLDER_SCRIPT_ID)),
        )

        final_state = await build_creation_workflow().ainvoke(
            _make_mid_seed_state(project_id, run_id, seed), workflow_config,
        )

        assert final_state["needs_manual_review"] is True
        assert "下降" in final_state["needs_manual_review_reason"]
        assert "finalize" not in final_state["completed_nodes"]
        assert final_state.get("status") not in ("completed", "failed")

    async def test_no_revision_when_all_pass(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        artifact_service: ArtifactService,
        fake_llm: FakeLLM,
    ) -> None:
        """全部评估通过 → 直接 finalize，修订分支不执行。"""
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        project_id, run_id = await _create_running_run(db, run_svc, test_project)
        seed = await _seed_full_project(
            db, artifact_service, test_project,
            ep1_report=_high_content(1, _PLACEHOLDER_SCRIPT_ID),
        )
        state = _make_mid_seed_state(project_id, run_id, seed)
        state["needs_revision_decision"] = False  # 全部通过

        final_state = await build_creation_workflow().ainvoke(state, workflow_config)

        assert final_state["status"] == "completed"
        assert "finalize" in final_state["completed_nodes"]
        for node in ("select_revision", "revise", "continuity_check", "re_evaluate"):
            assert node not in final_state["completed_nodes"]

    async def test_standalone_revision_workflow(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        artifact_service: ArtifactService,
        fake_llm: FakeLLM,
    ) -> None:
        """独立 build_revision_workflow 可单独跑通修订分支。"""
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        project_id, run_id = await _create_running_run(db, run_svc, test_project)
        seed = await _seed_full_project(db, artifact_service, test_project)
        fake_llm.register(
            "evaluate_episode",
            EvaluationReport.model_validate(_high_content(1, _PLACEHOLDER_SCRIPT_ID)),
        )

        state = _make_mid_seed_state(project_id, run_id, seed)
        final_state = await build_revision_workflow().ainvoke(state, workflow_config)

        assert final_state["revision_round"] == 1
        assert final_state["revision_candidate_episode"] == 1
        assert "select_revision" in final_state["completed_nodes"]
        assert "revise" in final_state["completed_nodes"]
        assert "continuity_check" in final_state["completed_nodes"]
        assert "re_evaluate" in final_state["completed_nodes"]
        # 独立图将 "finalize" 映射到 END（无 finalize 节点）
        assert final_state["needs_revision_decision"] is False
        assert final_state["needs_manual_review"] is False
