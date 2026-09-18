"""剧情状态失效与只读查询测试(M-04,W3-04)。

验收:
- 采用第 N 集新版本后,状态相对新工作集 stale,自最早变化集起待复核;
- 来源完全相同的前缀可复用(不重算);
- GET /projects/{id}/story-state 只读:ready/pending/gap/stale 语义正确,
  全程零模型调用(任何模型调用都会使测试失败);
- 旧 Run 固定快照(run_id 视图)不受新版本影响。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.application.run_service import RunService
from app.application.story_state_service import (
    StoryStateService,
    StoryWorkset,
)
from app.db.models.project import Project
from app.domain.script import ScriptDraft
from app.llm.fake import FakeLLM

_GOLDEN = Path(__file__).resolve().parents[2] / "golden"


def _load(name: str) -> dict[str, Any]:
    with open(_GOLDEN / f"{name}.json", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "expected_output" in data:
        return cast(dict[str, Any], data["expected_output"])
    return cast(dict[str, Any], data)


def _script(n: int, variant: str = "") -> dict[str, Any]:
    content = dict(_load("script_draft_valid"))
    content["episode_number"] = n
    content["plain_text"] = f"第{n}集正文{variant}"
    content["title"] = f"第{n}集{variant}"
    return content


class _DeltaStub:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def __call__(self, messages: list[dict[str, str]]) -> Any:
        import re

        from app.domain.summary import EpisodeDelta

        match = re.search(r"第 (\d+) 集剧本", messages[-1]["content"])
        episode = int(match.group(1)) if match else 1
        self.calls.append(episode)
        return EpisodeDelta(
            episode_number=episode, summary=f"第{episode}集完成"
        )


async def _seed_chain(
    db: Any, artifact_service: ArtifactService, project_id: uuid.UUID,
    episodes: int,
) -> tuple[StoryWorkset, StoryStateService, Any, Any, dict[int, Any], _DeltaStub]:
    await RunService().create_run(
        db, project_id=project_id, action="create_script"
    )
    sb = await artifact_service.create_validated_artifact(
        db, project_id=project_id, artifact_type="story_bible",
        content=_load("story_bible_football"),
    )
    outline = await artifact_service.create_validated_artifact(
        db, project_id=project_id, artifact_type="episode_outline_set",
        content=_load("outline_set_valid"),
    )
    stub = _DeltaStub()
    llm = FakeLLM(seed=42)
    llm.register_factory("episode_summary_v2", stub)
    agent = BaseAgent(name="summarizer", llm=llm)
    scripts: dict[int, Any] = {}
    for n in range(1, episodes + 1):
        ScriptDraft.model_validate(_script(n))
        scripts[n] = await artifact_service.create_validated_artifact(
            db, project_id=project_id, artifact_type="script_draft",
            episode_number=n, content=_script(n),
        )
    workset = StoryWorkset(
        project_id=project_id,
        story_bible_artifact_id=sb.id,
        outline_artifact_id=outline.id,
        scripts={n: a.id for n, a in scripts.items()},
    )
    service = StoryStateService(agent)
    return workset, service, sb, outline, scripts, stub


@pytest.mark.integration
class TestAdoptionInvalidation:
    async def test_new_version_of_episode_marks_suffix_stale(
        self, db_session: Any
    ) -> None:
        """第 2 集采用新版本:状态相对新工作集 stale,自第 2 集起待复核;
        第 1 集前缀(来源相同)复用,不重新派生。"""
        project_id = uuid.uuid4()
        db_session.add(Project(id=project_id, title="失效测试", status="draft"))
        await db_session.flush()
        artifact_service = ArtifactService()
        workset, service, _sb, _outline, scripts, stub = await _seed_chain(
            db_session, artifact_service, project_id, 3
        )
        outcome = await service.ensure_state_through(db_session, workset, 3)
        assert outcome.model_calls == 3
        ready = await service.resolve_status(db_session, workset, 3)
        assert ready["status"] == "ready"

        # 采用变化:第 2 集新版本(新 Artifact)
        new_ep2 = await artifact_service.create_validated_artifact(
            db_session, project_id=project_id, artifact_type="script_draft",
            episode_number=2, content=_script(2, variant="(修订版)"),
        )
        new_workset = StoryWorkset(
            project_id=project_id,
            story_bible_artifact_id=workset.story_bible_artifact_id,
            outline_artifact_id=workset.outline_artifact_id,
            scripts={1: scripts[1].id, 2: new_ep2.id, 3: scripts[3].id},
        )
        status = await service.resolve_status(db_session, new_workset, 3)
        assert status["status"] == "stale"
        assert status["stale_from_episode"] == 2

        # 重建后缀:前缀(第 1 集)零模型调用复用,只重派 2-3
        calls_before = list(stub.calls)
        rebuilt = await service.ensure_state_through(
            db_session, new_workset, 3
        )
        assert rebuilt.model_calls == 2  # 只有 2、3 集
        assert stub.calls == calls_before + [2, 3]
        assert rebuilt.state.through_episode == 3

    async def test_old_run_snapshot_unaffected(
        self, db_session: Any
    ) -> None:
        """旧 Run 的固定工作集视图在新版本采用后仍 ready(历史快照)。"""
        project_id = uuid.uuid4()
        db_session.add(Project(id=project_id, title="快照测试", status="draft"))
        await db_session.flush()
        artifact_service = ArtifactService()
        workset, service, _sb, _outline, _scripts, _stub = await _seed_chain(
            db_session, artifact_service, project_id, 2
        )
        await service.ensure_state_through(db_session, workset, 2)
        # 新版本第 2 集
        await artifact_service.create_validated_artifact(
            db_session, project_id=project_id, artifact_type="script_draft",
            episode_number=2, content=_script(2, variant="(新版)"),
        )
        # 旧工作集视图不变
        old_status = await service.resolve_status(db_session, workset, 2)
        assert old_status["status"] == "ready"


@pytest.mark.integration
class TestStoryStateEndpoint:
    async def test_get_story_state_statuses_and_zero_model_calls(
        self, db_session: Any, test_engine: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET 全程零模型调用;ready/pending/gap/stale 语义正确。"""
        from app.core.config import Settings
        from app.main import create_app

        project_id = uuid.uuid4()
        db_session.add(Project(id=project_id, title="端点测试", status="draft",
                               target_episode_count=3, current_episode_count=0))
        await db_session.flush()
        artifact_service = ArtifactService()
        workset, service, _sb, _outline, scripts, _stub = await _seed_chain(
            db_session, artifact_service, project_id, 2
        )
        await service.ensure_state_through(db_session, workset, 2)
        await db_session.commit()

        # 任何模型调用都会让 GET 失败(零模型调用保证)
        async def _no_model(*a: Any, **kw: Any) -> Any:
            raise AssertionError("GET story-state 不得调用模型")

        monkeypatch.setattr(BaseAgent, "generate_structured", _no_model)

        app = create_app(settings=Settings(app_env="test"))
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        import app.db.session as db_session_module

        db_session_module._async_session_factory = async_sessionmaker(
            test_engine, class_=AsyncSession, expire_on_commit=False
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # ready(默认工作集=各集最新 valid,恰好等于已派生链)
            resp = await client.get(f"/api/v1/projects/{project_id}/story-state")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["status"] == "ready"
            assert body["through_episode"] == 2
            assert body["state_artifact_id"]
            assert set(body["episode_summary_artifact_ids"]) == {"1", "2"}

            # pending:请求超前(第 3 集尚未写)→ gap(正文缺失)
            resp3 = await client.get(
                f"/api/v1/projects/{project_id}/story-state?through_episode=3"
            )
            assert resp3.json()["status"] == "gap"
            assert resp3.json()["missing_episodes"] == [3]

            # 新版本第 2 集 → stale(自 2 起)
            await artifact_service.create_validated_artifact(
                db_session, project_id=project_id, artifact_type="script_draft",
                episode_number=2, content=_script(2, variant="(新版)"),
            )
            await db_session.commit()
            resp_stale = await client.get(
                f"/api/v1/projects/{project_id}/story-state?through_episode=3"
            )
            body_stale = resp_stale.json()
            # 第 3 集正文缺失仍优先报 gap;限定到 2 集时为 stale
            resp_stale2 = await client.get(
                f"/api/v1/projects/{project_id}/story-state?through_episode=2"
            )
            assert resp_stale2.json()["status"] == "stale"
            assert resp_stale2.json()["stale_from_episode"] == 2
            assert body_stale["status"] == "gap"

            # 未知项目 404
            resp404 = await client.get(
                f"/api/v1/projects/{uuid.uuid4()}/story-state"
            )
            assert resp404.status_code == 404


@pytest_asyncio.fixture
async def db_session(test_engine: Any) -> Any:
    """本目录的独立 DB 会话(结束回滚;失效测试需要提交时自行 commit)。"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session
        await session.rollback()
