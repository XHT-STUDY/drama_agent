"""知识文档切块（D-02）。

按 Markdown 标题层级 + 语义段落把知识文档切分为可向量化的块：
- 保留父标题路径（heading_path），检索结果可回显来源层级；
- 确定性 chunk hash（基于 heading_path + content），供幂等摄取与「只重建变化部分」。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

# Markdown 标题：1-6 级
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

# 单块正文上限（超长段落按空行拆分为语义段落块）
DEFAULT_MAX_CHUNK_CHARS = 600


@dataclass(frozen=True)
class KnowledgeChunk:
    """一个待向量化的知识块（未写入数据库前的纯结构）。"""

    index: int
    content: str
    heading_path: list[str]
    chunk_hash: str

    def to_metadata(self) -> dict[str, Any]:
        """转换为 chunk_metadata（JSONB）的 JSON 友好结构。"""
        return {
            "heading_path": list(self.heading_path),
            "chunk_hash": self.chunk_hash,
        }


def compute_chunk_hash(heading_path: list[str], content: str) -> str:
    """确定性 chunk hash：标题路径 + 正文。"""
    payload = "\n".join(heading_path) + "\n" + content
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _split_long_section(text: str, max_chars: int) -> list[str]:
    """把超长段落拆分为不超过 max_chars 的语义段落块。

    按空行分段落，逐段累积；单段本身超长时原样保留（MVP 语料均为短片段）。
    """
    if len(text) <= max_chars:
        return [text]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    parts: list[str] = []
    buffer: list[str] = []
    buffer_len = 0
    for para in paragraphs:
        if buffer and buffer_len + len(para) + 1 > max_chars:
            parts.append("\n".join(buffer))
            buffer, buffer_len = [], 0
        buffer.append(para)
        buffer_len += len(para) + 1
    if buffer:
        parts.append("\n".join(buffer))
    return parts or [text]


def chunk_document(
    body: str,
    *,
    max_chars: int = DEFAULT_MAX_CHUNK_CHARS,
) -> list[KnowledgeChunk]:
    """把文档正文按标题层级 + 段落切分为知识块。

    Args:
        body: 文档正文（不含 frontmatter）。
        max_chars: 单块正文上限。

    Returns:
        按文档顺序排列的 KnowledgeChunk 列表。
    """
    sections: list[tuple[list[str], str]] = []
    heading_stack: list[str] = []
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_lines
        if current_lines:
            text = "\n".join(current_lines).strip()
            if text:
                sections.append((list(heading_stack), text))
            current_lines = []

    for line in body.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            heading_stack = heading_stack[: level - 1] + [title]
        else:
            current_lines.append(line)
    flush()

    chunks: list[KnowledgeChunk] = []
    index = 0
    for heading_path, content in sections:
        for part in _split_long_section(content, max_chars):
            chunks.append(
                KnowledgeChunk(
                    index=index,
                    content=part,
                    heading_path=heading_path,
                    chunk_hash=compute_chunk_hash(heading_path, part),
                )
            )
            index += 1
    return chunks
