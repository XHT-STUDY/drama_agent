"""B-04 Artifact API 集成测试。

验证：
- 首版本为 1
- 新版本不覆盖旧 content
- 非法 Schema → status="invalid"，不成为 latest valid
- source_artifact_ids 可查询
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.asyncio
class TestArtifactCreation:
    """Artifact 创建与版本管理。"""

    async def _create_project(self, client: AsyncClient) -> uuid.UUID:
        r = await client.post("/api/v1/projects", json={"title": "Artifact 测试"})
        return uuid.UUID(r.json()["id"])

    async def test_first_version_is_1(self, async_client: AsyncClient) -> None:
        """首版本为 1。"""
        project_id = await self._create_project(async_client)

        # 直接通过 store 创建
        from app.artifacts.store import ArtifactStore
        from app.db.session import _async_session_factory

        assert _async_session_factory is not None, "DB not initialized"
        async with _async_session_factory() as db:
            store = ArtifactStore()
            a = await store.create(
                db,
                project_id=project_id,
                artifact_type="script_draft",
                content={
                    "title": "测试剧本",
                    "scenes": [{"id": "s1", "title": "开场", "dialogues": []}],
                },
            )
            assert a.version == 1

    async def test_second_version_does_not_overwrite_first(self, async_client: AsyncClient) -> None:
        """新版本不覆盖旧 content。"""
        project_id = await self._create_project(async_client)

        from app.artifacts.store import ArtifactStore
        from app.db.session import _async_session_factory

        assert _async_session_factory is not None, "DB not initialized"
        async with _async_session_factory() as db:
            store = ArtifactStore()
            a1 = await store.create(
                db,
                project_id=project_id,
                artifact_type="script_draft",
                content={"title": "版本1"},
            )
            a2 = await store.create(
                db,
                project_id=project_id,
                artifact_type="script_draft",
                content={"title": "版本2"},
            )

            assert a1.version == 1
            assert a2.version == 2
            assert a1.id != a2.id  # 不同记录
            assert a1.content["title"] == "版本1"  # 旧版本不变
            assert a2.content["title"] == "版本2"

    async def test_invalid_schema_saved_as_invalid(self, async_client: AsyncClient) -> None:
        """非法 Schema 保存为 status='invalid'。"""
        project_id = await self._create_project(async_client)

        from app.application.artifact_service import ArtifactService
        from app.db.session import _async_session_factory

        assert _async_session_factory is not None, "DB not initialized"
        async with _async_session_factory() as db:
            svc = ArtifactService()
            # ScriptDraft 需要 scenes 字段，传空 dict 会校验失败
            result = await svc.create_validated_artifact(
                db,
                project_id=project_id,
                artifact_type="script_draft",
                content={"invalid": "content"},  # 不符合 ScriptDraft schema
            )
            assert result.status == "invalid"

    async def test_latest_only_returns_valid(self, async_client: AsyncClient) -> None:
        """invalid 版本不成为 latest valid。"""
        project_id = await self._create_project(async_client)

        from app.application.artifact_service import ArtifactService
        from app.db.session import _async_session_factory

        assert _async_session_factory is not None, "DB not initialized"
        async with _async_session_factory() as db:
            svc = ArtifactService()
            # 先创建 invalid
            await svc.create_validated_artifact(
                db,
                project_id=project_id,
                artifact_type="story_bible",
                content={"invalid": True},
            )

            # 再创建 valid（story_bible schema 的合法内容）
            valid_content = {
                "title": "足球少年",
                "logline": "一个被青训队抛弃的足球少年逆袭",
                "genre": "都市/逆袭",
                "tone": ["热血"],
                "world_setting": "中国都市足球青训圈",
                "protagonist": {
                    "character_id": "char_protagonist",
                    "name": "林峰",
                    "role": "主角",
                    "visible_goal": "成为职业足球运动员",
                },
                "antagonist": {
                    "character_id": "char_antagonist",
                    "name": "陈教练",
                    "role": "反派",
                    "visible_goal": "维护自己的权威",
                },
                "main_conflict": "天赋被埋没后重新证明自己",
                "stakes": "失去足球生涯和家人的信任",
            }
            await svc.create_validated_artifact(
                db,
                project_id=project_id,
                artifact_type="story_bible",
                content=valid_content,
            )

            # get_latest 应返回 valid 版本
            latest = await svc.get_latest(db, project_id, "story_bible")
            assert latest.status == "valid"

    async def test_source_artifact_ids_queryable(self, async_client: AsyncClient) -> None:
        """source_artifact_ids 可查询。"""
        project_id = await self._create_project(async_client)

        from app.artifacts.store import ArtifactStore
        from app.db.session import _async_session_factory

        assert _async_session_factory is not None, "DB not initialized"
        async with _async_session_factory() as db:
            store = ArtifactStore()

            source_id = str(
                (
                    await store.create(
                        db,
                        project_id=project_id,
                        artifact_type="story_bible",
                        content={
                            "title": "来源",
                            "logline": "test",
                            "genre": "test",
                            "tone": [],
                            "protagonist_seed": "test",
                            "conflict_seed": "test",
                            "source_type": "idea",
                            "characters": [],
                            "world_building": "",
                            "opening_hook": "",
                            "story_engine": "",
                        },
                    )
                ).id
            )

            derived = await store.create(
                db,
                project_id=project_id,
                artifact_type="episode_outline_set",
                content={
                    "title": "派生",
                    "episodes": [{"episode_number": 1, "title": "E1", "summary": "test", "hook": "hook"}],
                },
                source_artifact_ids=[
                    {"artifact_id": source_id, "version": 1, "relation": "derived_from"}
                ],
            )

            # 查询 links
            links = await store.get_source_links(db, derived.id)
            assert len(links) >= 1
            assert any(str(link.target_id) == source_id for link in links)


@pytest.mark.integration
@pytest.mark.asyncio
class TestArtifactReferences:
    """反向引用查询（J-08）：大纲修订后判断哪些剧本仍引用旧大纲。"""

    async def _seed_outline_with_scripts(
        self, client: AsyncClient
    ) -> tuple[uuid.UUID, list[str]]:
        """播种 SB + 大纲 + 2 集剧本（derived_from 大纲），返回 (大纲 ID, 剧本 ID 列表)。"""
        from app.artifacts.store import ArtifactStore
        from app.db.session import _async_session_factory

        project_id = uuid.UUID(
            (await client.post("/api/v1/projects", json={"title": "引用查询"})).json()["id"]
        )
        assert _async_session_factory is not None, "DB not initialized"
        async with _async_session_factory() as db:
            store = ArtifactStore()
            outline = await store.create(
                db,
                project_id=project_id,
                artifact_type="episode_outline_set",
                status="valid",
                content={
                    "episodes": [
                        {
                            "episode_number": 1,
                            "title": "E1",
                            "opening_hook": "h",
                            "objective": "o",
                            "core_conflict": "c",
                            "key_events": ["a", "b"],
                            "payoff": "p",
                            "ending_hook": "e",
                        }
                    ],
                    "arc_summary": "arc",
                },
            )
            script_ids: list[str] = []
            for ep in (1, 2):
                script = await store.create(
                    db,
                    project_id=project_id,
                    artifact_type="script_draft",
                    episode_number=ep,
                    status="valid",
                    content={"title": f"S{ep}", "scenes": []},
                    source_artifact_ids=[
                        {
                            "artifact_id": str(outline.id),
                            "version": outline.version,
                            "relation": "derived_from",
                        }
                    ],
                )
                script_ids.append(str(script.id))
            await db.commit()
            return outline.id, script_ids

    async def test_references_returns_scripts_derived_from_outline(
        self, async_client: AsyncClient
    ) -> None:
        """GET /artifacts/{id}/references?type=script_draft → 仍引用该大纲的剧本。"""
        outline_id, script_ids = await self._seed_outline_with_scripts(async_client)

        resp = await async_client.get(
            f"/api/v1/artifacts/{outline_id}/references",
            params={"type": "script_draft", "relation": "derived_from"},
        )
        assert resp.status_code == 200
        body = resp.json()
        returned_ids = {item["id"] for item in body}
        assert returned_ids == set(script_ids)
        assert all(item["type"] == "script_draft" for item in body)

    async def test_references_unknown_artifact_returns_404(
        self, async_client: AsyncClient
    ) -> None:
        resp = await async_client.get(f"/api/v1/artifacts/{uuid.uuid4()}/references")
        assert resp.status_code == 404
