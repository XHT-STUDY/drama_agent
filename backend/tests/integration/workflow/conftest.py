"""Creation Workflow 测试 fixtures (C-07)."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.application.run_service import RunService
from app.domain.evaluation import EvaluationReport
from app.domain.outline import EpisodeOutlineSet
from app.domain.requirement import NormalizedRequirement
from app.domain.revision import (
    ContinuitySemanticCheck,
    RevisionPlan,
    RevisionResult,
)
from app.domain.script import ScriptDraft
from app.domain.story_bible import StoryBible
from app.events.publisher import EventPublisher
from app.llm.fake import FakeLLM
from app.prompts.loader import PromptLoader


def _load_golden(name: str) -> dict[str, Any]:
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "..", "golden", f"{name}.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "expected_output" in data:
        return data["expected_output"]
    return data


@pytest.fixture
def fake_llm() -> FakeLLM:
    llm = FakeLLM(seed=42)
    req_golden = _load_golden("requirement_football")
    llm.register("normalize_requirement", NormalizedRequirement.model_validate(req_golden))
    llm.register("story_bible", StoryBible.model_validate(_load_golden("story_bible_football")))
    llm.register("outline", EpisodeOutlineSet.model_validate(_load_golden("outline_set_valid")))
    script_draft = ScriptDraft.model_validate(_load_golden("script_draft_valid"))
    llm.register("write_episode", script_draft)
    # 评估 fixture：高分 golden（服务端回填 need_revision=False → creation 走 finalize）
    llm.register("evaluate_episode", EvaluationReport.model_validate(_load_golden("evaluation_report_valid")))
    # 修订分支 fixtures（F-05）：默认走"通过"语义（连续性 pass）
    llm.register("revision_plan", RevisionPlan.model_validate(_load_golden("revision_plan_valid")))
    llm.register("revise_episode", RevisionResult.model_validate(_load_golden("revised_episode_football")))
    llm.register(
        "continuity_semantic_check",
        ContinuitySemanticCheck.model_validate(_load_golden("continuity_semantic_check_valid")),
    )
    return llm


@pytest.fixture
def agent(fake_llm: FakeLLM) -> BaseAgent:
    return BaseAgent(name="planner", llm=fake_llm)


@pytest.fixture
def prompt_loader() -> PromptLoader:
    return PromptLoader()


@pytest.fixture
def artifact_service() -> ArtifactService:
    return ArtifactService()


@pytest.fixture
def run_service() -> RunService:
    return RunService()


@pytest.fixture
def event_publisher() -> EventPublisher:
    return EventPublisher()


@pytest_asyncio.fixture
async def db_session(test_engine: Any) -> AsyncGenerator[Any, None]:
    """为工作流测试提供 DB 会话。

    注意：不使用 session.begin() 包裹——EventPublisher 的 autocommit
    会执行 commit + re-begin，与嵌套事务上下文冲突
    （此前导致 "Can't operate on closed transaction" 存量失败）。
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def test_project(db_session: Any) -> uuid.UUID:
    """在测试 DB 中创建 Project 并提交。"""
    from app.db.models.project import Project

    pid = uuid.uuid4()
    project = Project(id=pid, title="C-07 测试项目", status="draft")
    db_session.add(project)
    await db_session.flush()
    return pid


@pytest.fixture
def workflow_config(
    db_session: Any,
    agent: BaseAgent,
    prompt_loader: PromptLoader,
    artifact_service: ArtifactService,
    run_service: RunService,
    event_publisher: EventPublisher,
) -> dict[str, Any]:
    progress_log: list[dict[str, Any]] = []

    def progress_callback(node: str, event: str, progress: float) -> None:
        progress_log.append({"node": node, "event": event, "progress": progress})

    return {
        "configurable": {
            "db": db_session,
            "agent": agent,
            "prompt_loader": prompt_loader,
            "artifact_service": artifact_service,
            "run_service": run_service,
            "event_publisher": event_publisher,
            "user_input": "写一个关于被青训队抛弃的足球少年的逆袭故事。",
            "source_type": "idea",
            "rag_context": "",
            "progress_callback": progress_callback,
            "progress_log": progress_log,
        },
    }
