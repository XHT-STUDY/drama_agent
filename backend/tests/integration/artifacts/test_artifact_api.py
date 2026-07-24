"""B-04 Artifact API 集成测试。

验证：
- 首版本为 1
- 新版本不覆盖旧 content
- 非法 Schema → status="invalid"，不成为 latest valid
- source_artifact_ids 可查询
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.integration
@pytest.mark.asyncio
class TestArtifactCreation:
    """Artifact 创建与版本管理。"""

    async def _create_project(self, client: AsyncClient) -> str:
        r = await client.post("/api/v1/projects", json={"title": "Artifact 测试"})
        return r.json()["id"]

    async def test_first_version_is_1(self, async_client: AsyncClient) -> None:
        """首版本为 1。"""
        project_id = await self._create_project(async_client)

        # 直接通过 store 创建
        from app.artifacts.store import ArtifactStore
        from app.db.session import _async_session_factory

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
                "protagonist_seed": "被遗弃的天才",
                "conflict_seed": "逆袭之路的阻碍",
                "source_type": "idea",
                "characters": [],
                "world_building": "",
                "opening_hook": "",
                "story_engine": "",
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
