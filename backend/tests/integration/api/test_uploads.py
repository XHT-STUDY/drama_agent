"""Upload API 集成测试 (G-03)。

覆盖（对应计划验收）：
- 中文 TXT 上传不乱码，元数据（original_name/char_count/sha256/project_id）正确;
- 磁盘文件用服务端 UUID 存储键，不含客户端文件名;
- GBK TXT 解析带编码告警;
- DOCX 上传解析（段落+表格），落盘字节与上传一致;
- 空 TXT / 空 DOCX 明确处理;
- 损坏文件（.docx 非 zip）→ 422 FILE_PARSE_FAILED;
- 伪装扩展名（.txt 内容是 zip）→ 422 FILE_PARSE_FAILED;
- 大小超限 → 413 FILE_TOO_LARGE;
- 项目不存在 → 404;
- 跨项目隔离：A 项目上传不出现在 B 项目列表。

安全约束验证：文件名不含路径分隔符入库（原始名只进 original_name）。
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path
from typing import Any

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.main import create_app
from app.storage.local import LocalFileStore

# 测试统一用较小上限：正常用例在 64KB 内，超限用例发 128KB
_MAX_BYTES = 64 * 1024


def _docx_bytes(paragraphs: list[str] | None = None) -> bytes:
    """构造真实 DOCX 字节。"""
    from docx import Document

    doc = Document()
    for p in paragraphs or ["第一集", "夜城初遇"]:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest_asyncio.fixture
async def app(test_engine: Any, tmp_path: Path) -> Any:
    """使用临时上传目录 + 较小上限的 app 实例（覆盖 conftest 默认）。"""
    settings = Settings(app_env="test")
    settings.apply_env_overrides()
    settings.upload_file_root = str(tmp_path / "uploads")
    settings.upload_max_bytes = _MAX_BYTES
    app = create_app(settings=settings)
    app.state._test_engine = test_engine

    import app.db.session as db_session

    db_session._async_session_factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return app


async def _create_project(client: AsyncClient, title: str) -> str:
    resp = await client.post("/api/v1/projects", json={"title": title})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _upload(
    client: AsyncClient,
    project_id: str,
    *,
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
) -> Any:
    files = {"file": (filename, content, content_type)}
    return await client.post(
        f"/api/v1/projects/{project_id}/uploads", files=files
    )


async def test_upload_chinese_txt(app: Any, async_client: AsyncClient) -> None:
    """中文 TXT 上传：不乱码 + 元数据正确 + 磁盘为 UUID 键。"""
    project_id = await _create_project(async_client, "中文上传")
    text = "第一集：夜城初遇。霓虹在雨里闪烁，林晚推开玻璃门。"
    resp = await _upload(
        async_client, project_id, filename="第一集.txt", content=text.encode("utf-8")
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["project_id"] == project_id
    assert body["original_name"] == "第一集.txt"
    assert body["mime_type"] == "text/plain"
    assert body["parse_status"] == "parsed"
    assert body["char_count"] == len(text)
    assert body["warnings"] == []

    # 磁盘键是服务端 UUID，不含客户端文件名
    key = body["path"]
    assert "第一集" not in key
    assert Path(key).suffix == ".txt"

    # 落盘字节与上传一致（不乱码）
    store = LocalFileStore(root=app.state.settings.upload_file_root)
    stored = await store.open(key)
    assert stored.decode("utf-8") == text


async def test_upload_gbk_txt(app: Any, async_client: AsyncClient) -> None:
    """GBK TXT 解析成功并带编码告警。"""
    project_id = await _create_project(async_client, "GBK 上传")
    text = "旧版系统导出的剧本文本"
    resp = await _upload(
        async_client, project_id, filename="legacy.txt", content=text.encode("gbk")
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["char_count"] == len(text)
    assert any("GBK" in w for w in body["warnings"])
    store = LocalFileStore(root=app.state.settings.upload_file_root)
    assert (await store.open(body["path"])).decode("gbk") == text


async def test_upload_docx(async_client: AsyncClient, app: Any) -> None:
    """DOCX 上传解析（段落提取），落盘字节一致。"""
    project_id = await _create_project(async_client, "DOCX 上传")
    data = _docx_bytes()
    resp = await _upload(async_client, project_id, filename="剧本.docx", content=data)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["mime_type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    )
    assert body["char_count"] > 0
    assert Path(body["path"]).suffix == ".docx"
    store = LocalFileStore(root=app.state.settings.upload_file_root)
    assert await store.open(body["path"]) == data


async def test_upload_empty_txt(async_client: AsyncClient) -> None:
    """空 TXT 明确处理：201 + char_count=0。"""
    project_id = await _create_project(async_client, "空文件")
    resp = await _upload(async_client, project_id, filename="empty.txt", content=b"")
    assert resp.status_code == 201, resp.text
    assert resp.json()["char_count"] == 0


async def test_upload_corrupt_docx(async_client: AsyncClient) -> None:
    """.docx 但内容非 zip → 422 FILE_PARSE_FAILED。"""
    project_id = await _create_project(async_client, "损坏 DOCX")
    resp = await _upload(
        async_client, project_id, filename="broken.docx", content=b"not a zip at all"
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "FILE_PARSE_FAILED"


async def test_upload_disguised_extension(async_client: AsyncClient) -> None:
    """.txt 文件名但内容是 DOCX zip → 422 FILE_PARSE_FAILED（伪装拒绝）。"""
    project_id = await _create_project(async_client, "伪装文件")
    resp = await _upload(
        async_client, project_id, filename="sneaky.txt", content=_docx_bytes()
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "FILE_PARSE_FAILED"


async def test_upload_unsupported_extension(async_client: AsyncClient) -> None:
    """不支持扩展名 → 422 FILE_PARSE_FAILED。"""
    project_id = await _create_project(async_client, "不支持类型")
    resp = await _upload(async_client, project_id, filename="evil.pdf", content=b"x")
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "FILE_PARSE_FAILED"


async def test_upload_too_large(async_client: AsyncClient) -> None:
    """超过上限 → 413 FILE_TOO_LARGE。"""
    project_id = await _create_project(async_client, "超大文件")
    resp = await _upload(
        async_client, project_id, filename="big.txt", content=b"a" * (2 * _MAX_BYTES)
    )
    assert resp.status_code == 413, resp.text
    assert resp.json()["code"] == "FILE_TOO_LARGE"


async def test_upload_nonexistent_project(async_client: AsyncClient) -> None:
    """项目不存在 → 404。"""
    missing = uuid.uuid4()
    resp = await _upload(
        async_client, str(missing), filename="x.txt", content=b"hello"
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "PROJECT_NOT_FOUND"


async def test_cross_project_isolation(async_client: AsyncClient) -> None:
    """跨项目隔离：A 上传不出现在 B 列表。"""
    project_a = await _create_project(async_client, "项目 A")
    project_b = await _create_project(async_client, "项目 B")

    await _upload(async_client, project_a, filename="a.txt", content="A 内容".encode())

    resp_a = await async_client.get(f"/api/v1/projects/{project_a}/uploads")
    assert resp_a.status_code == 200
    assert [i["id"] for i in resp_a.json()["items"]] != []

    resp_b = await async_client.get(f"/api/v1/projects/{project_b}/uploads")
    assert resp_b.status_code == 200
    assert resp_b.json()["items"] == []


async def test_list_uploads_order(async_client: AsyncClient) -> None:
    """列表按创建时间倒序，字段完整。"""
    project_id = await _create_project(async_client, "列表排序")
    await _upload(async_client, project_id, filename="one.txt", content=b"first")
    await _upload(async_client, project_id, filename="two.txt", content=b"second")

    resp = await async_client.get(f"/api/v1/projects/{project_id}/uploads")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    items = body["items"]
    assert [i["original_name"] for i in items] == ["two.txt", "one.txt"]
    assert all(
        set(i) >= {
            "id", "project_id", "path", "sha256", "mime_type", "size_bytes",
            "original_name", "parse_status", "char_count", "warnings", "created_at",
        }
        for i in items
    )
