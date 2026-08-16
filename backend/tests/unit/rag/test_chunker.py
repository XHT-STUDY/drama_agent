"""D-02 知识切块（Chunker）单元测试。

覆盖：标题层级切块、父标题路径保留、段落拆分、hash 确定性、序号连续性。
纯本地逻辑，不访问网络与数据库。
"""

from __future__ import annotations

from app.rag.chunker import chunk_document, compute_chunk_hash


class TestChunkDocument:
    """chunk_document 切块行为。"""

    def test_empty_body_returns_no_chunks(self) -> None:
        """空正文返回空列表。"""
        assert chunk_document("") == []
        assert chunk_document("   \n\n  ") == []

    def test_no_heading_single_chunk(self) -> None:
        """无标题正文整体作为一个 chunk（heading_path 为空）。"""
        chunks = chunk_document("第一段内容。\n第二段内容。")
        assert len(chunks) == 1
        assert chunks[0].heading_path == []
        assert "第一段内容" in chunks[0].content

    def test_heading_splits_sections(self) -> None:
        """按标题拆分为多个 section。"""
        body = "# 战神逆袭\n第一段。\n# 赘婿归来\n第二段。"
        chunks = chunk_document(body)
        assert len(chunks) == 2
        assert chunks[0].heading_path == ["战神逆袭"]
        assert chunks[1].heading_path == ["赘婿归来"]

    def test_nested_headings_preserve_path(self) -> None:
        """嵌套标题保留父标题路径（层级正确截断）。"""
        body = (
            "# 主题\n"
            "开头。\n"
            "## 二级\n"
            "二级内容。\n"
            "### 三级\n"
            "三级内容。\n"
            "# 另一主题\n"
            "另一个。\n"
        )
        chunks = chunk_document(body)
        assert len(chunks) == 4
        assert chunks[0].heading_path == ["主题"]
        assert chunks[1].heading_path == ["主题", "二级"]
        assert chunks[2].heading_path == ["主题", "二级", "三级"]
        assert chunks[3].heading_path == ["另一主题"]

    def test_long_section_split_by_paragraph(self) -> None:
        """超长 section 按空行段落拆分。"""
        para = "字" * 300
        body = "\n\n".join([para, para, para])  # 3 × 300 字
        chunks = chunk_document(body, max_chars=400)
        assert len(chunks) == 3
        assert all(len(c.content) <= 400 for c in chunks)
        # 序号从 0 连续
        assert [c.index for c in chunks] == [0, 1, 2]

    def test_index_sequential_across_sections(self) -> None:
        """跨 section 的 chunk 序号连续。"""
        body = "# A\n" + "\n\n".join(["字" * 200] * 3) + "\n# B\n尾巴。"
        chunks = chunk_document(body, max_chars=300)
        assert [c.index for c in chunks] == list(range(len(chunks)))

    def test_heading_only_body_ignored(self) -> None:
        """只有标题没有正文时不产生 chunk。"""
        assert chunk_document("# 只有标题\n## 另一个标题") == []


class TestChunkHash:
    """chunk hash 确定性。"""

    def test_deterministic(self) -> None:
        """相同 heading_path + content 产生相同 hash。"""
        h1 = compute_chunk_hash(["a", "b"], "内容")
        h2 = compute_chunk_hash(["a", "b"], "内容")
        assert h1 == h2
        assert len(h1) == 64

    def test_content_change_changes_hash(self) -> None:
        """content 变化 hash 变化。"""
        assert compute_chunk_hash(["a"], "x") != compute_chunk_hash(["a"], "y")

    def test_path_change_changes_hash(self) -> None:
        """heading_path 变化 hash 变化。"""
        assert compute_chunk_hash(["a"], "x") != compute_chunk_hash(["b"], "x")

    def test_metadata_json_roundtrip(self) -> None:
        """chunk.to_metadata() 可 JSON 序列化且保留 heading_path/hash。"""
        chunks = chunk_document("# 主题\n内容。")
        assert len(chunks) == 1
        meta = chunks[0].to_metadata()
        assert meta["heading_path"] == ["主题"]
        assert meta["chunk_hash"] == chunks[0].chunk_hash

        import json

        json.dumps(meta)  # 必须可序列化
