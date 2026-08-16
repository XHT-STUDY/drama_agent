"""I-01 恢复矩阵集成测试（FakeLLM 确定性）。

验证 Phase I 韧性验收的核心语义：
1. 429 限流后重试成功 → 工作流仍完成
2. timeout 全部尝试耗尽 → failed + error_code=LLM_TIMEOUT
3. invalid schema 由 Parser 带反馈重试 → 成功
4. 硬预算超限 → failed + error_code=RUN_BUDGET_EXCEEDED
5. 协作式取消 → RunCancelledError + 不创建新 Artifact
6. 从 checkpoint 恢复 → 已完成节点不重调 LLM、已写集不重写
7. 每失败均有 error_code（随 2/4 断言）

全部使用 FakeLLM（含故障注入），无真实 LLM 调用。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.application.run_service import RunService
from app.domain.evaluation import EvaluationReport
from app.domain.outline import EpisodeOutlineSet
from app.domain.requirement import NormalizedRequirement
from app.domain.script import ScriptDraft
from app.domain.story_bible import StoryBible
from app.events.publisher import EventPublisher
from app.llm.budget import enter_run, exit_run
from app.llm.fake import FakeLLM
from app.llm.models import LLMErrorCode
from app.llm.retry import RetryPolicy
from app.prompts.loader import PromptLoader
from app.workflows.checkpoint import (
    RunCancelledError,
    clear_cancel,
    request_cancel,
)
from app.workflows.creation import build_creation_workflow
from app.workflows.state import CreationState


def _load_golden(name: str) -> dict[str, Any]:
    import os

    path = os.path.join(os.path.dirname(__file__), "..", "..", "golden", f"{name}.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "expected_output" in data:
        return data["expected_output"]
    return data


def _register_golden(llm: FakeLLM, *, high_score: bool = True) -> None:
    """注册 Creation Workflow 全链路 golden fixtures。"""
    llm.register(
        "normalize_requirement",
        NormalizedRequirement.model_validate(_load_golden("requirement_football")),
    )
    llm.register("story_bible", StoryBible.model_validate(_load_golden("story_bible_football")))
    llm.register("outline", EpisodeOutlineSet.model_validate(_load_golden("outline_set_valid")))
    llm.register("write_episode", ScriptDraft.model_validate(_load_golden("script_draft_valid")))
    if high_score:
        llm.register(
            "evaluate_episode",
            EvaluationReport.model_validate(_load_golden("evaluation_report_valid")),
        )
    else:
        llm.register(
            "evaluate_episode",
            EvaluationReport.model_validate(_load_golden("evaluation_report_lowscore")),
        )


def _make_llm(*, retry_policy: RetryPolicy | None = None, high_score: bool = True) -> FakeLLM:
    llm = FakeLLM(seed=42, retry_policy=retry_policy)
    _register_golden(llm, high_score=high_score)
    return llm


def _make_config(
    *,
    db: Any,
    agent: BaseAgent,
    prompt_loader: PromptLoader,
    artifact_service: ArtifactService,
    run_service: RunService,
    publisher: EventPublisher,
    user_input: str = "一个被青训队抛弃的足球少年逆袭故事",
) -> dict[str, Any]:
    """构建与 conftest.workflow_config 等价的运行时上下文。"""
    progress_log: list[dict[str, Any]] = []

    def progress_callback(node: str, event: str, progress: float) -> None:
        progress_log.append({"node": node, "event": event, "progress": progress})

    return {
        "configurable": {
            "db": db,
            "agent": agent,
            "prompt_loader": prompt_loader,
            "artifact_service": artifact_service,
            "run_service": run_service,
            "event_publisher": publisher,
            "user_input": user_input,
            "source_type": "idea",
            "rag_context": "",
            "progress_callback": progress_callback,
            "progress_log": progress_log,
        },
    }


def _make_initial_state(
    project_id: str,
    run_id: str,
    *,
    completed_nodes: list[str] | None = None,
    **extra: Any,
) -> CreationState:
    state = CreationState(
        run_id=run_id,
        project_id=project_id,
        action="create_script",
        requirement_artifact_id=None,
        story_bible_artifact_id=None,
        outline_set_artifact_id=None,
        script_artifact_ids={},
        continuity_state_text="",
        current_episode=1,
        status="running",
        needs_user_input=False,
        error_node=None,
        error_detail=None,
        completed_nodes=completed_nodes or [],
        input_hashes={},
        prompt_versions={},
    )
    for k, v in extra.items():
        state[k] = v  # type: ignore[literal-required]
    return state


async def _make_project(db: Any) -> uuid.UUID:
    from app.db.models.project import Project

    pid = uuid.uuid4()
    db.add(Project(id=pid, title="I-01 恢复矩阵项目", status="draft"))
    await db.flush()
    return pid


async def _make_run(db: Any, project_id: uuid.UUID, *, action: str = "create_script") -> str:
    """创建 WorkflowRun 并转为 running（事件发布要求 run 行存在）。"""
    svc = RunService()
    run = await svc.create_run(db, project_id=project_id, action=action)
    await svc.transition_status(db, run.id, "running")
    return str(run.id)


async def _count_artifacts_by_type(
    artifact_svc: ArtifactService, db: Any, project_id: uuid.UUID, artifact_type: str
) -> int:
    from app.artifacts.store import ArtifactStore

    store = ArtifactStore()
    items = await store.list_by_project(db, project_id, artifact_type, offset=0, limit=1000)
    return len(items)


# ========================================================================
# 1. 429 后重试成功
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
class TestRateLimitedRecovers:
    """429 限流 → 重试后成功，工作流仍完成。"""

    async def test_rate_limited_retry_recovers(
        self,
        db_session: Any,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
        run_service: RunService,
        event_publisher: EventPublisher,
    ) -> None:
        llm = _make_llm(retry_policy=RetryPolicy(base_delay=0.01))
        llm.inject_fault(1, "rate_limited")  # 第 1 次尝试 429 → 重试成功
        agent = BaseAgent(name="planner", llm=llm)
        config = _make_config(
            db=db_session, agent=agent, prompt_loader=prompt_loader,
            artifact_service=artifact_service, run_service=run_service,
            publisher=event_publisher,
        )
        project_id = await _make_project(db_session)
        run_id = await _make_run(db_session, project_id)

        final_state = await build_creation_workflow().ainvoke(
            _make_initial_state(str(project_id), run_id), config
        )

        assert final_state["status"] == "completed"
        history = llm.get_call_history()
        # 故障确实被注入（第 1 次调用为 rate_limited），且最终成功
        assert history[0].error_code == LLMErrorCode.RATE_LIMITED
        assert history[1].parsed is not None


# ========================================================================
# 2. timeout 耗尽 → LLM_TIMEOUT
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
class TestTimeoutExhausted:
    """timeout 全部尝试耗尽 → 节点失败，error_code=LLM_TIMEOUT。"""

    async def test_timeout_exhausted_fails_with_code(
        self,
        db_session: Any,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
        run_service: RunService,
        event_publisher: EventPublisher,
    ) -> None:
        llm = _make_llm(retry_policy=RetryPolicy(base_delay=0.01, max_retries=2))
        for idx in (1, 2, 3):
            llm.inject_fault(idx, "timeout")  # 3 次尝试全部超时
        agent = BaseAgent(name="planner", llm=llm)
        config = _make_config(
            db=db_session, agent=agent, prompt_loader=prompt_loader,
            artifact_service=artifact_service, run_service=run_service,
            publisher=event_publisher,
        )
        project_id = await _make_project(db_session)
        run_id = await _make_run(db_session, project_id)

        final_state = await build_creation_workflow().ainvoke(
            _make_initial_state(str(project_id), run_id), config
        )

        assert final_state["status"] == "failed"
        assert final_state["error_node"] == "normalize"
        # 验收 #7：每失败均有 error_code
        assert final_state["error_code"] == "LLM_TIMEOUT"
        assert len(llm.get_call_history()) == 3  # 全部尝试已耗尽


# ========================================================================
# 3. invalid schema → Parser 带反馈重试成功
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
class TestInvalidSchemaParserRetry:
    """输出合法 JSON 但 Schema 校验失败 → Parser 带反馈重试成功。"""

    async def test_invalid_schema_retries_to_success(
        self,
        db_session: Any,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
        run_service: RunService,
        event_publisher: EventPublisher,
    ) -> None:
        llm = _make_llm()  # 无 HTTP 层重试策略——Parser 重试独立于 HTTP 层
        llm.inject_fault(1, "invalid_schema")  # 第 1 次返回合法 JSON {} → Parser 带反馈重试
        agent = BaseAgent(name="planner", llm=llm)
        config = _make_config(
            db=db_session, agent=agent, prompt_loader=prompt_loader,
            artifact_service=artifact_service, run_service=run_service,
            publisher=event_publisher,
        )
        project_id = await _make_project(db_session)
        run_id = await _make_run(db_session, project_id)

        final_state = await build_creation_workflow().ainvoke(
            _make_initial_state(str(project_id), run_id), config
        )

        assert final_state["status"] == "completed"
        # normalize_requirement 的第一次调用被 Parser 重试覆盖（2 次尝试）
        history = llm.get_call_history()
        assert history[0].error_detail  # 第一次返回的是"Schema 校验失败"输出
        assert history[0].parsed is None
        assert history[1].parsed is not None


# ========================================================================
# 4. 硬预算超限 → RUN_BUDGET_EXCEEDED
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
class TestHardBudget:
    """per-run 硬预算超限 → failed + RUN_BUDGET_EXCEEDED。"""

    async def test_hard_budget_exceeded(
        self,
        db_session: Any,
        workflow_config: dict[str, Any],
    ) -> None:
        project_id = await _make_project(db_session)
        run_id = await _make_run(db_session, project_id)
        enter_run(run_id, hard_calls=1)  # 只允许 1 次 LLM 调用
        try:
            final_state = await build_creation_workflow().ainvoke(
                _make_initial_state(str(project_id), run_id),
                workflow_config,
            )
        finally:
            exit_run(run_id)

        assert final_state["status"] == "failed"
        # 第 1 次调用（normalize）成功计数后，第 2 次（story_bible）触发硬上限
        assert final_state["error_code"] == "RUN_BUDGET_EXCEEDED"
        assert final_state["error_node"] == "story_bible"


# ========================================================================
# 5. 协作式取消 → RunCancelledError + 无新 Artifact
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
class TestCooperativeCancel:
    """请求取消后，下一节点守卫抛出 RunCancelledError，且不创建新 Artifact。"""

    async def test_cancel_stops_before_new_artifacts(
        self,
        db_session: Any,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
        run_service: RunService,
        event_publisher: EventPublisher,
    ) -> None:
        llm = _make_llm()
        agent = BaseAgent(name="planner", llm=llm)
        config = _make_config(
            db=db_session, agent=agent, prompt_loader=prompt_loader,
            artifact_service=artifact_service, run_service=run_service,
            publisher=event_publisher,
        )
        project_id = await _make_project(db_session)
        run_id = await _make_run(db_session, project_id)

        # 播种前 4 个节点已完成及其 Artifact（write_episodes 是下一个节点）
        req = NormalizedRequirement.model_validate(_load_golden("requirement_football"))
        req_art = await artifact_service.create_validated_artifact(
            db_session, project_id=project_id,
            artifact_type="normalized_requirement", content=req.model_dump(),
        )
        sb = StoryBible.model_validate(_load_golden("story_bible_football"))
        sb_art = await artifact_service.create_validated_artifact(
            db_session, project_id=project_id,
            artifact_type="story_bible", content=sb.model_dump(),
            source_artifact_ids=[
                {"artifact_id": str(req_art.id), "version": req_art.version, "relation": "derived_from"},
            ],
        )
        ol = EpisodeOutlineSet.model_validate(_load_golden("outline_set_valid"))
        ol_art = await artifact_service.create_validated_artifact(
            db_session, project_id=project_id,
            artifact_type="episode_outline_set", content=ol.model_dump(),
            source_artifact_ids=[
                {"artifact_id": str(sb_art.id), "version": sb_art.version, "relation": "derived_from"},
            ],
        )

        initial_state = _make_initial_state(str(project_id), run_id)
        initial_state["completed_nodes"] = ["normalize", "retrieve", "story_bible", "outline"]
        initial_state["requirement_artifact_id"] = str(req_art.id)
        initial_state["story_bible_artifact_id"] = str(sb_art.id)
        initial_state["outline_set_artifact_id"] = str(ol_art.id)

        request_cancel(run_id)
        try:
            with pytest.raises(RunCancelledError):
                await build_creation_workflow().ainvoke(initial_state, config)
        finally:
            clear_cancel(run_id)

        # 验收：cancel 后不创建新 Artifact（write_episodes 从未运行）
        scripts = await _count_artifacts_by_type(
            artifact_service, db_session, project_id, "script_draft"
        )
        assert scripts == 0
        assert len(llm.get_call_history()) == 0  # 无任何 LLM 调用


# ========================================================================
# 6. checkpoint 恢复 → 不重调已完成节点、不重写已写集
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
class TestCheckpointResume:
    """从 checkpoint 恢复：completed_nodes 早退 + 已写集跳过。"""

    async def test_resume_skips_completed_nodes(
        self,
        db_session: Any,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
        run_service: RunService,
        event_publisher: EventPublisher,
    ) -> None:
        llm = _make_llm()
        agent = BaseAgent(name="planner", llm=llm)
        config = _make_config(
            db=db_session, agent=agent, prompt_loader=prompt_loader,
            artifact_service=artifact_service, run_service=run_service,
            publisher=event_publisher,
        )

        # ---- A. 全新完整运行：记录 LLM 调用总数 ----
        proj_a = await _make_project(db_session)
        run_a = await _make_run(db_session, proj_a)
        await build_creation_workflow().ainvoke(
            _make_initial_state(str(proj_a), run_a), config
        )
        fresh_calls = len(llm.get_call_history())

        # ---- B. 从 checkpoint 恢复：前 4 节点已完成 + 第 1 集已写 ----
        proj_b = await _make_project(db_session)
        run_b = await _make_run(db_session, proj_b)
        req = NormalizedRequirement.model_validate(_load_golden("requirement_football"))
        req_art = await artifact_service.create_validated_artifact(
            db_session, project_id=proj_b,
            artifact_type="normalized_requirement", content=req.model_dump(),
        )
        sb = StoryBible.model_validate(_load_golden("story_bible_football"))
        sb_art = await artifact_service.create_validated_artifact(
            db_session, project_id=proj_b,
            artifact_type="story_bible", content=sb.model_dump(),
            source_artifact_ids=[
                {"artifact_id": str(req_art.id), "version": req_art.version, "relation": "derived_from"},
            ],
        )
        ol = EpisodeOutlineSet.model_validate(_load_golden("outline_set_valid"))
        ol_art = await artifact_service.create_validated_artifact(
            db_session, project_id=proj_b,
            artifact_type="episode_outline_set", content=ol.model_dump(),
            source_artifact_ids=[
                {"artifact_id": str(sb_art.id), "version": sb_art.version, "relation": "derived_from"},
            ],
        )
        script1 = ScriptDraft.model_validate(_load_golden("script_draft_valid"))
        script1_art = await artifact_service.create_validated_artifact(
            db_session, project_id=proj_b,
            # ScriptDraft 含 UUID 字段，须以 JSON 模式序列化（同 write_episode 节点）
            artifact_type="script_draft", episode_number=1, content=script1.model_dump(mode="json"),
            source_artifact_ids=[
                {"artifact_id": str(ol_art.id), "version": ol_art.version, "relation": "derived_from"},
            ],
        )
        script1_id = str(script1_art.id)

        llm.reset()  # 只统计恢复运行的调用
        resume_state = _make_initial_state(str(proj_b), run_b)
        resume_state["completed_nodes"] = ["normalize", "retrieve", "story_bible", "outline"]
        resume_state["requirement_artifact_id"] = str(req_art.id)
        resume_state["story_bible_artifact_id"] = str(sb_art.id)
        resume_state["outline_set_artifact_id"] = str(ol_art.id)
        resume_state["script_artifact_ids"] = {"1": script1_id}

        final_state = await build_creation_workflow().ainvoke(resume_state, config)
        resume_calls = len(llm.get_call_history())

        assert final_state["status"] == "completed"
        # 第 1 集未被重写（existing_scripts 跳过）
        assert final_state["script_artifact_ids"]["1"] == script1_id
        # 恢复运行少调用：normalize/story_bible/outline（3 次）已被 completed_nodes 跳过，
        # 且第 1 集的 write_episode（1 次）已被已写集跳过 → 共少 4 次。
        assert resume_calls == fresh_calls - 4
