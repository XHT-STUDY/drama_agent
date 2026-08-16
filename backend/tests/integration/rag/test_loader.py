"""D-02 Loader / 摄取集成测试。

覆盖：
- 语料目录扫描（跳过 README / VERSION / rubric）；
- Markdown 与 JSON 两种格式加载；
- document hash 确定性与变更检测；
- 幂等摄取：跳过 / 更新（只重建变化 chunk）/ 新建；
- 源文件删除不物理删除线上记录；
- chunk 保留来源、分类、标题路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.db.models.knowledge_document import KnowledgeDocument
from app.db.repositories.knowledge import KnowledgeRepository
from app.rag.chunker import chunk_document
from app.rag.loader import (
    compute_document_hash,
    discover_knowledge_files,
    load_knowledge_corpus,
    load_knowledge_file,
)
from app.rag.models import KnowledgeMetadataError, knowledge_root


def _write_doc(
    tmp_path: Path,
    name: str,
    *,
    title: str = "测试文档",
    category: str = "payoff",
    body: str = "正文内容。",
    tags: list[str] | None = None,
    genre: str = "都市",
) -> Path:
    """写一篇带 frontmatter 的知识文档并返回路径。"""
    meta = {
        "category": category,
        "title": title,
        "source": "drama-agent-self-auth",
        "license": "MIT",
        "language": "zh",
        "genre": genre,
        "stage": "writer",
        "tags": tags or ["测试"],
        "version": "1.0.0",
    }
    fm = yaml.dump(meta, allow_unicode=True, sort_keys=False)
    path = tmp_path / name
    path.write_text(f"---\n{fm}---\n{body}\n", encoding="utf-8")
    return path


@pytest.mark.integration
class TestDiscover:
    """语料目录扫描。"""

    def test_real_corpus_discovers_docs(self) -> None:
        """真实语料目录能发现全部知识文档，且跳过特殊资产。"""
        files = discover_knowledge_files(knowledge_root())
        assert len(files) >= 18
        names = {f.name for f in files}
        assert "README.md" not in names, "不应发现 README.md"
        assert "VERSION" not in names, "不应发现 VERSION"
        for path in files:
            assert "rubric" not in path.parts, f"不应发现 rubric 特殊资产: {path}"

    def test_nonexistent_root_raises(self) -> None:
        """目录不存在时报错。"""
        with pytest.raises(FileNotFoundError):
            discover_knowledge_files(Path("/no/such/knowledge/dir"))


@pytest.mark.integration
class TestLoadFile:
    """单文件加载。"""

    def test_load_real_corpus_doc(self) -> None:
        """真实语料文档可加载，元数据完整、正文非空、hash 64 位。"""
        docs = load_knowledge_corpus(knowledge_root())
        assert len(docs) >= 18
        for doc in docs:
            assert doc.metadata.title
            assert doc.metadata.source == "drama-agent-self-auth"
            assert doc.metadata.license == "MIT"
            assert doc.content.strip()
            assert len(doc.document_hash) == 64

    def test_load_markdown_with_frontmatter(self, tmp_path: Path) -> None:
        """Markdown frontmatter 加载。"""
        path = _write_doc(tmp_path, "a.md", title="标题A", category="payoff", body="## 小节\n内容。")
        doc = load_knowledge_file(path)
        assert doc.metadata.title == "标题A"
        assert doc.metadata.category.value == "payoff"
        assert "## 小节" in doc.content
        assert doc.content.strip()

    def test_load_json_format(self, tmp_path: Path) -> None:
        """JSON 格式加载。"""
        path = tmp_path / "b.json"
        path.write_text(
            '{"metadata": {"category": "compliance", "title": "JSON文档", '
            '"source": "drama-agent-self-auth", "license": "MIT"}, "content": "JSON 正文。"}',
            encoding="utf-8",
        )
        doc = load_knowledge_file(path)
        assert doc.metadata.category.value == "compliance"
        assert doc.metadata.title == "JSON文档"
        assert doc.content == "JSON 正文。"

    def test_missing_frontmatter_raises(self, tmp_path: Path) -> None:
        """无 frontmatter 的文档加载失败。"""
        path = tmp_path / "bad.md"
        path.write_text("没有 frontmatter 的正文", encoding="utf-8")
        with pytest.raises(KnowledgeMetadataError):
            load_knowledge_file(path)


@pytest.mark.integration
class TestDocumentHash:
    """document hash 确定性与变更检测。"""

    def test_deterministic(self, tmp_path: Path) -> None:
        """相同文件 hash 稳定。"""
        path = _write_doc(tmp_path, "a.md", body="稳定正文。")
        h1 = load_knowledge_file(path).document_hash
        h2 = load_knowledge_file(path).document_hash
        assert h1 == h2

    def test_body_change_changes_hash(self, tmp_path: Path) -> None:
        """正文变化 hash 变化。"""
        path = _write_doc(tmp_path, "a.md", body="版本一。")
        h1 = load_knowledge_file(path).document_hash
        path.write_text(path.read_text(encoding="utf-8").replace("版本一", "版本二"), encoding="utf-8")
        assert load_knowledge_file(path).document_hash != h1

    def test_metadata_change_changes_hash(self, tmp_path: Path) -> None:
        """元数据变化 hash 变化。"""
        path = _write_doc(tmp_path, "a.md", genre="都市")
        h1 = load_knowledge_file(path).document_hash
        raw = path.read_text(encoding="utf-8").replace("都市", "战神")
        path.write_text(raw, encoding="utf-8")
        assert load_knowledge_file(path).document_hash != h1

    def test_compute_hash_stable_shape(self, tmp_path: Path) -> None:
        """compute_document_hash 与加载结果一致。"""
        path = _write_doc(tmp_path, "a.md", body="正文。")
        doc = load_knowledge_file(path)
        assert compute_document_hash(doc.metadata, doc.content) == doc.document_hash


@pytest.mark.integration
class TestIngest:
    """Repository 幂等摄取。"""

    async def test_create_then_skip(self, test_session, tmp_path: Path) -> None:
        """首次摄取创建，重复摄取跳过（chunk 不重复）。"""
        repo = KnowledgeRepository(test_session)
        path = _write_doc(tmp_path, "a.md", body="## 主题\n内容。")
        loaded = load_knowledge_file(path)
        chunks = chunk_document(loaded.content)

        doc, created, changed = await repo.ingest_document(loaded, chunks, corpus_version="mvp_v1")
        assert created and changed
        assert doc.category == "payoff"
        assert doc.corpus_version == "mvp_v1"
        assert len(doc.document_hash) == 64
        chunk_count_after_create = await repo.count_chunks()

        doc2, created2, changed2 = await repo.ingest_document(loaded, chunks, corpus_version="mvp_v1")
        assert not created2 and not changed2
        assert doc2.id == doc.id
        assert await repo.count_chunks() == chunk_count_after_create

    async def test_update_rebuilds_only_changed_chunks(
        self, test_session, tmp_path: Path
    ) -> None:
        """文档更新时只重建变化的 chunk，未变化 chunk 保留原行。"""
        repo = KnowledgeRepository(test_session)
        path = _write_doc(tmp_path, "a.md", body="# 甲\nAAA内容。\n# 乙\nBBB内容。")
        loaded = load_knowledge_file(path)
        doc, _created, _changed = await repo.ingest_document(
            loaded, chunk_document(loaded.content), corpus_version="mvp_v1"
        )

        chunks_before = await repo.list_chunks_by_document(doc.id)
        assert len(chunks_before) == 2
        id_by_head = {}
        for c in chunks_before:
            head = (c.chunk_metadata or {}).get("heading_path")
            id_by_head[tuple(head)] = c.id
        id_a = id_by_head[("甲",)]
        id_b = id_by_head[("乙",)]

        # 修改乙节内容，甲节不动
        raw = path.read_text(encoding="utf-8")
        path.write_text(raw.replace("BBB内容。", "BBB内容，已修改。"), encoding="utf-8")
        loaded2 = load_knowledge_file(path)
        doc2, created2, changed2 = await repo.ingest_document(
            loaded2, chunk_document(loaded2.content), corpus_version="mvp_v1"
        )
        assert not created2 and changed2
        assert doc2.id == doc.id

        chunks_after = await repo.list_chunks_by_document(doc.id)
        assert len(chunks_after) == 2
        id_by_head_after = {}
        for c in chunks_after:
            head = (c.chunk_metadata or {}).get("heading_path")
            id_by_head_after[tuple(head)] = (c.id, c.content)
        # 甲节 chunk 复用原行
        assert id_by_head_after[("甲",)][0] == id_a
        # 乙节 chunk 已重建
        assert "已修改" in id_by_head_after[("乙",)][1]
        assert id_by_head_after[("乙",)][0] != id_b

    async def test_source_file_removal_keeps_records(
        self, test_session, tmp_path: Path
    ) -> None:
        """删除源文件不会静默物理删除线上记录。"""
        repo = KnowledgeRepository(test_session)
        path = _write_doc(tmp_path, "a.md", body="内容。")
        loaded = load_knowledge_file(path)
        await repo.ingest_document(loaded, chunk_document(loaded.content), corpus_version="mvp_v1")

        # 源文件消失后重新扫描摄取
        path.unlink()
        files = discover_knowledge_files(tmp_path)
        assert files == []
        assert await repo.count_documents() == 1  # 线上记录仍在
        assert await repo.count_chunks() >= 1

    async def test_chunk_metadata_retains_source_path(self, test_session, tmp_path: Path) -> None:
        """chunk 保留来源、分类、标题路径。"""
        repo = KnowledgeRepository(test_session)
        path = _write_doc(tmp_path, "a.md", title="战神模板", body="# 主线\n结构。")
        loaded = load_knowledge_file(path)
        doc, _c, _ch = await repo.ingest_document(
            loaded, chunk_document(loaded.content), corpus_version="mvp_v1"
        )
        chunk = (await repo.list_chunks_by_document(doc.id))[0]
        meta = chunk.chunk_metadata or {}
        assert meta["heading_path"] == ["主线"]
        assert len(meta["chunk_hash"]) == 64

    async def test_document_row_has_full_metadata(self, test_session, tmp_path: Path) -> None:
        """文档行写入完整元数据（含 source/genre/stage/tags）。"""
        repo = KnowledgeRepository(test_session)
        path = _write_doc(tmp_path, "a.md", title="存档", genre="战神", tags=["爽", "逆袭"])
        loaded = load_knowledge_file(path)
        doc, _c, _ch = await repo.ingest_document(
            loaded, chunk_document(loaded.content), corpus_version="mvp_v1"
        )
        assert doc.title == "存档"
        assert doc.source == "drama-agent-self-auth"
        assert doc.genre == "战神"
        assert doc.stage == "writer"
        assert doc.tags == ["爽", "逆袭"]
        assert doc.corpus_version == "mvp_v1"

    async def test_documents_and_chunks_persisted(self, test_session, tmp_path: Path) -> None:
        """摄取后文档与 chunk 均落库（独立查询可见）。"""
        repo = KnowledgeRepository(test_session)
        loaded = load_knowledge_file(_write_doc(tmp_path, "x.md", body="# 段\n正文。"))
        doc, created, _ch = await repo.ingest_document(
            loaded, chunk_document(loaded.content), corpus_version="mvp_v1"
        )
        assert created
        assert await repo.count_documents() == 1
        assert await repo.count_chunks() == 1

        stmt_doc = await test_session.get(KnowledgeDocument, doc.id)
        assert stmt_doc is not None
        stmt_chunks = await repo.list_chunks_by_document(doc.id)
        assert len(stmt_chunks) == 1
        assert stmt_chunks[0].document_id == doc.id
