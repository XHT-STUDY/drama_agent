"""Export API 集成测试 (G-06)。

覆盖（对应 G-06 验收）：
- POST /projects/{id}/exports → 202 + run_id（action=export），Worker 完成后
  export_file Artifact 落库且 status=valid;
- GET /exports/{artifact_id}/download?project_id=... 返回文件字节 +
  安全 Content-Disposition（中文经 filename* 编码）;
- 下载文件名安全且可读（不含路径分隔符 / 控制字符，ASCII 兜底）;
- 不能下载其他项目文件（跨项目 403 CROSS_PROJECT_ACCESS）;
- 文件丢失（Artifact 不存在 / 非 export_file / 存储文件被清理）
  → 404 EXPORT_FILE_MISSING;
- Export Artifact source links 完整（StoryBible + 3 集剧本）。

导出是确定性操作（不调 LLM）；Worker 在 FakeLLM 环境启动但不消费。
导出文件写入默认 ./var/exports（已 gitignore），Worker 与下载端点同目录。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.storage.local import LocalFileStore


@pytest.mark.integration
@pytest.mark.asyncio
class TestExportAPI:
    """导出 POST + 下载端点纵切。"""

    async def _create_project(self, async_client: AsyncClient, title: str) -> str:
        resp = await async_client.post("/api/v1/projects", json={"title": title})
        assert resp.status_code == 201, resp.text
        return str(resp.json()["id"])

    async def _seed_content(self, app: Any, project_id: str) -> None:
        """在项目中播种 StoryBible + 3 集剧本（提交，Worker 独立会话可见）。"""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.application.artifact_service import ArtifactService
        from app.domain.enums import ArtifactType
        from tests.integration.export.test_export_service import (
            _story_bible,
            _valid_script,
        )

        engine = app.state._test_engine
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        svc = ArtifactService()
        async with factory() as db:
            await svc.create_validated_artifact(
                db,
                project_id=uuid.UUID(project_id),
                artifact_type=ArtifactType.STORY_BIBLE,
                content=_story_bible(),
            )
            for ep in (1, 2, 3):
                await svc.create_validated_artifact(
                    db,
                    project_id=uuid.UUID(project_id),
                    artifact_type=ArtifactType.SCRIPT_DRAFT,
                    episode_number=ep,
                    content=_valid_script(ep, f"第{ep}集"),
                )
            await db.commit()

    async def _start_export(
        self, async_client: AsyncClient, project_id: str, payload: dict[str, Any]
    ) -> str:
        resp = await async_client.post(
            f"/api/v1/projects/{project_id}/exports", json=payload
        )
        assert resp.status_code == 202, resp.text
        return str(resp.json()["run_id"])

    async def _wait_run(
        self, async_client: AsyncClient, run_id: str, max_tries: int = 60
    ) -> str:
        status = "queued"
        for _ in range(max_tries):
            await asyncio.sleep(0.2)
            resp = await async_client.get(f"/api/v1/runs/{run_id}")
            status = resp.json()["status"]
            if status in ("completed", "failed"):
                break
        return status

    async def _export_artifact(
        self,
        async_client: AsyncClient,
        project_id: str,
        *,
        kinds: list[str],
        fmt: str = "markdown",
    ) -> dict[str, Any]:
        run_id = await self._start_export(
            async_client, project_id, {"kinds": kinds, "format": fmt}
        )
        status = await self._wait_run(async_client, run_id)
        assert status == "completed", f"导出 Run 未完成: {status}"
        arts = await async_client.get(
            f"/api/v1/projects/{project_id}/artifacts?type=export_file"
        )
        items = arts.json()["items"]
        assert items, "导出完成后应存在 export_file Artifact"
        assert isinstance(items[0], dict)
        return items[0]

    # ---- 正常导出 + 下载 ----

    async def test_export_download_markdown_flow(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """导出 Markdown → 下载 200，字节可读，Content-Disposition 安全。"""
        project_id = await self._create_project(async_client, "足球少年逆袭记")
        await self._seed_content(app, project_id)

        art = await self._export_artifact(
            async_client, project_id, kinds=["story_bible", "script"]
        )
        assert art["type"] == "export_file"
        assert art["status"] == "valid"

        dl = await async_client.get(
            f"/api/v1/exports/{art['id']}/download",
            params={"project_id": project_id},
        )
        assert dl.status_code == 200, dl.text
        assert dl.headers["content-type"].startswith("text/markdown")

        cd = dl.headers.get("content-disposition", "")
        assert "attachment" in cd, cd
        assert "filename*=" in cd, "中文文件名应走 RFC 5987 filename*"
        assert "\\" not in cd and "\n" not in cd, "文件名不得含路径分隔符/控制字符"

        # 内容可读且包含导出内容（中文不乱码）
        text = dl.content.decode("utf-8")
        assert "世界观与人物设定" in text
        assert "第 1 集" in text

    async def test_export_download_docx_flow(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """导出 DOCX → 下载 content-type 为 docx，字节为 zip（可被 python-docx 打开）。"""
        project_id = await self._create_project(async_client, "DOCX 导出")
        await self._seed_content(app, project_id)

        art = await self._export_artifact(
            async_client, project_id, kinds=["story_bible"], fmt="docx"
        )
        dl = await async_client.get(
            f"/api/v1/exports/{art['id']}/download",
            params={"project_id": project_id},
        )
        assert dl.status_code == 200
        assert "wordprocessingml" in dl.headers["content-type"]
        assert dl.content[:2] == b"PK", "DOCX 应为 zip 容器"

        from io import BytesIO

        from docx import Document

        doc = Document(BytesIO(dl.content))
        texts = "".join(p.text for p in doc.paragraphs)
        assert "世界观与人物设定" in texts

    async def test_export_source_links_complete(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """Export Artifact source links 完整：StoryBible + 3 集剧本。"""
        project_id = await self._create_project(async_client, "源链接")
        await self._seed_content(app, project_id)

        art = await self._export_artifact(
            async_client, project_id, kinds=["story_bible", "script"]
        )
        links = await async_client.get(f"/api/v1/artifacts/{art['id']}/links")
        assert links.status_code == 200
        body = links.json()
        assert len(body) == 4, "StoryBible + 3 集剧本 = 4 条源链接"
        assert all(link["relation"] == "derived_from" for link in body)

    # ---- 跨项目 403 ----

    async def test_cross_project_download_rejected(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """不能下载其他项目的导出文件（403 CROSS_PROJECT_ACCESS）。"""
        project_a = await self._create_project(async_client, "项目 A")
        project_b = await self._create_project(async_client, "项目 B")
        await self._seed_content(app, project_a)

        art = await self._export_artifact(
            async_client, project_a, kinds=["story_bible"]
        )
        resp = await async_client.get(
            f"/api/v1/exports/{art['id']}/download",
            params={"project_id": project_b},
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "CROSS_PROJECT_ACCESS"

    # ---- 文件丢失 404 EXPORT_FILE_MISSING ----

    async def test_download_nonexistent_artifact(
        self, async_client: AsyncClient
    ) -> None:
        """Artifact 不存在 → 404 EXPORT_FILE_MISSING。"""
        fake_id = str(uuid.uuid4())
        resp = await async_client.get(
            f"/api/v1/exports/{fake_id}/download",
            params={"project_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "EXPORT_FILE_MISSING"

    async def test_download_non_export_artifact(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """Artifact 存在但不是 export_file → 404 EXPORT_FILE_MISSING。"""
        project_id = await self._create_project(async_client, "非导出")
        await self._seed_content(app, project_id)

        arts = await async_client.get(
            f"/api/v1/projects/{project_id}/artifacts?type=story_bible"
        )
        sb_id = arts.json()["items"][0]["id"]
        resp = await async_client.get(
            f"/api/v1/exports/{sb_id}/download",
            params={"project_id": project_id},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "EXPORT_FILE_MISSING"

    async def test_download_lost_storage_file(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """导出文件在 FileStore 中已丢失（被清理）→ 404 EXPORT_FILE_MISSING。"""
        project_id = await self._create_project(async_client, "文件丢失")
        await self._seed_content(app, project_id)

        art = await self._export_artifact(
            async_client, project_id, kinds=["story_bible"]
        )
        storage_key = art["content"]["storage_key"]
        store = LocalFileStore(root=app.state.settings.export_file_root)
        await store.delete(storage_key)

        resp = await async_client.get(
            f"/api/v1/exports/{art['id']}/download",
            params={"project_id": project_id},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "EXPORT_FILE_MISSING"

    # ---- POST 校验 ----

    async def test_create_export_project_not_found(
        self, async_client: AsyncClient
    ) -> None:
        """项目不存在 → 404 PROJECT_NOT_FOUND。"""
        resp = await async_client.post(
            f"/api/v1/projects/{uuid.uuid4()}/exports",
            json={"kinds": ["story_bible"]},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "PROJECT_NOT_FOUND"

    async def test_create_export_invalid_kind(self, async_client: AsyncClient) -> None:
        """非法 kind → 422（Pydantic Literal 校验）。"""
        project_id = await self._create_project(async_client, "非法 kind")
        resp = await async_client.post(
            f"/api/v1/projects/{project_id}/exports",
            json={"kinds": ["not_a_kind"]},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "VALIDATION_ERROR"

    async def test_create_export_empty_kinds(self, async_client: AsyncClient) -> None:
        """kinds 为空列表 → 422。"""
        project_id = await self._create_project(async_client, "空 kinds")
        resp = await async_client.post(
            f"/api/v1/projects/{project_id}/exports",
            json={"kinds": []},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "VALIDATION_ERROR"


async def _create_project_any(async_client: AsyncClient, title: str) -> str:
    resp = await async_client.post("/api/v1/projects", json={"title": title})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _wait_run_any(async_client: AsyncClient, run_id: str, max_tries: int = 60) -> str:
    status = "queued"
    for _ in range(max_tries):
        await asyncio.sleep(0.2)
        resp = await async_client.get(f"/api/v1/runs/{run_id}")
        status = resp.json()["status"]
        if status in ("completed", "failed"):
            break
    return status


@pytest.mark.integration
@pytest.mark.asyncio
class TestExportFrozenSelectionW105:
    """W1-05：导出选择在接受时冻结为显式 Artifact ID，历史重下字节一致。"""

    _create_project = staticmethod(_create_project_any)
    _wait_run = staticmethod(_wait_run_any)

    async def _seed_one_script(
        self, app: Any, project_id: str, title: str
    ) -> str:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.application.artifact_service import ArtifactService
        from app.domain.enums import ArtifactType
        from tests.integration.export.test_export_service import _valid_script

        engine = app.state._test_engine
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        svc = ArtifactService()
        async with factory() as db:
            art = await svc.create_validated_artifact(
                db,
                project_id=uuid.UUID(project_id),
                artifact_type=ArtifactType.SCRIPT_DRAFT,
                episode_number=1,
                content=_valid_script(1, title),
            )
            await db.commit()
            return str(art.id)

    async def test_legacy_request_freezes_latest_and_redownload_bytes_stable(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """旧客户端省略 IDs：接受时解析 latest 冻结进 config；改稿后历史
        重下字节一致（SHA256 相同），新导出才用新版本。"""
        project_id = await self._create_project(async_client, "冻结选择测试")
        v1_id = await self._seed_one_script(app, project_id, "旧版第一集")

        # 发起导出（省略 artifact_ids）
        resp = await async_client.post(
            f"/api/v1/projects/{project_id}/exports",
            json={"kinds": ["script"], "format": "markdown"},
        )
        assert resp.status_code == 202, resp.text
        run = resp.json()
        # 接受即冻结：config.options.artifact_ids 是显式 v1 ID
        frozen = run["config_snapshot"]["options"]["artifact_ids"]
        assert frozen == {"script": [v1_id]}
        status = await self._wait_run(async_client, run["run_id"])
        assert status == "completed"

        run_resp = await async_client.get(f"/api/v1/runs/{run['run_id']}")
        export_artifact_id = run_resp.json()["result_artifact_ids"][0]

        dl1 = await async_client.get(
            f"/api/v1/exports/{export_artifact_id}/download",
            params={"project_id": project_id},
        )
        assert dl1.status_code == 200

        # 改稿：第 1 集出现 v2
        await self._seed_one_script(app, project_id, "新版第一集")

        # 历史重下：字节与首次一致（不含 v2 内容）
        dl2 = await async_client.get(
            f"/api/v1/exports/{export_artifact_id}/download",
            params={"project_id": project_id},
        )
        assert dl2.content == dl1.content
        assert "旧版第一集" in dl1.content.decode("utf-8")
        assert "新版第一集" not in dl2.content.decode("utf-8")

        # 服务端历史列出交付记录
        history = await async_client.get(f"/api/v1/projects/{project_id}/exports")
        assert history.status_code == 200
        ids = [item["id"] for item in history.json()["items"]]
        assert export_artifact_id in ids

        # 新导出解析新 latest（v2）——冻结的是"发起时刻"
        resp2 = await async_client.post(
            f"/api/v1/projects/{project_id}/exports",
            json={"kinds": ["script"], "format": "markdown"},
        )
        frozen2 = resp2.json()["config_snapshot"]["options"]["artifact_ids"]
        assert frozen2["script"] != [v1_id]

    async def test_explicit_selection_rejections(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """显式选择的排队前 422：缺项/空数组/跨项目/同集多版本。"""
        project_id = await self._create_project(async_client, "显式选择校验")
        v1 = await self._seed_one_script(app, project_id, "第一集")

        other_project = await self._create_project(async_client, "别的项目")
        other_v1 = await self._seed_one_script(app, other_project, "别家第一集")

        cases: list[dict[str, Any]] = [
            # 缺 story_bible 项（选了 kinds 却没给 IDs）
            {"kinds": ["script", "story_bible"], "artifact_ids": {"script": [v1]}},
            # 空数组
            {"kinds": ["script"], "artifact_ids": {"script": []}},
            # 跨项目 Artifact
            {"kinds": ["script"], "artifact_ids": {"script": [other_v1]}},
            # 同集两个版本
            {"kinds": ["script"], "artifact_ids": {"script": [v1, v1]}},
        ]
        for payload in cases:
            resp = await async_client.post(
                f"/api/v1/projects/{project_id}/exports", json=payload
            )
            assert resp.status_code == 422, payload
            assert resp.json()["code"] == "EXPORT_SELECTION_INVALID", payload

    async def test_mismatched_evaluation_dropped_with_warning(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """评估报告绑定的剧本不在所选集合 → 默认剔除并记 selection_warnings。"""
        import json as _json
        from pathlib import Path

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.application.artifact_service import ArtifactService
        from app.domain.enums import ArtifactType
        from tests.integration.export.test_export_service import _valid_script

        golden = _json.loads(
            (Path(__file__).resolve().parents[2] / "golden" / "evaluation_report_valid.json")
            .read_text(encoding="utf-8")
        )

        project_id = await self._create_project(async_client, "错配评估")
        engine = app.state._test_engine
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        svc = ArtifactService()
        async with factory() as db:
            script_v1 = await svc.create_validated_artifact(
                db, project_id=uuid.UUID(project_id),
                artifact_type=ArtifactType.SCRIPT_DRAFT, episode_number=1,
                content=_valid_script(1, "第一集"),
            )
            # 评估绑定的是 v1
            eval_v1 = await svc.create_validated_artifact(
                db, project_id=uuid.UUID(project_id),
                artifact_type=ArtifactType.EVALUATION_REPORT, episode_number=1,
                content={**golden, "script_artifact_id": str(script_v1.id)},
            )
            # 改稿出 v2（评估不绑定 v2）
            script_v2 = await svc.create_validated_artifact(
                db, project_id=uuid.UUID(project_id),
                artifact_type=ArtifactType.SCRIPT_DRAFT, episode_number=1,
                content=_valid_script(1, "第一集修订"),
            )
            await db.commit()

        # 显式选 v2 剧本 + v1 时代的评估
        resp = await async_client.post(
            f"/api/v1/projects/{project_id}/exports",
            json={
                "kinds": ["script", "evaluation"],
                "format": "markdown",
                "artifact_ids": {
                    "script": [str(script_v2.id)],
                    "evaluation": [str(eval_v1.id)],
                },
            },
        )
        assert resp.status_code == 202, resp.text
        options = resp.json()["config_snapshot"]["options"]
        assert options["artifact_ids"]["evaluation"] == []
        assert any("评估报告" in w for w in options.get("selection_warnings", []))
