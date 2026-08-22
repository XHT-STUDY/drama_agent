"""Outline Revision Workflow 测试（J-08，action=revise_outline）。

覆盖:
1. TDD anchor: 修订产生新大纲版本（latest valid），不重写任何剧本；
   旧大纲内容/checksum 不变；影响分析进入 state（变更集 + 受影响剧本）;
2. 不变量失败：诊断版本落库为 invalid，Run failed，latest valid 不变;
3. 无 Story Bible / 非法目标 → 失败，不产生新版本。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.runnables import RunnableConfig

from app.application.artifact_service import ArtifactService
from app.artifacts.store import ArtifactStore
from app.domain.outline import EpisodeOutlineSet
from app.llm.fake import FakeLLM
from app.workflows.outline_revision import build_outline_revision_workflow
from app.workflows.state import CreationState

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"


def _load(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))
    )


async def _seed_project(
    db: Any,
    artifact_svc: ArtifactService,
    project_id: uuid.UUID,
) -> dict[str, Any]:
    """播种 SB + 大纲 + 3 集剧本（derived_from 大纲、references SB）。"""
    sb = await artifact_svc.create_validated_artifact(
        db, project_id=project_id, artifact_type="story_bible",
        content=_load("story_bible_valid"),
    )
    outline = await artifact_svc.create_validated_artifact(
        db, project_id=project_id, artifact_type="episode_outline_set",
        content=_load("outline_set_valid"),
    )
    script_ids: dict[str, str] = {}
    for ep in (1, 2, 3):
        content = _load("script_draft_valid")
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
    await db.flush()
    return {
        "story_bible": str(sb.id),
        "outline": str(outline.id),
        "scripts": script_ids,
    }


def _make_state(project_id: str, run_id: str, outline_id: str) -> CreationState:
    return CreationState(
        run_id=run_id,
        project_id=project_id,
        action="revise_outline",
        source_outline_artifact_id=outline_id,
        user_constraints=["第 3 集增加林峰与陈浩的正面冲突"],
        user_instruction="第 3 集增加林峰与陈浩的正面冲突",
        outline_impact={},
        script_artifact_ids={},
        evaluation_artifact_ids={},
        status="running",
        needs_user_input=False,
        error_node=None,
        error_detail=None,
        completed_nodes=[],
        input_hashes={},
        prompt_versions={},
    )


async def _create_running_run(db: Any, run_svc: Any, project_id: uuid.UUID) -> str:
    run = await run_svc.create_run(db, project_id=project_id, action="revise_outline")
    await run_svc.transition_status(db, run.id, "running")
    return str(run.id)


@pytest.mark.workflow
@pytest.mark.asyncio
class TestOutlineRevisionWorkflow:
    async def test_outline_revision_creates_new_version_without_rewriting_scripts(
        self,
        test_project: uuid.UUID,
        workflow_config: RunnableConfig,
        artifact_service: ArtifactService,
        fake_llm: FakeLLM,
    ) -> None:
        """TDD anchor: 新大纲版本落库为 latest valid，剧本完全不动。"""
        fake_llm.register(
            "outline_reviser",
            EpisodeOutlineSet.model_validate(_load("outline_revision_valid")),
        )
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        run_id = await _create_running_run(db, run_svc, test_project)
        seed = await _seed_project(db, artifact_service, test_project)
        old_outline_id = seed["outline"]

        old_artifact = await artifact_service.get_version(db, uuid.UUID(old_outline_id))
        old_checksum, old_content = old_artifact.checksum, old_artifact.content

        final_state = await build_outline_revision_workflow().ainvoke(
            _make_state(str(test_project), run_id, old_outline_id),
            workflow_config,
        )

        assert final_state.get("status") != "failed"
        # 只执行了 revise_outline 一个节点——不调用剧本生成/修订
        assert final_state["completed_nodes"] == ["revise_outline"]

        store = ArtifactStore()
        # 新版本成为 latest valid（version 2），旧版本内容/checksum 不变
        latest = await store.get_latest(db, test_project, "episode_outline_set", 1)
        assert latest is not None
        assert latest.version == 2 and latest.status == "valid"
        assert str(latest.id) != old_outline_id
        old_after = await store.get_version(db, uuid.UUID(old_outline_id))
        assert old_after.checksum == old_checksum
        assert old_after.content == old_content
        # 新版本 sources：旧大纲 revises + Story Bible references
        links = await artifact_service.get_source_links(db, latest.id)
        relations = {(link["relation"], link["target_id"]) for link in links}
        assert ("revises", old_outline_id) in relations
        assert ("references", seed["story_bible"]) in relations

        # 剧本不被重写：每集仍只有 1 个版本、状态 valid、ID 不变
        for ep in (1, 2, 3):
            versions = await store.list_versions(db, test_project, "script_draft", ep)
            assert len(versions) == 1
            assert versions[0].status == "valid"
            assert str(versions[0].id) == seed["scripts"][str(ep)]

        # 影响分析进入 state：第 3 集变化，第 3 集剧本受影响
        impact = final_state["outline_impact"]
        assert impact["changed_episodes"] == [3]
        assert seed["scripts"]["3"] in impact["dependent_script_ids"]
        assert seed["scripts"]["1"] not in impact["dependent_script_ids"]
        assert any("建议发起剧本修订" in f for f in impact["follow_ups"])

    async def test_invariant_failure_saves_invalid_diagnostic_and_keeps_latest_valid(
        self,
        test_project: uuid.UUID,
        workflow_config: RunnableConfig,
        artifact_service: ArtifactService,
        fake_llm: FakeLLM,
    ) -> None:
        """不变量失败：诊断版本 invalid 落库，Run failed，latest valid 不变。"""
        invalid_output = EpisodeOutlineSet.model_validate(
            {**_load("outline_revision_valid"), "episodes": _load("outline_revision_valid")["episodes"][:9]}
        )
        fake_llm.register("outline_reviser", invalid_output)
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        run_id = await _create_running_run(db, run_svc, test_project)
        seed = await _seed_project(db, artifact_service, test_project)
        old_outline_id = seed["outline"]

        final_state = await build_outline_revision_workflow().ainvoke(
            _make_state(str(test_project), run_id, old_outline_id),
            workflow_config,
        )

        assert final_state.get("status") == "failed"
        assert final_state.get("error_node") == "revise_outline"
        assert "集数必须保持" in (final_state.get("error_detail") or "")

        store = ArtifactStore()
        # latest valid 仍是旧大纲（version 1，内容/checksum 不变）
        latest = await store.get_latest(db, test_project, "episode_outline_set", 1)
        assert latest is not None
        assert str(latest.id) == old_outline_id and latest.version == 1
        # 诊断版本存在且为 invalid（sources 绑定旧大纲）
        versions = await store.list_versions(db, test_project, "episode_outline_set", 1)
        invalid_versions = [v for v in versions if v.status == "invalid"]
        assert len(invalid_versions) == 1

    async def test_missing_story_bible_fails_without_new_version(
        self,
        test_project: uuid.UUID,
        workflow_config: RunnableConfig,
        artifact_service: ArtifactService,
        fake_llm: FakeLLM,
    ) -> None:
        """无 Story Bible → 修订失败（不落任何新版本）。"""
        fake_llm.register(
            "outline_reviser",
            EpisodeOutlineSet.model_validate(_load("outline_revision_valid")),
        )
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        run_id = await _create_running_run(db, run_svc, test_project)
        outline = await artifact_service.create_validated_artifact(
            db, project_id=test_project, artifact_type="episode_outline_set",
            content=_load("outline_set_valid"),
        )

        final_state = await build_outline_revision_workflow().ainvoke(
            _make_state(str(test_project), run_id, str(outline.id)),
            workflow_config,
        )

        assert final_state.get("status") == "failed"
        store = ArtifactStore()
        versions = await store.list_versions(db, test_project, "episode_outline_set", 1)
        assert len(versions) == 1  # 只有原大纲，未产生新版本

    async def test_foreign_target_fails_prepare(
        self,
        test_project: uuid.UUID,
        workflow_config: RunnableConfig,
        artifact_service: ArtifactService,
    ) -> None:
        """目标不属于本项目 → prepare 校验失败，不进入修订。"""
        db = workflow_config["configurable"]["db"]
        run_svc = workflow_config["configurable"]["run_service"]
        run_id = await _create_running_run(db, run_svc, test_project)
        await _seed_project(db, artifact_service, test_project)

        final_state = await build_outline_revision_workflow().ainvoke(
            _make_state(str(test_project), run_id, str(uuid.uuid4())),
            workflow_config,
        )

        assert final_state.get("status") == "failed"
        assert final_state.get("error_node") == "revise_outline"
