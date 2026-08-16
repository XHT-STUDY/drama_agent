"""知识文档加载（D-02）。

支持两种格式：
- Markdown（YAML frontmatter 元数据 + 正文）；
- JSON（{ "metadata": {...}, "content": "..." }）。

提供语料目录扫描（自动跳过 README / VERSION / rubric 特殊资产），
以及确定性 document hash（元数据 + 正文），供幂等摄取判定。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.rag.models import KnowledgeDocMetadata, parse_frontmatter

# 语料扫描支持的文档后缀
_KNOWN_SUFFIXES = {".md", ".json"}
# 根目录下非知识文档文件
_NON_DOC_FILENAMES = {"README.md", "VERSION"}
# 特殊资产目录（rubric 由 E-01 单独承载，不按知识文档摄取）
_SKIP_DIRS = {"rubric"}


@dataclass(frozen=True)
class LoadedKnowledgeDoc:
    """加载并解析完成的知识文档（未切块、未向量化）。"""

    path: Path
    metadata: KnowledgeDocMetadata
    content: str
    document_hash: str


def strip_frontmatter(raw: str) -> str:
    """去掉文档开头的 frontmatter，返回正文。"""
    stripped = raw
    if stripped.startswith("---"):
        # 只切第一个 --- 块，保留正文中可能出现的分隔线
        parts = stripped.split("\n---", 1)
        if len(parts) == 2:
            stripped = parts[1]
    return stripped.strip()


def compute_document_hash(metadata: KnowledgeDocMetadata, body: str) -> str:
    """确定性 document hash：规范化元数据 + 正文。

    任一字段或正文变化都会产生不同哈希，作为幂等摄取与变更重建的依据。
    """
    canonical = json.dumps(
        {
            "category": metadata.category.value,
            "title": metadata.title,
            "source": metadata.source,
            "license": metadata.license,
            "language": metadata.language,
            "genre": metadata.genre,
            "stage": metadata.stage,
            "tags": sorted(metadata.tags),
            "version": metadata.version,
            "body": body,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_json_doc(raw: str, path: Path) -> tuple[KnowledgeDocMetadata, str]:
    """解析 JSON 格式知识文档：{metadata, content}。"""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"知识文档 JSON 解析失败 ({path}): {e}") from e
    if not isinstance(data, dict) or "metadata" not in data or "content" not in data:
        raise ValueError(f"知识文档 JSON 缺少 metadata/content 键 ({path})")
    metadata = KnowledgeDocMetadata.model_validate(data["metadata"])
    content = str(data["content"]).strip()
    return metadata, content


def load_knowledge_file(path: Path) -> LoadedKnowledgeDoc:
    """加载单个知识文档文件（.md / .json）。

    Raises:
        ValueError: 元数据解析或校验失败、格式不支持。
    """
    raw = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        metadata, body = _parse_json_doc(raw, path)
    elif path.suffix == ".md":
        metadata = KnowledgeDocMetadata.model_validate(parse_frontmatter(raw))
        body = strip_frontmatter(raw)
    else:
        raise ValueError(f"不支持的知识文档格式: {path.suffix} ({path})")
    return LoadedKnowledgeDoc(
        path=path,
        metadata=metadata,
        content=body,
        document_hash=compute_document_hash(metadata, body),
    )


def discover_knowledge_files(root: Path) -> list[Path]:
    """扫描语料目录，返回待摄取的知识文档文件列表（排序稳定）。

    自动跳过：
    - 根目录 README.md / VERSION（非知识文档）；
    - rubric/ 目录（E-01 特殊资产，另行管理）。
    """
    if not root.is_dir():
        raise FileNotFoundError(f"语料目录不存在: {root}")

    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if rel.name in _NON_DOC_FILENAMES:
            continue
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() not in _KNOWN_SUFFIXES:
            continue
        files.append(path)
    return files


def load_knowledge_corpus(root: Path) -> list[LoadedKnowledgeDoc]:
    """加载语料目录下的全部知识文档。"""
    return [load_knowledge_file(path) for path in discover_knowledge_files(root)]
