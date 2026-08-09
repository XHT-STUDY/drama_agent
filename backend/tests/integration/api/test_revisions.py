"""Revision API 闭环契约测试 (F-06).

测试范围:
- POST /projects/{id}/revisions 自动修订 → 202 + run_id → 轮询 completed → 结果链可查
- POST 指定剧本 + user_instruction → ep1 恰 2 版本、计划含指令、候选/连续性/新评估齐全
- 指定剧本无绑定评估 → 404 EVALUATION_NOT_FOUND
- 跨项目剧本 → 403 CROSS_PROJECT_ACCESS
- GET /revisions 列表 200
- GET /revisions/{plan_id} 计划详情 + 结果链
- OpenAPI paths 含 /revisions

Worker 在 APP_ENV=test 下使用 FakeLLM + golden fixtures（runs.py 注册），
不访问外部 LLM API。播种经 ArtifactService + 自带 db_session。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.artifact_service import ArtifactService
from app.domain.enums import DEFAULT_EVALUATION_WEIGHTS
from app.domain.evaluation import compute_overall_score

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"


@pytest_asyncio.fixture
async def db_session(test_engine: Any) -> AsyncGenerator[AsyncSession, None]:
    """基于 conftest test_engine 的独立数据库会话。"""
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


def _load_golden(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(
        (GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8")
    ))


def _low_content(script_id: str) -> dict[str, Any]:
    """低分评估报告（conflict_intensity=40 → overall≈73.2，需修订）。

    保留 golden 评估的 issues（含 iss_001），使 revision_plan_valid fixture
    能通过 filter_grounded_operations。
    """
    data = _load_golden("evaluation_report_valid")
    dims = dict(data["dimension_scores"])
    dims["conflict_intensity"] = 40
    content = dict(data)
    content["episode_number"] = 1
    content["script_artifact_id"] = script_id
    content["dimension_scores"] = dims
    content["overall_score"] = compute_overall_score(dims, DEFAULT_EVALUATION_WEIGHTS)
    content["need_revision"] = True
    return content


async def _create_project(async_client: AsyncClient, title: str = "修订 API 测试") -> str:
    resp = await async_client.post("/api/v1/projects", json={"title": title})
    assert resp.status_code == 201
    return cast(str, resp.json()["id"])


async def _seed_revision_project(
    async_client: AsyncClient,
    db: AsyncSession,
    *,
    with_eval: bool = True,
) -> dict[str, str]:
    """创建项目 + story_bible + outline + script(ep1) [+ 低分评估]，先 commit 再 POST。"""
    project_id = await _create_project(async_client)

    svc = ArtifactService()
    sb = await svc.create_validated_artifact(
        db, project_id=uuid.UUID(project_id), artifact_type="story_bible",
        content=_load_golden("story_bible_valid"),
    )
    outline = await svc.create_validated_artifact(
        db, project_id=uuid.UUID(project_id), artifact_type="episode_outline_set",
        content=_load_golden("outline_set_valid"),
    )
    script = await svc.create_validated_artifact(
        db, project_id=uuid.UUID(project_id), artifact_type="script_draft",
        episode_number=1, content=_load_golden("script_draft_valid"),
        source_artifact_ids=[
            {"artifact_id": str(outline.id), "version": outline.version, "relation": "derived_from"},
            {"artifact_id": str(sb.id), "version": sb.version, "relation": "references"},
        ],
    )
    eval_id: str | None = None
    if with_eval:
        ev = await svc.create_validated_artifact(
            db, project_id=uuid.UUID(project_id), artifact_type="evaluation_report",
            episode_number=1, content=_low_content(str(script.id)),
            source_artifact_ids=[
                {"artifact_id": str(script.id), "version": script.version, "relation": "derived_from"},
            ],
        )
        eval_id = str(ev.id)
    await db.commit()
    return {
        "project_id": project_id,
        "script_id": str(script.id),
        "eval_id": eval_id or "",
    }


async def _wait_run_terminal(
    async_client: AsyncClient, run_id: str, loops: int = 60,
) -> tuple[str, dict[str, Any]]:
    """轮询 Run 状态直至终态（completed/failed/needs_review）。"""
    for _ in range(loops):
        await asyncio.sleep(0.2)
        resp = await async_client.get(f"/api/v1/runs/{run_id}")
        status = resp.json()["status"]
        if status in ("completed", "failed", "needs_review"):
            return status, resp.json()
    return "timeout", {}


@pytest.mark.integration
@pytest.mark.asyncio
class TestCreateRevision:
    """POST /revisions 发起修订。"""

    async def test_auto_revision_runs_to_completion(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        """自动修订：202 + run_id → completed → 计划可查、结果链完整。"""
        seed = await _seed_revision_project(async_client, db_session)

        resp = await async_client.post(
            f"/api/v1/projects/{seed['project_id']}/revisions", json={}
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "run_id" in data
        assert data["action"] == "revise"
        assert data["status"] == "queued"

        run_id = data["run_id"]
        status, run_data = await _wait_run_terminal(async_client, run_id)
        assert status == "completed", f"run 未完成: {run_data}"

        # 修订计划列表
        list_resp = await async_client.get(
            f"/api/v1/projects/{seed['project_id']}/revisions"
        )
        assert list_resp.status_code == 200
        plans = list_resp.json()["items"]
        assert len(plans) == 1
        plan = plans[0]
        assert plan["type"] == "revision_plan"
        assert plan["episode_number"] == 1
        assert plan["content"]["episode_number"] == 1

        # 结果链：候选稿 / 连续性检查 / 新评估 / diff_ids
        detail_resp = await async_client.get(
            f"/api/v1/projects/{seed['project_id']}/revisions/{plan['id']}"
        )
        assert detail_resp.status_code == 200
        chain = detail_resp.json()["result_chain"]
        assert chain["source_script"] is not None
        assert chain["source_script"]["id"] == seed["script_id"]
        assert chain["source_evaluation"] is not None
        assert chain["candidate_script"] is not None
        assert chain["continuity_check"] is not None
        assert chain["new_evaluation"] is not None
        assert (
            chain["new_evaluation"]["content"]["script_artifact_id"]
            == chain["candidate_script"]["id"]
        )
        assert chain["diff_ids"]["base"] == seed["script_id"]
        assert chain["diff_ids"]["target"] == chain["candidate_script"]["id"]

    async def test_specific_script_with_user_instruction(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        """指定剧本 + user_instruction：ep1 恰 2 版本、计划含指令、候选已提升 valid。"""
        seed = await _seed_revision_project(async_client, db_session)
        instruction = "加强反派动机，但不得改变主角身世"

        resp = await async_client.post(
            f"/api/v1/projects/{seed['project_id']}/revisions",
            json={
                "script_artifact_id": seed["script_id"],
                "user_instruction": instruction,
            },
        )
        assert resp.status_code == 202
        run_id = resp.json()["run_id"]
        status, run_data = await _wait_run_terminal(async_client, run_id)
        assert status == "completed", f"run 未完成: {run_data}"

        # ep1 恰好 2 个版本（原稿 v1 + 候选稿 v2），候选稿已提升 valid
        versions_resp = await async_client.get(
            f"/api/v1/artifacts/{seed['script_id']}/versions"
        )
        assert versions_resp.status_code == 200
        versions = versions_resp.json()
        assert len(versions) == 2
        candidate = versions[-1]
        assert candidate["version"] == 2
        assert candidate["status"] == "valid"

        # 修订计划含 user_instruction（可审计）
        list_resp = await async_client.get(
            f"/api/v1/projects/{seed['project_id']}/revisions"
        )
        plans = list_resp.json()["items"]
        assert len(plans) == 1
        assert plans[0]["content"]["user_instruction"] == instruction

        # 结果链绑定候选稿
        detail_resp = await async_client.get(
            f"/api/v1/projects/{seed['project_id']}/revisions/{plans[0]['id']}"
        )
        chain = detail_resp.json()["result_chain"]
        assert chain["candidate_script"]["id"] == candidate["id"]
        assert chain["continuity_check"] is not None
        assert (
            chain["new_evaluation"]["content"]["script_artifact_id"]
            == candidate["id"]
        )
        assert chain["diff_ids"]["target"] == candidate["id"]

    async def test_specific_script_without_evaluation_returns_404(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        """指定剧本无绑定评估 → 404 EVALUATION_NOT_FOUND（"已过期评估不匹配"拒绝）。"""
        seed = await _seed_revision_project(async_client, db_session, with_eval=False)

        resp = await async_client.post(
            f"/api/v1/projects/{seed['project_id']}/revisions",
            json={"script_artifact_id": seed["script_id"]},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "EVALUATION_NOT_FOUND"

    async def test_cross_project_script_returns_403(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        """跨项目剧本 → 403 CROSS_PROJECT_ACCESS。"""
        project_a = await _create_project(async_client, "项目A")
        seed_b = await _seed_revision_project(async_client, db_session)

        resp = await async_client.post(
            f"/api/v1/projects/{project_a}/revisions",
            json={"script_artifact_id": seed_b["script_id"]},
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "CROSS_PROJECT_ACCESS"


@pytest.mark.integration
@pytest.mark.asyncio
class TestQueryRevisions:
    """GET /revisions 查询。"""

    async def _seed_and_revise(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> str:
        """播种并跑一次自动修订，返回 project_id。"""
        seed = await _seed_revision_project(async_client, db_session)
        resp = await async_client.post(
            f"/api/v1/projects/{seed['project_id']}/revisions", json={}
        )
        assert resp.status_code == 202
        status, _ = await _wait_run_terminal(async_client, resp.json()["run_id"])
        assert status == "completed"
        return seed["project_id"]

    async def test_list_revisions_returns_200(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        """GET /revisions 返回分页列表。"""
        project_id = await self._seed_and_revise(async_client, db_session)

        resp = await async_client.get(f"/api/v1/projects/{project_id}/revisions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["type"] == "revision_plan"

    async def test_revision_detail_contains_chain(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        """GET /revisions/{plan_id} 返回计划 + 完整结果链（6 键齐全）。"""
        project_id = await self._seed_and_revise(async_client, db_session)

        list_resp = await async_client.get(f"/api/v1/projects/{project_id}/revisions")
        plan_id = list_resp.json()["items"][0]["id"]

        resp = await async_client.get(
            f"/api/v1/projects/{project_id}/revisions/{plan_id}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == plan_id
        chain = data["result_chain"]
        assert set(chain.keys()) == {
            "source_script", "source_evaluation", "candidate_script",
            "continuity_check", "new_evaluation", "diff_ids",
        }
        assert chain["source_script"] is not None
        assert chain["candidate_script"] is not None
        assert chain["new_evaluation"] is not None
        assert chain["diff_ids"]["target"] is not None

    async def test_revision_detail_cross_project_403(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        """跨项目访问修订计划 → 403。"""
        project_a = await _create_project(async_client, "项目A")
        project_b = await self._seed_and_revise(async_client, db_session)

        list_resp = await async_client.get(f"/api/v1/projects/{project_b}/revisions")
        plan_id = list_resp.json()["items"][0]["id"]

        resp = await async_client.get(f"/api/v1/projects/{project_a}/revisions/{plan_id}")
        assert resp.status_code == 403
        assert resp.json()["code"] == "CROSS_PROJECT_ACCESS"


@pytest.mark.integration
@pytest.mark.asyncio
class TestOpenAPIContract:
    """OpenAPI 契约。"""

    async def test_openapi_contains_revisions_paths(
        self, async_client: AsyncClient,
    ) -> None:
        """OpenAPI paths 包含 /revisions 端点。"""
        resp = await async_client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        revision_paths = [
            p for p in paths if p.endswith("/revisions")
            or p.endswith("/revisions/{plan_artifact_id}")
        ]
        assert len(revision_paths) >= 2, f"修订端点未注册: {list(paths.keys())}"
