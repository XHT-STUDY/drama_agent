from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.application.agent_context_service import AgentContextService
from app.core.config import Settings
from app.core.errors import InvalidActiveContextError
from app.db.models.artifact import Artifact
from app.db.models.project import Project
from app.domain.agent_command import ActiveArtifactContext
from app.memory.context_builder import ContextBuilder


class _Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class _Db:
    def __init__(self, value: Any) -> None:
        self.value = value

    async def execute(self, _statement: Any) -> _Result:
        return _Result(self.value)


def _project(project_id: uuid.UUID) -> Project:
    return Project(id=project_id, title="测试项目", target_episode_count=10)


@pytest.mark.asyncio
async def test_active_artifact_from_other_project_is_rejected() -> None:
    project_id = uuid.uuid4()
    other_project_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    project = _project(project_id)
    artifact = Artifact(
        id=artifact_id,
        project_id=other_project_id,
        type="script_draft",
        episode_number=3,
        version=1,
        content={"title": "第三集"},
        status="valid",
    )
    service = AgentContextService(settings=Settings(app_env="test"))
    active = ActiveArtifactContext(
        artifact_id=artifact_id,
        artifact_type="script_draft",
        episode_number=3,
    )

    with pytest.raises(InvalidActiveContextError) as exc_info:
        await service._load_active(_Db(artifact), project, active)  # type: ignore[arg-type]

    assert exc_info.value.code == "INVALID_ACTIVE_CONTEXT"


def test_project_context_contains_indexes_without_script_body() -> None:
    project_id = uuid.uuid4()
    project = _project(project_id)
    script = Artifact(
        id=uuid.uuid4(),
        project_id=project_id,
        type="script_draft",
        episode_number=3,
        version=2,
        content={"title": "逆袭", "word_count": 1200, "scenes": [], "plain_text": "不应进入"},
        status="valid",
    )
    evaluation = Artifact(
        id=uuid.uuid4(),
        project_id=project_id,
        type="evaluation_report",
        episode_number=3,
        version=1,
        content={"overall_score": 82.5, "need_revision": False, "issues": []},
        status="valid",
    )

    context = AgentContextService._project_context(
        project, None, None, [script], [evaluation]
    )

    assert "E03 v2" in context
    assert "score=82.5" in context
    assert "不应进入" not in context


def test_protected_user_request_is_not_silently_truncated() -> None:
    builder = ContextBuilder(budget_tokens=100)
    with pytest.raises(Exception) as exc_info:
        builder.build_for(
            "requirement",
            user_request="用户的完整目标" * 40,
            current_target="",
            protected_sections={"user_request", "current_target"},
        )
    assert getattr(exc_info.value, "code", None) == "CONTEXT_TOO_LARGE"


def test_agent_budget_defaults() -> None:
    settings = Settings(app_env="test")
    assert settings.agent_context_budget_tokens == 12000
    assert settings.agent_recent_message_limit == 12
    assert settings.agent_turn_lease_seconds == 120
    assert settings.agent_turn_max_tokens == 16000
    assert settings.agent_max_replan_depth == 1
