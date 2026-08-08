"""F-04 Diff API 与版本查询集成测试。

对照验收：
① 中文文本 Diff 不乱码
② 可识别新增/删除/修改场景（diff 正常）
③ A/B 颠倒时方向正确
④ 跨项目查询拒绝
⑤ 限制超大 diff 的响应体 / change_ratio 供 Revision Gate 消费
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.application.artifact_service import ArtifactService

_GOLDEN = Path(__file__).resolve().parents[2] / "golden"


def _load_golden(name: str) -> dict[str, Any]:
    with open(_GOLDEN / name, encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


def _script_a_content() -> dict[str, Any]:
    """原稿 content（ep1，2 场）。"""
    return _load_golden("script_draft_valid.json")


def _script_b_content() -> dict[str, Any]:
    """修订稿 content（F-02 golden 的 script_draft，同集同场数）。"""
    return _load_golden("revised_episode_football.json")["script_draft"]  # type: ignore[no-any-return]


@pytest.mark.integration
class TestArtifactVersions:
    """版本历史与 Diff 端点。"""

    async def _create_project(
        self, client: AsyncClient, title: str = "Diff 测试"
    ) -> uuid.UUID:
        r = await client.post("/api/v1/projects", json={"title": title})
        assert r.status_code in (200, 201), r.text
        return uuid.UUID(r.json()["id"])

    async def _create_script(
        self,
        project_id: uuid.UUID,
        content: dict[str, Any],
        *,
        episode_number: int = 1,
        artifact_type: str = "script_draft",
    ) -> uuid.UUID:
        import app.db.session as db_session

        assert db_session._async_session_factory is not None, "DB not initialized"
        async with db_session._async_session_factory() as db:
            svc = ArtifactService()
            resp = await svc.create_validated_artifact(
                db,
                project_id=project_id,
                artifact_type=artifact_type,
                episode_number=episode_number,
                content=content,
            )
            await db.commit()
            return resp.id

    async def _diff(self, client: AsyncClient, from_id: uuid.UUID, to_id: uuid.UUID) -> Any:
        r = await client.get(
            f"/api/v1/artifacts/diff?from_artifact_id={from_id}&to_artifact_id={to_id}"
        )
        return r

    @staticmethod
    def _big_draft(episode: int, n_dialogue: int, suffix: str) -> dict[str, Any]:
        """构造大量对白的剧本 content（用于超大 diff 截断测试）。"""
        return {
            "episode_number": episode,
            "title": "超大 diff 测试",
            "opening_hook": "开场钩子",
            "scenes": [
                {
                    "scene_number": 1,
                    "location": "场景一",
                    "time_of_day": "日",
                    "characters": ["甲"],
                    "action": "第一场动作描写。",
                    "dialogue": [
                        {
                            "speaker": "甲",
                            "text": f"第{i}句对白{suffix}",
                            "parenthetical": None,
                        }
                        for i in range(n_dialogue)
                    ],
                },
                {
                    "scene_number": 2,
                    "location": "场景二",
                    "time_of_day": "夜",
                    "characters": ["乙"],
                    "action": "第二场动作描写。",
                    "dialogue": [],
                },
            ],
            "ending_hook": "结尾钩子",
            "plain_text": f"第{episode}集正文",
            "word_count": 0,
            "dialogue_ratio": 0.0,
            "referenced_outline_artifact_id": "00000000-0000-0000-0000-000000000001",
        }

    # ---- 版本列表 ----

    async def test_version_list_ascending_and_immutable(self, async_client: AsyncClient) -> None:
        """版本历史升序返回，旧 content 不被覆盖。"""
        project_id = await self._create_project(async_client)
        v1_id = await self._create_script(project_id, _script_a_content())
        v2_id = await self._create_script(project_id, _script_b_content())

        r = await async_client.get(f"/api/v1/artifacts/{v1_id}/versions")
        assert r.status_code == 200, r.text
        versions = r.json()
        assert [v["version"] for v in versions] == [1, 2]
        assert versions[0]["id"] == str(v1_id)
        assert versions[1]["id"] == str(v2_id)
        # 同一 (project, type, episode) 下的版本
        assert versions[0]["episode_number"] == versions[1]["episode_number"] == 1
        # 旧版本 content 未被覆盖：对白数不同
        n1 = len(versions[0]["content"]["scenes"][0]["dialogue"])
        n2 = len(versions[1]["content"]["scenes"][0]["dialogue"])
        assert n1 != n2

    # ---- Diff 正常路径 ----

    async def test_diff_normal(self, async_client: AsyncClient) -> None:
        """两版本 diff：mode=scene、统计存在、中文不乱码。"""
        project_id = await self._create_project(async_client)
        v1 = await self._create_script(project_id, _script_a_content())
        v2 = await self._create_script(project_id, _script_b_content())

        r = await self._diff(async_client, v1, v2)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "scene"
        assert 0.0 <= body["change_ratio"] <= 1.0
        assert body["stats"]["from_chars"] > 0
        assert body["scene_summary"]["modified"] >= 1
        # Artifact 元数据回填
        assert body["from_artifact_id"] == str(v1)
        assert body["to_artifact_id"] == str(v2)
        assert body["from_version"] == 1
        assert body["to_version"] == 2
        assert body["project_id"] == str(project_id)
        assert body["episode_number"] == 1
        # ① 中文不乱码：响应原始文本无 \uXXXX 转义，中文原样
        assert "青训营更衣室" in r.text
        assert "\\u" not in r.text

    async def test_diff_direction_symmetric(self, async_client: AsyncClient) -> None:
        """③ A/B 颠倒时 change_ratio 不变、added/removed 互换。"""
        project_id = await self._create_project(async_client)
        v1 = await self._create_script(project_id, _script_a_content())
        v2 = await self._create_script(project_id, _script_b_content())

        ab = (await self._diff(async_client, v1, v2)).json()
        ba = (await self._diff(async_client, v2, v1)).json()
        assert ab["change_ratio"] == ba["change_ratio"]
        assert ab["stats"]["added_lines"] == ba["stats"]["removed_lines"]
        assert ab["stats"]["removed_lines"] == ba["stats"]["added_lines"]

    # ---- 拒绝路径 ----

    async def test_cross_project_rejected(self, async_client: AsyncClient) -> None:
        """④ 跨项目查询拒绝。"""
        p1 = await self._create_project(async_client, "项目一")
        p2 = await self._create_project(async_client, "项目二")
        a = await self._create_script(p1, _script_a_content())
        b = await self._create_script(p2, _script_b_content())

        r = await self._diff(async_client, a, b)
        assert r.status_code == 400
        assert r.json()["code"] == "CROSS_PROJECT_DIFF_FORBIDDEN"

    async def test_unsupported_type_rejected(self, async_client: AsyncClient) -> None:
        """非 script_draft 类型拒绝。"""
        project_id = await self._create_project(async_client)
        script_id = await self._create_script(project_id, _script_a_content())
        # 同项目建一个 story_bible（内容无需合法，类型即触发校验）
        bible_id = await self._create_script(
            project_id, {"invalid": "content"}, artifact_type="story_bible"
        )

        r = await self._diff(async_client, script_id, bible_id)
        assert r.status_code == 400
        assert r.json()["code"] == "DIFF_UNSUPPORTED_TYPE"

    async def test_episode_mismatch_rejected(self, async_client: AsyncClient) -> None:
        """from/to 不同集拒绝。"""
        project_id = await self._create_project(async_client)
        ep1_id = await self._create_script(project_id, _script_a_content(), episode_number=1)
        ep2_content = dict(_script_a_content())
        ep2_content["episode_number"] = 2
        ep2_id = await self._create_script(project_id, ep2_content, episode_number=2)

        r = await self._diff(async_client, ep1_id, ep2_id)
        assert r.status_code == 400
        assert r.json()["code"] == "DIFF_EPISODE_MISMATCH"

    async def test_artifact_not_found(self, async_client: AsyncClient) -> None:
        """不存在的 Artifact → 404。"""
        project_id = await self._create_project(async_client)
        v1 = await self._create_script(project_id, _script_a_content())

        r = await self._diff(async_client, v1, uuid.uuid4())
        assert r.status_code == 404
        assert r.json()["code"] == "ARTIFACT_NOT_FOUND"

    # ---- 回退与截断 ----

    async def test_invalid_content_falls_back_to_line_diff(self, async_client: AsyncClient) -> None:
        """content 无法解析为 ScriptDraft 时回退全文行 diff（mode=line）。"""
        project_id = await self._create_project(async_client)
        valid_id = await self._create_script(project_id, _script_a_content())
        # 缺 scenes 的 script_draft → status=invalid
        invalid_id = await self._create_script(project_id, {"invalid": "content"})

        r = await self._diff(async_client, valid_id, invalid_id)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "line"
        assert 0.0 <= body["change_ratio"] <= 1.0
        assert body["scene_summary"]["from_scene_count"] == 0

    async def test_oversized_diff_truncated(self, async_client: AsyncClient) -> None:
        """⑤ 超大 diff 限制响应体：truncated=True 且行明细清空。"""
        project_id = await self._create_project(async_client)
        v_suffix_a = await self._create_script(project_id, self._big_draft(1, 2100, "甲"))
        v_suffix_b = await self._create_script(project_id, self._big_draft(1, 2100, "乙"))

        r = await self._diff(async_client, v_suffix_a, v_suffix_b)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["truncated"] is True
        assert body["stats"]["modified_lines"] > 2000
        # 行明细全部截断，但统计与摘要保留
        assert all(sc["line_changes"] == [] for sc in body["scene_changes"])
        assert all(sc["line_changes_truncated"] for sc in body["scene_changes"])
        assert body["stats"]["from_chars"] > 0
        assert 0.0 <= body["change_ratio"] <= 1.0
