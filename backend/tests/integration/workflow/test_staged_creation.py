"""L-2 分段创作测试：stop_after=outline → SB+大纲就绪后停在确认门。

- SB 与大纲 Artifact 已产出；
- 不写任何剧本（write_episodes 未执行）；
- state.stage_gate=outline、Run 由 Dispatcher 转 needs_review（此处直接跑图验证 state）。
"""

from __future__ import annotations

import uuid
from typing import Any

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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_continue_after_gate_writes_scripts_without_recompute(
    test_project: uuid.UUID,
    workflow_config: RunnableConfig,
    artifact_service: Any,
    test_engine: Any,
) -> None:
    """L-3 全链路：分段停门 → continue（剥离门字段）→ 恢复执行写剧本。

    completed_nodes 保留 → SB/大纲节点早退不重算；剧本与评估照常产出。
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from app.application.workflow_dispatcher import _execute_workflow
    from app.db.models.workflow_run import WorkflowRun

    db = workflow_config["configurable"]["db"]
    run_svc = workflow_config["configurable"]["run_service"]
    # _execute_workflow 使用全局 session factory → 指向测试引擎
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    import app.db.session as db_session

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    db_session._async_session_factory = factory

    # 第一段：staged → 停门
    run = await run_svc.create_run(
        db=db, project_id=test_project, action="create_script",
        config={"options": {
            "user_input": (
                "被青训队抛弃的足球少年林峰凭借战术视野天赋，"
                "从底层联赛逆袭至职业巅峰，要求强爽点与每集结尾钩子。"
            ),
            "outline_count": 10,
            "script_count": 3, "stop_after": "outline",
        }},
    )
    await db.execute(update(WorkflowRun).where(WorkflowRun.id == run.id).values(
        status="running", lease_owner="t1",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5), attempt_count=1))
    await db.commit()
    await _execute_workflow(run.id, "create_script", {}, "t1")
    await db.commit()
    await db.rollback()  # 放弃本会话快照，重新读取 dispatcher 写入的终态
    from sqlalchemy import select as _sel
    fresh = await async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)().__aenter__()
    gated = (await fresh.execute(
        _sel(WorkflowRun).where(WorkflowRun.id == run.id)
    )).scalar_one()
    assert gated.status == "needs_review", gated.status
    assert (gated.state_summary or {}).get("stage_gate") == "outline"
    await fresh.__aexit__(None, None, None)

    sb_before = (gated.state_summary or {})["story_bible_artifact_id"]
    outline_before = (gated.state_summary or {})["outline_set_artifact_id"]

    # continue：剥离门字段（端点逻辑的等价操作，直接在测试内执行）
    resumed = {
        k: v for k, v in (gated.state_summary or {}).items()
        if k not in ("stage_gate", "stop_after")
    }
    config = dict(gated.config_snapshot or {})
    config["options"] = {k: v for k, v in (config.get("options") or {}).items() if k != "stop_after"}
    await db.execute(update(WorkflowRun).where(WorkflowRun.id == run.id).values(
        state_summary=resumed, config_snapshot=config,
        status="running", lease_owner="t2",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5), attempt_count=2))
    await db.commit()

    # 第二段：恢复执行 → 剧本与评估产出
    await _execute_workflow(run.id, "create_script", {}, "t2")
    await db.rollback()
    fresh2 = await async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)().__aenter__()
    final = (await fresh2.execute(
        _sel(WorkflowRun).where(WorkflowRun.id == run.id)
    )).scalar_one()
    await fresh2.__aexit__(None, None, None)
    assert final.status in ("completed", "needs_review")
    assert (final.state_summary or {}).get("stage_gate", "") == ""

    summary = final.state_summary or {}
    # SB/大纲未被重算（同一 Artifact ID）
    assert summary["story_bible_artifact_id"] == sb_before
    assert summary["outline_set_artifact_id"] == outline_before
    # 剧本已写出
    assert summary.get("script_artifact_ids")
