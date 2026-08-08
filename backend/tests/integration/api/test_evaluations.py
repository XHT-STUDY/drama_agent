"""Evaluation Service 与 API 集成测试 (E-03).

测试范围:
- evaluate_script 编排:剧本 → 评估报告持久化,绑定剧本版本
- 幂等复用:同一剧本版本重复评估返回已有报告
- 跨项目防护:不允许评估其他项目的 Artifact
- evaluate_many 按集号排序
- 查询 API:GET /evaluations、GET /evaluations/for-script/{sid}

全部测试使用 FakeLLM,不访问外部 LLM API。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.base import BaseAgent
from app.agents.evaluation import EvaluationAgent
from app.application.artifact_service import ArtifactService
from app.application.evaluation_service import EvaluationService
from app.core.errors import AppError
from app.domain.evaluation import EvaluationReport
from app.llm.fake import FakeLLM
from app.prompts.loader import PromptLoader
from app.skills.evaluator import EvaluationSkill
from app.skills.registry import SkillRegistry

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"


@pytest_asyncio.fixture
async def db_session(test_engine: Any) -> AsyncGenerator[AsyncSession, None]:
    """基于 conftest test_engine 的独立数据库会话。"""
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


def _load_golden(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(
        (GOLDEN_DIR / name).read_text(encoding="utf-8")
    ))


def _build_evaluator(script_artifact_id: str | None = None) -> EvaluationAgent:
    """构造带 FakeLLM 注册 evaluate_episode fixture 的评估器。"""
    llm = FakeLLM(seed=42)
    report_data = _load_golden("evaluation_report_valid.json")
    report_data["script_artifact_id"] = script_artifact_id or str(uuid.uuid4())
    llm.register(
        "evaluate_episode",
        EvaluationReport.model_validate(report_data),
    )
    registry = SkillRegistry()
    registry.register(EvaluationSkill())
    return EvaluationAgent(
        base_agent=BaseAgent(name="evaluator", llm=llm),
        skill_registry=registry,
    )


async def _create_project(async_client: AsyncClient, title: str = "评估测试项目") -> str:
    resp = await async_client.post("/api/v1/projects", json={"title": title})
    assert resp.status_code == 201
    return cast(str, resp.json()["id"])


async def _seed_artifacts(
    db: AsyncSession,
    project_id: str,
) -> tuple[str, str, str]:
    """创建 outline/story_bible/script 三个 Artifact，返回 script id。"""
    svc = ArtifactService()
    outline = await svc.create_validated_artifact(
        db, project_id=uuid.UUID(project_id),
        artifact_type="episode_outline_set",
        content=_load_golden("outline_set_valid.json"),
    )
    sb = await svc.create_validated_artifact(
        db, project_id=uuid.UUID(project_id),
        artifact_type="story_bible",
        content=_load_golden("story_bible_valid.json"),
    )
    script = await svc.create_validated_artifact(
        db, project_id=uuid.UUID(project_id),
        artifact_type="script_draft",
        episode_number=1,
        content=_load_golden("script_draft_valid.json"),
        source_artifact_ids=[
            {"artifact_id": str(outline.id), "version": outline.version, "relation": "derived_from"},
            {"artifact_id": str(sb.id), "version": sb.version, "relation": "references"},
        ],
    )
    await db.commit()
    return str(script.id), str(outline.id), str(sb.id)


@pytest.mark.integration
@pytest.mark.asyncio
class TestEvaluationService:
    """EvaluationService 编排。"""

    async def test_evaluate_script_creates_report(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        """evaluate_script 生成并持久化评估报告，绑定剧本版本。"""
        project_id = await _create_project(async_client)
        script_id, _, _ = await _seed_artifacts(db_session, project_id)
        evaluator = _build_evaluator(script_id)

        svc = EvaluationService()
        artifact = await svc.evaluate_script(
            db_session, project_id=uuid.UUID(project_id),
            script_artifact_id=uuid.UUID(script_id),
            evaluator=evaluator,
            prompt_loader=PromptLoader(),
        )
        await db_session.commit()

        assert artifact.type == "evaluation_report"
        assert artifact.episode_number == 1
        assert artifact.status == "valid"
        # 绑定被评估的剧本版本
        assert artifact.content["script_artifact_id"] == script_id
        # overall 由服务端确定性回填
        assert artifact.content["overall_score"] == 77.3
        assert artifact.prompt_version == "1.1.0"

    async def test_evaluate_script_idempotent(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        """同一剧本版本重复评估返回已有报告（幂等复用）。"""
        project_id = await _create_project(async_client)
        script_id, _, _ = await _seed_artifacts(db_session, project_id)
        evaluator = _build_evaluator(script_id)

        svc = EvaluationService()
        first = await svc.evaluate_script(
            db_session, project_id=uuid.UUID(project_id),
            script_artifact_id=uuid.UUID(script_id),
            evaluator=evaluator,
            prompt_loader=PromptLoader(),
        )
        await db_session.commit()
        second = await svc.evaluate_script(
            db_session, project_id=uuid.UUID(project_id),
            script_artifact_id=uuid.UUID(script_id),
            evaluator=evaluator,
            prompt_loader=PromptLoader(),
        )
        await db_session.commit()
        assert second.id == first.id  # 复用,不新建

    async def test_cross_project_access_rejected(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        """不允许评估其他项目的 Artifact。"""
        project_a = await _create_project(async_client, "项目A")
        project_b = await _create_project(async_client, "项目B")
        script_id, _, _ = await _seed_artifacts(db_session, project_a)
        evaluator = _build_evaluator(script_id)

        svc = EvaluationService()
        with pytest.raises(AppError) as exc:
            await svc.evaluate_script(
                db_session, project_id=uuid.UUID(project_b),
                script_artifact_id=uuid.UUID(script_id),
                evaluator=evaluator,
                prompt_loader=PromptLoader(),
            )
        assert exc.value.code == "CROSS_PROJECT_ACCESS"

    async def test_evaluate_many_sorted_by_episode(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        """evaluate_many 结果按集号升序。"""
        project_id = await _create_project(async_client)
        script_id, _, _ = await _seed_artifacts(db_session, project_id)
        evaluator = _build_evaluator(script_id)

        svc = EvaluationService()
        results = await svc.evaluate_many(
            db_session, project_id=uuid.UUID(project_id),
            script_artifact_ids=[uuid.UUID(script_id)],
            evaluator=evaluator,
            prompt_loader=PromptLoader(),
        )
        await db_session.commit()
        assert len(results) == 1
        assert results[0].episode_number == 1


@pytest.mark.integration
@pytest.mark.asyncio
class TestEvaluationAPI:
    """评估查询 API。"""

    async def _seed_evaluation(
        self, async_client: AsyncClient, db: AsyncSession, project_id: str
    ) -> str:
        """创建剧本并评估,返回 script id。"""
        script_id, _, _ = await _seed_artifacts(db, project_id)
        evaluator = _build_evaluator(script_id)
        await EvaluationService().evaluate_script(
            db, project_id=uuid.UUID(project_id),
            script_artifact_id=uuid.UUID(script_id),
            evaluator=evaluator,
            prompt_loader=PromptLoader(),
        )
        await db.commit()
        return script_id

    async def test_list_evaluations(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        """GET /evaluations 返回评估列表。"""
        project_id = await _create_project(async_client)
        await self._seed_evaluation(async_client, db_session, project_id)

        resp = await async_client.get(f"/api/v1/projects/{project_id}/evaluations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["type"] == "evaluation_report"

    async def test_get_evaluation_for_script(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        """GET /evaluations/for-script/{sid} 返回该剧本的评估。"""
        project_id = await _create_project(async_client)
        script_id = await self._seed_evaluation(async_client, db_session, project_id)

        resp = await async_client.get(
            f"/api/v1/projects/{project_id}/evaluations/for-script/{script_id}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"]["script_artifact_id"] == script_id

    async def test_get_evaluation_not_found(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        """无评估报告时返回 404。"""
        project_id = await _create_project(async_client)
        resp = await async_client.get(
            f"/api/v1/projects/{project_id}/evaluations/for-script/{uuid.uuid4()}"
        )
        assert resp.status_code == 404
