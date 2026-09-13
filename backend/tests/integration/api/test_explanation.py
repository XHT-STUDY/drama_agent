"""W1-03 明确目标直达与有原文的解释——集成测试。

覆盖任务卡最小正反样例：
- 目标优先级：明确集数 > 活动上下文 > 无目标（None 回落）；
- 解释读取确切稿件（含历史版本只读解释）并按场景组装原文来源；
- 超预算/无正文 → 窄化与有限答复（不假装读完）；
- 引文服务端验证与回填（artifact_id/version/checksum），伪造引文剔除；
- 同 Turn 重放不二次调用；scene_number 校验与 request_hash 纳入。
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.base import BaseAgent
from app.api.dependencies import get_agent_command_service
from app.application.agent_command_service import AgentCommandService
from app.application.agent_context_service import AgentContextService
from app.application.artifact_service import ArtifactService
from app.core.config import Settings
from app.core.errors import InvalidActiveContextError
from app.db.models.project import Project
from app.domain.agent_planner import (
    AgentPlannerOutput,
    ArtifactExplanationOutput,
    ExplanationCitationOutput,
)
from app.domain.enums import ArtifactType
from app.llm.fake import FakeLLM

_GOLDEN = Path(__file__).resolve().parents[2] / "golden"


def _load(name: str) -> dict[str, Any]:
    data = json.loads((_GOLDEN / f"{name}.json").read_text(encoding="utf-8"))
    return data.get("expected_output", data) if isinstance(data, dict) else data


def _script_content(title: str = "第三集") -> dict[str, Any]:
    content = dict(_load("script_draft_valid"))
    content["title"] = title
    return content


@pytest_asyncio.fixture
async def db_session(test_engine: Any) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
def planner_llm() -> FakeLLM:
    llm = FakeLLM(seed=42)
    # Planner 桩：内容性问题路由 explain（真实路由由 v1.4 prompt 决定）
    llm.register(
        "agent_command_planner",
        AgentPlannerOutput(
            turn_type="answer",
            intent="explain",
            answer="（我来读一下原文再回答）",
        ),
    )
    return llm


@pytest.fixture
def agent_service(planner_llm: FakeLLM) -> AgentCommandService:
    return AgentCommandService(
        settings=Settings(app_env="test"),
        planner_agent=BaseAgent(name="planner", llm=planner_llm),
    )


@pytest_asyncio.fixture
async def agent_api(
    app: Any, agent_service: AgentCommandService
) -> AsyncGenerator[AsyncClient, None]:
    app.dependency_overrides[get_agent_command_service] = lambda: agent_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _seed_project_with_script(
    db: AsyncSession, *, episodes: list[int] | None = None, versions: int = 1
) -> tuple[Project, list[Any]]:
    project = Project(title="解释测试", target_episode_count=10)
    db.add(project)
    await db.flush()
    svc = ArtifactService()
    artifacts: list[Any] = []
    for ep in episodes or [3]:
        for v in range(versions):
            artifacts.append(
                await svc.create_validated_artifact(
                    db,
                    project_id=project.id,
                    artifact_type=ArtifactType.SCRIPT_DRAFT,
                    episode_number=ep,
                    content=_script_content(f"第{ep}集-v{v + 1}"),
                )
            )
    await db.commit()
    return project, artifacts


async def _turn(
    client: AsyncClient,
    project_id: str,
    content: str,
    key: str,
    active_context: dict[str, Any] | None = None,
) -> Any:
    body: dict[str, Any] = {"content": content, "idempotency_key": key}
    if active_context:
        body["active_context"] = active_context
    return await client.post(
        f"/api/v1/projects/{project_id}/agent/turns", json=body
    )


# ========================================================================
# 解释上下文（AgentContextService.build_explanation_context）
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
class TestBuildExplanationContext:
    async def test_explicit_episode_beats_active_context(
        self, db_session: AsyncSession
    ) -> None:
        """明确第 3 集（active 是第 2 集）→ 读第 3 集最新稿。"""
        project, _arts = await _seed_project_with_script(db_session, episodes=[2, 3])
        from app.db.repositories.artifacts import ArtifactRepository

        latest3 = await ArtifactRepository(db_session).get_latest_valid(
            project.id, "script_draft", 3
        )
        assert latest3 is not None

        ctx = await AgentContextService(
            settings=Settings(app_env="test")
        ).build_explanation_context(
            db_session,
            project,
            "第3集这场翻脸为什么这么突然",
            active_context=None,
        )
        assert ctx is not None and ctx.status == "ok"
        assert ctx.target.artifact_id == str(latest3.id)
        assert ctx.target.episode == 3
        # 剧本来源按场景组装
        assert all(s.kind == "script_scene" for s in ctx.sources[:2])

    async def test_active_context_used_when_no_explicit_target(
        self, db_session: AsyncSession
    ) -> None:
        """无明确目标时用活动上下文（含历史版本——只读解释允许）。"""
        project, arts = await _seed_project_with_script(db_session, versions=2)
        v1 = arts[0]
        ctx = await AgentContextService(
            settings=Settings(app_env="test")
        ).build_explanation_context(
            db_session,
            project,
            "这场为什么突然翻脸",
            active_context=_active(v1),
        )
        assert ctx is not None and ctx.status == "ok"
        # 历史版本 v1 被读取（解释锚定用户所看版本，不偷偷换最新）
        assert ctx.target.artifact_id == str(v1.id)
        assert ctx.target.version == 1

    async def test_scene_filter_narrows_to_single_scene(
        self, db_session: AsyncSession
    ) -> None:
        """active.scene_number → 只组装该场原文。"""
        project, arts = await _seed_project_with_script(db_session)
        ctx = await AgentContextService(
            settings=Settings(app_env="test")
        ).build_explanation_context(
            db_session,
            project,
            "这场为什么突然翻脸",
            active_context=_active(arts[0], scene_number=2),
        )
        assert ctx is not None and ctx.status == "ok"
        script_sources = [s for s in ctx.sources if s.kind == "script_scene"]
        assert len(script_sources) == 1
        assert script_sources[0].scene_number == 2

    async def test_over_budget_script_without_scene_narrows(
        self, db_session: AsyncSession
    ) -> None:
        """正文超预算且未选定单场 → narrow（不截断后假装读完）。"""
        project, arts = await _seed_project_with_script(db_session)
        # 放大正文体积超过测试配置的预算（默认 12000 字符）
        big = dict(_script_content())
        for scene in big["scenes"]:
            scene["action"] = (scene.get("action") or "") + "很长的动作描写。" * 3000
        svc = ArtifactService()
        big_art = await svc.create_validated_artifact(
            db_session,
            project_id=project.id,
            artifact_type=ArtifactType.SCRIPT_DRAFT,
            episode_number=4,
            content=big,
        )
        await db_session.commit()

        service = AgentContextService(
            settings=Settings(app_env="test", agent_context_budget_tokens=1000)
        )
        ctx = await service.build_explanation_context(
            db_session, project, "第4集讲了什么", active_context=None
        )
        assert ctx is not None and ctx.status == "narrow"
        assert ctx.sources == []
        del arts, big_art

    async def test_title_only_script_is_no_text(
        self, db_session: AsyncSession
    ) -> None:
        """只有标题没有场景 → no_text（不能从标题解释剧情）。"""
        from app.db.models.artifact import Artifact as ArtifactModel

        project = Project(title="空剧本项目", target_episode_count=10)
        db_session.add(project)
        await db_session.flush()
        # 空 scenes 过不了 schema 校验——no_text 防的是存量/异常行，
        # 直插 ORM 模拟（status=valid 但 content 无场景）
        db_session.add(
            ArtifactModel(
                project_id=project.id,
                type="script_draft",
                episode_number=1,
                version=1,
                status="valid",
                content={"title": "只有标题"},
            )
        )
        await db_session.commit()

        ctx = await AgentContextService(
            settings=Settings(app_env="test")
        ).build_explanation_context(
            db_session, project, "第1集讲了什么", active_context=None
        )
        assert ctx is not None and ctx.status == "no_text"

    async def test_no_target_returns_none(self, db_session: AsyncSession) -> None:
        """无明确对象/集数且无活动上下文 → None（回落项目级答复）。"""
        project, _ = await _seed_project_with_script(db_session)
        ctx = await AgentContextService(
            settings=Settings(app_env="test")
        ).build_explanation_context(
            db_session, project, "项目写得怎么样了", active_context=None
        )
        assert ctx is None

    async def test_invalid_scene_number_rejected(self, db_session: AsyncSession) -> None:
        """scene_number 不存在于该版本 → 活动上下文非法。"""
        project, arts = await _seed_project_with_script(db_session)
        service = AgentContextService(settings=Settings(app_env="test"))
        with pytest.raises(InvalidActiveContextError):
            await service.validate_active_context(
                db_session,
                project,
                _active(arts[0], scene_number=99),
            )

    async def test_explicit_outline_target(self, db_session: AsyncSession) -> None:
        """明确"大纲" → 大纲为解释目标。"""
        project, _ = await _seed_project_with_script(db_session)
        svc = ArtifactService()
        outline = await svc.create_validated_artifact(
            db_session,
            project_id=project.id,
            artifact_type=ArtifactType.EPISODE_OUTLINE_SET,
            content=_load("outline_set_valid"),
        )
        await db_session.commit()
        ctx = await AgentContextService(
            settings=Settings(app_env="test")
        ).build_explanation_context(
            db_session, project, "大纲里第3集要讲什么", active_context=None
        )
        assert ctx is not None and ctx.status == "ok"
        assert ctx.target.artifact_id == str(outline.id)
        assert ctx.sources[0].kind == "outline"


def _active(artifact: Any, scene_number: int | None = None) -> Any:
    from app.domain.agent_command import ActiveArtifactContext

    return ActiveArtifactContext(
        artifact_id=artifact.id,
        artifact_type="script_draft",
        episode_number=artifact.episode_number,
        version=artifact.version,
        checksum=artifact.checksum,
        scene_number=scene_number,
    )


# ========================================================================
# Turn 全链路：解释作答、引文回填、重放幂等
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
class TestExplainTurn:
    async def test_explain_turn_answers_with_verified_citations(
        self, agent_api: AsyncClient, db_session: AsyncSession, planner_llm: FakeLLM
    ) -> None:
        """内容性问题 → 读第 3 集原文作答，引文可溯源且回填 Artifact 引用。"""
        project, arts = await _seed_project_with_script(db_session)
        scene_quote = "……五年。从十四岁到现在。"
        planner_llm.register(
            "artifact_explainer",
            ArtifactExplanationOutput(
                answer="这场是林峰被淘汰后深夜独自加练、遇到老者的转折。",
                citations=[
                    ExplanationCitationOutput(
                        source_index=2, scene_number=2, quote=scene_quote
                    )
                ],
            ),
        )
        resp = await _turn(
            agent_api, str(project.id), "第3集这场翻脸为什么这么突然", "explain-1"
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["turn_type"] == "answer"

        # 消息含引文与解释元数据
        messages = await agent_api.get(
            f"/api/v1/conversations/{body['conversation_id']}/messages"
        )
        items = (
            messages.json().get("items") or [] if messages.status_code == 200 else []
        )
        answer_msg = next(
            (m for m in items if m.get("metadata", {}).get("explanation_citations")),
            None,
        )
        assert answer_msg is not None, "解释消息应带 explanation_citations"
        citations = answer_msg["metadata"]["explanation_citations"]
        assert citations[0]["artifact_id"] == str(arts[0].id)
        assert citations[0]["version"] == arts[0].version
        assert citations[0]["scene_number"] == 2
        assert citations[0]["quote"] == scene_quote
        assert citations[0]["checksum"] == arts[0].checksum
        # 引文行附在答复中（读者可直接核对）
        assert scene_quote in answer_msg["content"]

    async def test_chinese_numeral_episode_routes_end_to_end(
        self, agent_api: AsyncClient, db_session: AsyncSession, planner_llm: FakeLLM
    ) -> None:
        """中文集数（"第三集"）端到端：读第 3 集原文作答。"""
        project, arts = await _seed_project_with_script(db_session)
        planner_llm.register(
            "artifact_explainer",
            ArtifactExplanationOutput(
                answer="……",
                citations=[
                    ExplanationCitationOutput(
                        source_index=1, scene_number=1, quote="职业足球很残酷"
                    )
                ],
            ),
        )
        resp = await _turn(agent_api, str(project.id), "第三集这场讲了什么", "cn-ep-1")
        assert resp.status_code == 200, resp.text
        conversation_id = resp.json()["conversation_id"]
        messages = await agent_api.get(
            f"/api/v1/conversations/{conversation_id}/messages"
        )
        items = messages.json().get("items") or []
        final = [m for m in items if m["role"] == "assistant"][-1]
        citations = (final.get("metadata") or {}).get("explanation_citations") or []
        assert citations, "中文集数解释应产出引文"
        assert citations[0]["artifact_id"] == str(arts[0].id)

    async def test_citations_anchor_to_version_at_explanation_time(
        self, agent_api: AsyncClient, db_session: AsyncSession, planner_llm: FakeLLM
    ) -> None:
        """解释后生成新稿：已存引文仍锚定解释时的 v1（引用不漂移）。"""
        project, arts = await _seed_project_with_script(db_session)
        planner_llm.register(
            "artifact_explainer",
            ArtifactExplanationOutput(
                answer="……",
                citations=[
                    ExplanationCitationOutput(
                        source_index=2, scene_number=2, quote="……五年。从十四岁到现在。"
                    )
                ],
            ),
        )
        resp = await _turn(
            agent_api, str(project.id), "第3集这场讲了什么", "anchor-1"
        )
        assert resp.status_code == 200
        conversation_id = resp.json()["conversation_id"]

        # 解释之后产生 v2（同集新版本）
        await ArtifactService().create_validated_artifact(
            db_session,
            project_id=project.id,
            artifact_type=ArtifactType.SCRIPT_DRAFT,
            episode_number=3,
            content=_script_content("第3集-v2"),
        )
        await db_session.commit()

        messages = await agent_api.get(
            f"/api/v1/conversations/{conversation_id}/messages"
        )
        items = messages.json().get("items") or []
        final = [m for m in items if m["role"] == "assistant"][-1]
        citations = (final.get("metadata") or {}).get("explanation_citations") or []
        v1 = arts[0]
        assert citations[0]["artifact_id"] == str(v1.id)
        assert citations[0]["version"] == v1.version
        assert citations[0]["checksum"] == v1.checksum

    async def test_same_turn_replay_returns_same_answer(
        self, agent_api: AsyncClient, db_session: AsyncSession, planner_llm: FakeLLM
    ) -> None:
        """同幂等键重放 → 返回原响应，不二次调用模型。"""
        project, _ = await _seed_project_with_script(db_session)
        planner_llm.register(
            "artifact_explainer",
            ArtifactExplanationOutput(
                answer="依据原文的回答。",
                citations=[
                    ExplanationCitationOutput(
                        source_index=2, scene_number=2, quote="……五年。从十四岁到现在。"
                    )
                ],
            ),
        )
        body = {
            "content": "第3集这场为什么突然翻脸",
            "idempotency_key": "replay-explain",
        }
        first = await agent_api.post(
            f"/api/v1/projects/{project.id}/agent/turns", json=body
        )
        assert first.status_code == 200
        calls_after_first = planner_llm._attempt_count
        second = await agent_api.post(
            f"/api/v1/projects/{project.id}/agent/turns", json=body
        )
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["response_message_id"] == first.json()["response_message_id"]
        # 幂等命中在 Planner 之前返回——重放不发生新的模型调用
        assert planner_llm._attempt_count == calls_after_first

    async def test_forged_citations_fall_back_to_limited_answer(
        self, agent_api: AsyncClient, db_session: AsyncSession, planner_llm: FakeLLM
    ) -> None:
        """全部引文无法溯源 → 有限答复（原文不足以确认），不带引文元数据。"""
        project, _ = await _seed_project_with_script(db_session)
        planner_llm.register(
            "artifact_explainer",
            ArtifactExplanationOutput(
                answer="凭空编造的剧情解释。",
                citations=[
                    ExplanationCitationOutput(
                        source_index=2, scene_number=2, quote="这句引文根本不存在于原文"
                    )
                ],
            ),
        )
        resp = await _turn(
            agent_api, str(project.id), "第3集女主为什么离开", "forged-1"
        )
        assert resp.status_code == 200
        conversation_id = resp.json()["conversation_id"]
        messages = await agent_api.get(
            f"/api/v1/conversations/{conversation_id}/messages"
        )
        items = messages.json().get("items") or []
        final = [m for m in items if m["role"] == "assistant"][-1]
        assert "原文中" in final["content"] or "无法" in final["content"]
        assert "explanation_citations" not in (final.get("metadata") or {})

    async def test_scene_number_enters_request_hash(
        self, agent_api: AsyncClient, db_session: AsyncSession, planner_llm: FakeLLM
    ) -> None:
        """同幂等键不同 scene_number → 409 IDEMPOTENCY_KEY_REUSED。"""
        project, arts = await _seed_project_with_script(db_session)
        planner_llm.register(
            "artifact_explainer",
            ArtifactExplanationOutput(
                answer="……",
                citations=[],
            ),
        )
        base = {
            "content": "这场为什么突然翻脸",
            "idempotency_key": "hash-scene",
            "active_context": {
                "artifact_id": str(arts[0].id),
                "artifact_type": "script_draft",
                "episode_number": 3,
                "scene_number": 1,
            },
        }
        first = await agent_api.post(
            f"/api/v1/projects/{project.id}/agent/turns", json=base
        )
        assert first.status_code in (200, 202), first.text
        second = dict(base)
        second["active_context"] = {**base["active_context"], "scene_number": 2}
        resp = await agent_api.post(
            f"/api/v1/projects/{project.id}/agent/turns", json=second
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "IDEMPOTENCY_KEY_REUSED"

    async def test_nonexistent_scene_number_rejected_422(
        self, agent_api: AsyncClient, db_session: AsyncSession
    ) -> None:
        """scene_number 不在该版本 → 请求在事务 A 即被拒绝。"""
        project, arts = await _seed_project_with_script(db_session)
        resp = await _turn(
            agent_api,
            str(project.id),
            "这场为什么突然翻脸",
            "bad-scene",
            active_context={
                "artifact_id": str(arts[0].id),
                "artifact_type": "script_draft",
                "episode_number": 3,
                "scene_number": 99,
            },
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "INVALID_ACTIVE_CONTEXT"
