"""D-01 知识库元数据与内容治理测试。

对 knowledge/ 语料做全量扫描校验：
- 每个知识文档 frontmatter 必须通过 KnowledgeDocMetadata 校验（必含 source/license/title/category）；
- 每类（除 rubric）至少 3 个可测试短片段；
- 测试资料不包含完整商业剧本（正文长度启发式）；
- 语料版本可读取且与 VERSION 文件一致（corpus_version 可写入检索追踪）。

本测试只读本地文件，不访问网络。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.rag.models import (
    CORPUS_DOC_CATEGORIES,
    KnowledgeCategory,
    KnowledgeMetadataError,
    corpus_version_path,
    extract_doc_metadata,
    knowledge_root,
    load_corpus_version,
)

# 语料根目录
KNOWLEDGE_ROOT = knowledge_root()

# 知识文档最大正文字数（防完整商业剧本混入）
MAX_DOC_BODY_CHARS = 3000

# 单类最少片段数（D-01 验收：每类 ≥3 个可测试短片段）
MIN_FRAGMENTS_PER_CATEGORY = 3


def _corpus_docs() -> list[tuple[Path, str]]:
    """返回语料中所有知识文档（排除根 README.md 与 rubric 特殊资产）。"""
    docs: list[tuple[Path, str]] = []
    for path in sorted(KNOWLEDGE_ROOT.rglob("*.md")):
        if path.relative_to(KNOWLEDGE_ROOT) == Path("README.md"):
            continue  # 说明文档，非知识片段
        docs.append((path, path.read_text(encoding="utf-8")))
    return docs


def _body(text: str) -> str:
    """去除 frontmatter 后返回正文。"""
    stripped = text
    if stripped.startswith("---"):
        parts = stripped.split("\n---", 1)
        if len(parts) == 2:
            stripped = parts[1]
    return stripped.strip()


class TestCorpusMetadata:
    """语料元数据全量校验（D-01 验收 1：每份文档有来源与授权字段）。"""

    def test_all_docs_have_valid_metadata(self) -> None:
        """每个文档 frontmatter 均通过 KnowledgeDocMetadata 校验。"""
        docs = _corpus_docs()
        assert len(docs) >= 18, f"语料文档过少: {len(docs)}"
        for path, text in docs:
            meta = extract_doc_metadata(text)
            assert meta.title.strip(), f"{path}: 标题为空"
            assert meta.source.strip(), f"{path}: source 为空"
            assert meta.license.strip(), f"{path}: license 为空"
            assert meta.category in KnowledgeCategory, f"{path}: category 非法"

    def test_all_docs_have_nonempty_body(self) -> None:
        """每个文档正文非空（可测试短片段）。"""
        for path, text in _corpus_docs():
            assert _body(text), f"{path}: 正文为空"

    def test_self_authored_source_for_all_docs(self) -> None:
        """语料全部为仓库自建原创内容（内容治理要求）。"""
        for path, text in _corpus_docs():
            meta = extract_doc_metadata(text)
            assert "self-auth" in meta.source.lower(), f"{path}: source 非自建原创"

    def test_no_full_commercial_script(self) -> None:
        """测试资料不含完整商业剧本（长度启发式）。"""
        for path, text in _corpus_docs():
            body = _body(text)
            assert len(body) <= MAX_DOC_BODY_CHARS, (
                f"{path}: 正文 {len(body)} 字超过上限 {MAX_DOC_BODY_CHARS}，疑似完整剧本"
            )


class TestCategoryCoverage:
    """每类至少 3 个可测试片段（D-01 验收）。"""

    def test_each_category_has_min_fragments(self) -> None:
        """除 rubric 外每类文档数 ≥ 3。"""
        counts: dict[KnowledgeCategory, int] = {}
        for _path, text in _corpus_docs():
            meta = extract_doc_metadata(text)
            counts[meta.category] = counts.get(meta.category, 0) + 1
        for category in CORPUS_DOC_CATEGORIES:
            assert counts.get(category, 0) >= MIN_FRAGMENTS_PER_CATEGORY, (
                f"分类 {category.value} 只有 {counts.get(category, 0)} 个片段，"
                f"少于 {MIN_FRAGMENTS_PER_CATEGORY}"
            )


class TestMetadataValidation:
    """元数据 Schema 校验行为。"""

    def test_missing_license_rejected(self) -> None:
        """缺 license 必须校验失败。"""
        raw = """---
category: payoff
title: 测试
source: drama-agent-self-auth
---
正文
"""
        with pytest.raises(KnowledgeMetadataError):
            extract_doc_metadata(raw)

    def test_unknown_category_rejected(self) -> None:
        """非法 category 必须校验失败。"""
        raw = """---
category: not_a_real_category
title: 测试
source: drama-agent-self-auth
license: MIT
---
正文
"""
        with pytest.raises(KnowledgeMetadataError):
            extract_doc_metadata(raw)

    def test_extra_fields_rejected(self) -> None:
        """extra=forbid：多填字段必须校验失败。"""
        raw = """---
category: payoff
title: 测试
source: drama-agent-self-auth
license: MIT
extra_field: 不允许
---
正文
"""
        with pytest.raises(KnowledgeMetadataError):
            extract_doc_metadata(raw)

    def test_missing_frontmatter_rejected(self) -> None:
        """无 frontmatter 的文档必须报错。"""
        with pytest.raises(KnowledgeMetadataError):
            extract_doc_metadata("没有 frontmatter 的正文")


class TestCorpusVersion:
    """语料版本读取（corpus_version 可写入检索追踪）。"""

    def test_corpus_version_readable(self) -> None:
        """能读取语料版本，且非空。"""
        version = load_corpus_version()
        assert version.strip(), "语料版本为空"

    def test_corpus_version_matches_version_file(self) -> None:
        """load_corpus_version 与 knowledge/VERSION 文件一致。"""
        path = corpus_version_path()
        assert path.exists(), "knowledge/VERSION 缺失"
        expected = path.read_text(encoding="utf-8").strip()
        assert load_corpus_version() == expected
