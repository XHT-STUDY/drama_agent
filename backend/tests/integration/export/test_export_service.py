"""ExportService 集成测试 (G-05)。

覆盖组装 → 序列化 → 落盘 → 持久化的完整服务链路：
- 各 kind 组装 latest valid，ExportFile Artifact 为 valid；
- 源 Artifact 链接完整（source_artifact_ids + artifact_links）；
- 幂等：同选择 + 同源重复导出不产生新版本；
- 显式版本选择（G-05 验收项）;
- 任一步失败不生成 valid ExportFile（验收项）;
- 跨项目 Artifact 拒绝；
- 文件可被 FileStore 重新打开，内容与 sha256 一致。

FileStore 注入 tmp_path，避免污染默认导出目录。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

import pytest
import pytest_asyncio

from app.application.artifact_service import ArtifactService
from app.application.export_service import ExportError, ExportService
from app.artifacts.store import ArtifactStore
from app.domain.enums import ArtifactType
from app.domain.export import ExportSelection
from app.storage.local import LocalFileStore
from app.storage.protocol import FileStore

# 固定导出时间（确定性测试）
_FIXED_NOW = datetime(2026, 8, 16, 10, 0, 0, tzinfo=UTC)


def _valid_script(episode_number: int, title: str) -> dict:
    """构造通过 ScriptDraft 校验的剧本 content。"""
    return {
        "episode_number": episode_number,
        "title": title,
        "opening_hook": "开局钩子",
        "ending_hook": "结尾钩子",
        "scenes": [
            {
                "scene_number": 1,
                "location": "训练场",
                "time_of_day": "日",
                "characters": ["林峰"],
                "action": "林峰独自加练",
                "dialogue": [{"speaker": "林峰", "text": "我不会放弃"}],
            },
            {
                "scene_number": 2,
                "location": "宿舍",
                "time_of_day": "夜",
                "characters": ["林峰"],
                "action": "林峰整理心情",
                "dialogue": [],
            },
        ],
        "plain_text": "林峰独自加练，随后回宿舍整理心情。",
        "word_count": 18,
        "dialogue_ratio": 0.2,
        "referenced_outline_artifact_id": str(uuid.uuid4()),
    }


def _story_bible() -> dict:
    """构造通过 StoryBible 校验的 content（复用单元测试样例）。"""
    from tests.unit.export.test_markdown import _story_bible as _make

    return _make()


@pytest_asyncio.fixture
async def seeded_project(
    db_session: Any,
    test_project: uuid.UUID,
    artifact_service: ArtifactService,
) -> uuid.UUID:
    """在项目中播种 StoryBible + 3 集剧本，返回 project_id。"""
    await artifact_service.create_validated_artifact(
        db_session,
        project_id=test_project,
        artifact_type=ArtifactType.STORY_BIBLE,
        content=_story_bible(),
    )
    for ep in (1, 2, 3):
        await artifact_service.create_validated_artifact(
            db_session,
            project_id=test_project,
            artifact_type=ArtifactType.SCRIPT_DRAFT,
            episode_number=ep,
            content=_valid_script(ep, f"第{ep}集"),
        )
    return test_project


def _service(tmp_path: Any) -> tuple[ExportService, FileStore]:
    store: FileStore = LocalFileStore(root=str(tmp_path / "exports"))
    service = ExportService(file_store=store)
    return service, store


async def _export(
    service: ExportService,
    db: Any,
    project_id: uuid.UUID,
    *,
    kinds: list[str],
    fmt: str = "markdown",
    artifact_ids: dict[str, list[str]] | None = None,
) -> Any:
    return await service.export_project(
        db,
        project_id=project_id,
        selection=ExportSelection(kinds=kinds, format=fmt, artifact_ids=artifact_ids),  # type: ignore[arg-type]
        now=_FIXED_NOW,
    )


@pytest.mark.integration
@pytest.mark.asyncio
class TestExportService:
    """ExportService 完整链路。"""

    async def test_markdown_export_creates_valid_artifact(
        self,
        db_session: Any,
        seeded_project: uuid.UUID,
        tmp_path: Any,
    ) -> None:
        """导出 StoryBible → valid ExportFile，文件可重读且内容一致。"""
        service, store = _service(tmp_path)
        artifact = await _export(service, db_session, seeded_project, kinds=["story_bible"])

        assert artifact.type == ArtifactType.EXPORT_FILE
        assert artifact.status == "valid"
        content = artifact.content
        assert content["format"] == "markdown"
        assert content["filename"].endswith(".md")
        assert content["size_bytes"] > 0

        data = await store.open(content["storage_key"])
        assert len(data) == content["size_bytes"]
        assert hashlib_sha256(data) == content["sha256"], "sha256 应与文件内容一致"
        text = data.decode("utf-8")
        assert "世界观与人物设定" in text
        assert "足球少年逆袭记" in text

    async def test_docx_export_creates_openable_file(
        self,
        db_session: Any,
        seeded_project: uuid.UUID,
        tmp_path: Any,
    ) -> None:
        """DOCX 导出：文件可被 python-docx 重开，中文保留。"""
        from docx import Document

        service, store = _service(tmp_path)
        artifact = await _export(
            service, db_session, seeded_project, kinds=["story_bible", "script"], fmt="docx"
        )
        assert artifact.content["format"] == "docx"
        assert artifact.content["filename"].endswith(".docx")

        data = await store.open(artifact.content["storage_key"])
        doc = Document(BytesIO(data))
        texts = "".join(p.text for p in doc.paragraphs)
        assert "世界观与人物设定" in texts
        assert "第 1 集剧本" in texts

    async def test_source_links_complete(
        self,
        db_session: Any,
        seeded_project: uuid.UUID,
        artifact_service: ArtifactService,
        tmp_path: Any,
    ) -> None:
        """Export Artifact source links 完整：3 集剧本全部被链接。"""
        service, store = _service(tmp_path)
        artifact = await _export(service, db_session, seeded_project, kinds=["script"])

        sources = artifact.content["source_artifact_ids"]
        assert len(sources) == 3, "source_artifact_ids 应覆盖 3 集剧本"

        # Artifact 表的 source links（G-06 验收项）同样完整
        links = await artifact_service.get_source_links(db_session, artifact.id)
        assert len(links) == 3
        assert all(link["relation"] == "derived_from" for link in links)

    async def test_latest_valid_only(
        self,
        db_session: Any,
        seeded_project: uuid.UUID,
        artifact_service: ArtifactService,
        tmp_path: Any,
    ) -> None:
        """只导出各集 latest valid（第 1 集写入 v2 后应导出 v2）。"""
        # 第 1 集新增一个更高版本
        await artifact_service.create_validated_artifact(
            db_session,
            project_id=seeded_project,
            artifact_type=ArtifactType.SCRIPT_DRAFT,
            episode_number=1,
            content=_valid_script(1, "第1集（修订）"),
        )
        service, store = _service(tmp_path)
        artifact = await _export(service, db_session, seeded_project, kinds=["script"])

        sources = artifact.content["source_artifact_ids"]
        assert len(sources) == 3, "仍应只有 3 集（每集一个版本）"
        # 第 1 集最新 valid 的 id 必须出现在源中
        store_a = ArtifactStore()
        ep1_latest = await store_a.get_latest(
            db_session, seeded_project, "script_draft", 1
        )
        ep1_sources = [s for s in sources if s["artifact_id"] == str(ep1_latest.id)]
        assert len(ep1_sources) == 1
        assert ep1_sources[0]["version"] == 2, "应导出第 1 集最新版本（v2）"

    async def test_idempotent_same_selection_no_duplicate(
        self,
        db_session: Any,
        seeded_project: uuid.UUID,
        tmp_path: Any,
    ) -> None:
        """同选择 + 同源重复导出：幂等复用，不产生新版本。"""
        service, store = _service(tmp_path)
        first = await _export(
            service, db_session, seeded_project, kinds=["story_bible", "script"]
        )
        second = await _export(
            service, db_session, seeded_project, kinds=["story_bible", "script"]
        )

        assert first.id == second.id, "重复导出应幂等复用同一 ExportFile"
        assert first.version == 1
        # 幂等后只有第一个文件保留（孤儿文件已被清理）
        assert await store.exists(first.content["storage_key"])

    async def test_explicit_version_selection(
        self,
        db_session: Any,
        seeded_project: uuid.UUID,
        artifact_service: ArtifactService,
        tmp_path: Any,
    ) -> None:
        """显式选择版本：导出指定 Artifact ID。"""
        service, store = _service(tmp_path)
        # 创建一个旧版 StoryBible
        old = await artifact_service.create_validated_artifact(
            db_session,
            project_id=seeded_project,
            artifact_type=ArtifactType.STORY_BIBLE,
            content=_story_bible(),
        )
        artifact = await _export(
            service,
            db_session,
            seeded_project,
            kinds=["story_bible"],
            artifact_ids={"story_bible": [str(old.id)]},
        )
        assert artifact.content["source_artifact_ids"][0]["artifact_id"] == str(old.id)

    async def test_explicit_wrong_type_rejected(
        self,
        db_session: Any,
        seeded_project: uuid.UUID,
        artifact_service: ArtifactService,
        tmp_path: Any,
    ) -> None:
        """显式选择类型不匹配（script 位置给 story_bible）→ 拒绝。"""
        service, store = _service(tmp_path)
        sb = await artifact_service.create_validated_artifact(
            db_session,
            project_id=seeded_project,
            artifact_type=ArtifactType.STORY_BIBLE,
            content=_story_bible(),
        )
        with pytest.raises(ExportError):
            await _export(
                service,
                db_session,
                seeded_project,
                kinds=["script"],
                artifact_ids={"script": [str(sb.id)]},
            )

    async def test_cross_project_artifact_rejected(
        self,
        db_session: Any,
        test_project: uuid.UUID,
        artifact_service: ArtifactService,
        tmp_path: Any,
    ) -> None:
        """别的项目的 Artifact 对本项目不可见 → 拒绝导出。"""
        from app.db.models.project import Project

        other = uuid.uuid4()
        db_session.add(Project(id=other, title="别家项目", status="draft"))
        await db_session.flush()
        sb = await artifact_service.create_validated_artifact(
            db_session,
            project_id=other,
            artifact_type=ArtifactType.STORY_BIBLE,
            content=_story_bible(),
        )
        service, store = _service(tmp_path)
        with pytest.raises(ExportError):
            await _export(
                service,
                db_session,
                test_project,
                kinds=["story_bible"],
                artifact_ids={"story_bible": [str(sb.id)]},
            )

    async def test_failure_no_valid_export_file(
        self,
        db_session: Any,
        test_project: uuid.UUID,
        tmp_path: Any,
    ) -> None:
        """任一步失败 → 抛异常，不生成 valid ExportFile（验收项）。

        指定不存在的 Artifact ID → get_version 抛 NotFoundError；
        断言导出未留下任何 ExportFile Artifact。
        """
        from app.core.errors import NotFoundError

        service, store = _service(tmp_path)
        with pytest.raises((ExportError, NotFoundError)):
            await _export(
                service,
                db_session,
                test_project,
                kinds=["story_bible"],
                artifact_ids={"story_bible": [str(uuid.uuid4())]},
            )
        # 项目下无任何 export_file Artifact
        store_a = ArtifactStore()
        items = await store_a.list_by_project(
            db_session, test_project, ArtifactType.EXPORT_FILE, offset=0, limit=10
        )
        assert items == []

    async def test_missing_kind_generates_warning_but_valid(
        self,
        db_session: Any,
        test_project: uuid.UUID,
        tmp_path: Any,
    ) -> None:
        """kind 无有效内容：不失败，warnings 记录 + 内容为占位。"""
        service, store = _service(tmp_path)
        artifact = await _export(
            service, db_session, test_project, kinds=["story_bible", "evaluation"]
        )
        assert artifact.status == "valid"
        warnings = artifact.content["warnings"]
        assert any("story_bible 无可用有效内容" in w for w in warnings)
        assert any("evaluation 无可用有效内容" in w for w in warnings)


def hashlib_sha256(data: bytes) -> str:
    """计算字节的 SHA256（测试断言用）。"""
    import hashlib

    return hashlib.sha256(data).hexdigest()
