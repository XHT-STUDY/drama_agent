"""Conversational Revision Workflow 测试（J-06，action=revise_script）。

覆盖:
1. TDD anchor: 目标剧本没有绑定评估时，先仅评估目标集并持久化报告，
   再执行用户指定修订（prepare_target → ensure_evaluation → revise →
   continuity_check → re_evaluate）;
2. 已有绑定评估时不重复评估;
3. 幂等/重试: 以终态 completed_nodes 重放不产生新版本、不覆盖原稿;
4. 连续性失败 → needs_manual_review，候选稿保持 draft;
5. 目标不合法（非本项目的剧本 ID）→ 失败。

全部使用 FakeLLM（conftest fixtures）。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.runnables import RunnableConfig

from app.application.artifact_service import ArtifactService
from app.artifacts.store import ArtifactStore
from app.db.repositories.artifacts import ArtifactRepository
from app.domain.revision import ContinuitySemanticCheck, RevisionPlan
from app.llm.fake import FakeLLM
from app.workflows.conversational_revision import (
    build_conversational_revision_workflow,
)
from app.workflows.state import CreationState

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"


def _load_golden(name: str) -> dict[str, Any]:
    import json

    return cast(
        dict[str, Any], json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))
    )


async def _seed_scripts(
    db: Any,
    artifact_svc: ArtifactService,
    project_id: uuid.UUID,
) -> dict[str, str]:
    """播种 story_bible / outline / 3 集 valid 剧本（不含评估）。"""
    sb = await artifact_svc.create_validated_artifact(
        db, project_id=project_id, artifact_type="story_bible",
        content=_load_golden("story_bible_valid"),
    )
    await artifact_svc.create_validated_artifact(
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
                {"artifact_id": str(sb.id), "version": sb.version, "relation": "references"},
            ],
        )
        script_ids[str(ep)] = str(s.id)
    await db.flush()
    return script_ids


def _make_state(
    project_id: str,
    run_id: str,
    source_script_id: str,
    *,
    completed: list[str] | None = None,
) -> CreationState:
    return CreationState(
        run_id=run_id,
        project_id=project_id,
        action="revise_script",
        source_script_artifact_id=source_script_id,
        user_constraints=["增加主角与教练的正面冲突"],
        user_instruction="增加主角与教练的正面冲突",
        script_artifact_ids={},
        evaluation_artifact_ids={},
        needs_revision_decision=False,
        continuity_state_text="",
        revision_round=0,
        revision_candidate_episode=None,
        revision_plan_artifact_id=None,
        needs_manual_review=False,
        needs_manual_review_reason=None,
        current_episode=1,
        status="running",
        needs_user_input=False,
        error_node=None,
        error_detail=None,
        completed_nodes=list(completed or []),
        input_hashes={},
        prompt_versions={},
    )


async def _create_running_run(
    db: Any, run_svc: Any, project_id: uuid.UUID
) -> str:
    run = await run_svc.create_run(db, project_id=project_id, action="revise_script")
    await run_svc.transition_status(db, run.id, "running")
    return str(run.id)


@pytest.mark.workflow
@pytest.mark.asyncio
class TestConversationalRevisionWorkflow:
    async def test_missing_evaluation_is_created_before_user_directed_revision(
        self,
        test_project: uuid.UUID,
        workflow_config: RunnableConfig,
        artifact_service: ArtifactService,
    ) -> None:
        """TDD anchor: 目标剧本无绑定评估 → ensure_evaluation 先评估目标集。"""
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        run_id = await _create_running_run(db, run_svc, test_project)
        script_ids = await _seed_scripts(db, artifact_service, test_project)

        final_state = await build_conversational_revision_workflow().ainvoke(
            _make_state(str(test_project), run_id, script_ids["1"]),
            workflow_config,
        )

        # 五个节点全部完成
        for node in (
            "prepare_target", "ensure_evaluation", "revise",
            "continuity_check", "re_evaluate",
        ):
            assert node in final_state["completed_nodes"], final_state["completed_nodes"]
        assert final_state.get("needs_manual_review") is False
        assert final_state.get("status") != "failed"

        repo = ArtifactRepository(db)
        # 修订前目标剧本没有绑定评估 → 现在有了（先评估后修订）
        bound = await repo.find_evaluation_for_script(
            test_project, uuid.UUID(script_ids["1"])
        )
        assert bound is not None

        # 新稿成为第 1 集 latest valid，原稿未被覆盖
        store = ArtifactStore()
        new_id = final_state["script_artifact_ids"]["1"]
        assert new_id != script_ids["1"]
        latest = await store.get_latest(db, test_project, "script_draft", 1)
        assert latest is not None and str(latest.id) == new_id
        assert latest.status == "valid"
        original = await store.get_version(db, uuid.UUID(script_ids["1"]))
        assert original.status == "valid"

        # 修订计划与连续性结果均已落库
        plan = await artifact_service.get_version(
            db, uuid.UUID(final_state["revision_plan_artifact_id"])
        )
        RevisionPlan.model_validate(plan.content)
        continuity = await artifact_service.get_version(
            db, uuid.UUID(final_state["continuity_check_artifact_id"])
        )
        assert continuity.type == "continuity_check"

        # 重评报告绑定新稿
        new_eval = await artifact_service.get_version(
            db, uuid.UUID(final_state["evaluation_artifact_ids"]["1"])
        )
        assert new_eval.content["script_artifact_id"] == new_id
        # 单轮用户指定修订：不进入自动修订循环
        assert final_state.get("revision_round", 0) == 0

    async def test_existing_evaluation_is_reused(
        self,
        test_project: uuid.UUID,
        workflow_config: RunnableConfig,
        artifact_service: ArtifactService,
        fake_llm: FakeLLM,
    ) -> None:
        """目标剧本已有绑定评估 → ensure_evaluation 复用，不新建报告。"""
        from app.domain.evaluation import EvaluationReport

        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        run_id = await _create_running_run(db, run_svc, test_project)
        script_ids = await _seed_scripts(db, artifact_service, test_project)

        report = _load_golden("evaluation_report_valid")
        report["episode_number"] = 1
        report["script_artifact_id"] = script_ids["1"]
        existing = await artifact_service.create_validated_artifact(
            db, project_id=test_project, artifact_type="evaluation_report",
            episode_number=1, content=report,
            source_artifact_ids=[
                {"artifact_id": script_ids["1"], "version": 0, "relation": "derived_from"},
            ],
        )
        fake_llm.register(
            "evaluate_episode",
            EvaluationReport.model_validate(_load_golden("evaluation_report_valid")),
        )

        await build_conversational_revision_workflow().ainvoke(
            _make_state(str(test_project), run_id, script_ids["1"]),
            workflow_config,
        )

        # 只有一次评估调用（重评新稿），目标剧本原有评估被复用而非重建
        store = ArtifactStore()
        reports = await store.list_by_project(
            db, test_project, "evaluation_report", offset=0, limit=100
        )
        # 原评估 1 份 + 新稿重评 1 份 = 2（若 ensure_evaluation 重复评估会是 3）
        assert len(reports) == 2
        assert str(existing.id) in {str(r.id) for r in reports}

    async def test_replay_with_completed_nodes_creates_no_duplicates(
        self,
        test_project: uuid.UUID,
        workflow_config: RunnableConfig,
        artifact_service: ArtifactService,
    ) -> None:
        """以终态 completed_nodes 重放（模拟 retry 恢复）→ 不产生重复版本。"""
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        run_id = await _create_running_run(db, run_svc, test_project)
        script_ids = await _seed_scripts(db, artifact_service, test_project)

        workflow = build_conversational_revision_workflow()
        first = await workflow.ainvoke(
            _make_state(str(test_project), run_id, script_ids["1"]), workflow_config
        )

        store = ArtifactStore()
        before_scripts = len(await store.list_versions(db, test_project, "script_draft", 1))
        before_plans = len(
            await store.list_by_project(db, test_project, "revision_plan", offset=0, limit=100)
        )

        second = await workflow.ainvoke(
            cast(CreationState, first), workflow_config
        )

        assert len(await store.list_versions(db, test_project, "script_draft", 1)) == before_scripts
        assert len(
            await store.list_by_project(db, test_project, "revision_plan", offset=0, limit=100)
        ) == before_plans
        assert second["script_artifact_ids"] == first["script_artifact_ids"]

    async def test_continuity_failure_keeps_candidate_draft(
        self,
        test_project: uuid.UUID,
        workflow_config: RunnableConfig,
        artifact_service: ArtifactService,
        fake_llm: FakeLLM,
    ) -> None:
        """连续性失败 → needs_manual_review，候选稿保持 draft，不重评。"""
        fake_llm.register(
            "continuity_semantic_check",
            ContinuitySemanticCheck.model_validate(_load_golden("continuity_semantic_check_fail")),
        )
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        run_id = await _create_running_run(db, run_svc, test_project)
        script_ids = await _seed_scripts(db, artifact_service, test_project)

        final_state = await build_conversational_revision_workflow().ainvoke(
            _make_state(str(test_project), run_id, script_ids["1"]),
            workflow_config,
        )

        assert final_state.get("needs_manual_review") is True
        assert "re_evaluate" not in final_state["completed_nodes"]

        store = ArtifactStore()
        new_id = final_state["script_artifact_ids"]["1"]
        candidate = await store.get_version(db, uuid.UUID(new_id))
        assert candidate.status == "draft"
        # 原稿仍是 latest valid
        latest = await store.get_latest(db, test_project, "script_draft", 1)
        assert latest is not None and str(latest.id) == script_ids["1"]

    async def test_invalid_source_script_fails_prepare(
        self,
        test_project: uuid.UUID,
        workflow_config: RunnableConfig,
        artifact_service: ArtifactService,
    ) -> None:
        """目标不属于本项目 / 非 valid → prepare_target 失败，不进入修订。"""
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        run_id = await _create_running_run(db, run_svc, test_project)
        await _seed_scripts(db, artifact_service, test_project)

        # 另一个项目的剧本 ID（不存在于本项目）
        foreign = uuid.uuid4()
        final_state = await build_conversational_revision_workflow().ainvoke(
            _make_state(str(test_project), run_id, str(foreign)),
            workflow_config,
        )

        assert final_state.get("status") == "failed"
        assert final_state.get("error_node") == "prepare_target"
        assert "revise" not in final_state["completed_nodes"]
