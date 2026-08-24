"""L-2 分段创作测试：stop_after=outline → SB+大纲就绪后停在确认门。

- SB 与大纲 Artifact 已产出；
- 不写任何剧本（write_episodes 未执行）；
- state.stage_gate=outline、Run 由 Dispatcher 转 needs_review（此处直接跑图验证 state）。
"""

from __future__ import annotations

import uuid

import pytest
from langchain_core.runnables import RunnableConfig

from app.artifacts.store import ArtifactStore
from app.workflows.creation import build_creation_workflow
from app.workflows.state import CreationState


def _staged_state(project_id: str, run_id: str) -> CreationState:
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
        revision_round=0,
        revision_candidate_episode=None,
        revision_plan_artifact_id=None,
        needs_manual_review=False,
        needs_manual_review_reason=None,
        stop_after="outline",
        stage_gate="",
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
async def test_staged_creation_stops_at_outline_gate(
    test_project: uuid.UUID,
    workflow_config: RunnableConfig,
) -> None:
    """stop_after=outline：SB+大纲产出、零剧本、stage_gate 置位。"""
    db = workflow_config["configurable"]["db"]
    run_svc = workflow_config["configurable"]["run_service"]
    run = await run_svc.create_run(
        db=db, project_id=test_project, action="create_script"
    )
    await run_svc.transition_status(db, run.id, "running")

    final_state = await build_creation_workflow().ainvoke(
        _staged_state(str(test_project), str(run.id)), workflow_config
    )

    assert final_state.get("stage_gate") == "outline"
    assert final_state.get("status") != "failed"
    # SB 与大纲已产出
    assert final_state.get("story_bible_artifact_id")
    assert final_state.get("outline_set_artifact_id")
    # 未进入剧本写作与评估
    assert "write_episodes" not in final_state.get("completed_nodes", [])
    assert "evaluate_episodes" not in final_state.get("completed_nodes", [])

    store = ArtifactStore()
    scripts = await store.list_by_project(
        db, test_project, "script_draft", offset=0, limit=100
    )
    assert scripts == []


@pytest.mark.workflow
@pytest.mark.asyncio
async def test_full_flow_unchanged_without_stop_after(
    test_project: uuid.UUID,
    workflow_config: RunnableConfig,
) -> None:
    """未设置 stop_after → 全流程照旧（回归保护）。"""
    db = workflow_config["configurable"]["db"]
    run_svc = workflow_config["configurable"]["run_service"]
    run = await run_svc.create_run(
        db=db, project_id=test_project, action="create_script"
    )
    await run_svc.transition_status(db, run.id, "running")

    state = _staged_state(str(test_project), str(run.id))
    state["stop_after"] = ""
    final_state = await build_creation_workflow().ainvoke(state, workflow_config)

    assert final_state.get("stage_gate", "") == ""
    assert "write_episodes" in final_state.get("completed_nodes", [])
    assert "evaluate_episodes" in final_state.get("completed_nodes", [])
