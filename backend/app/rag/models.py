"""RAG 知识库领域模型（D-01）。

定义知识文档的分类、元数据与语料版本契约：
- KnowledgeCategory：7 类知识分类（与 DEV_PLAN 阶段 D 一致）
- KnowledgeDocMetadata：知识文档 frontmatter 元数据校验（extra=forbid）
- extract_doc_metadata / load_corpus_version：元数据解析与语料版本读取

模块边界：只做"解析 + 校验 + 版本读取"，不做切块与向量化（loader/chunker 在 D-02）。
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

# knowledge/ 目录相对于本文件（backend/app/rag/ → 仓库根）
_KNOWLEDGE_ROOT = Path(__file__).resolve().parents[3] / "knowledge"

# 语料版本默认值（knowledge/VERSION 缺失时使用）
DEFAULT_CORPUS_VERSION = "mvp_v1"


class KnowledgeCategory(StrEnum):
    """知识文档分类（DEV_PLAN 阶段 D：D-01 定义的 7 类）。

    rubric 分类由 knowledge/rubric/mvp_v1.yaml 单独承载（E-01 已实现），
    其余分类以独立知识文档形式存在于 knowledge/templates|hooks|examples|compliance。
    """

    GENRE_TEMPLATE = "genre_template"
    OPENING_HOOK = "opening_hook"
    ENDING_HOOK = "ending_hook"
    PAYOFF = "payoff"
    CHARACTER_ARCHETYPE = "character_archetype"
    RUBRIC = "rubric"
    COMPLIANCE = "compliance"


# 语料中按独立文档存放的分类（rubric 是特殊资产，单独计数）
CORPUS_DOC_CATEGORIES: tuple[KnowledgeCategory, ...] = (
    KnowledgeCategory.GENRE_TEMPLATE,
    KnowledgeCategory.OPENING_HOOK,
    KnowledgeCategory.ENDING_HOOK,
    KnowledgeCategory.PAYOFF,
    KnowledgeCategory.CHARACTER_ARCHETYPE,
    KnowledgeCategory.COMPLIANCE,
)


class KnowledgeDocMetadata(BaseModel):
    """知识文档 frontmatter 元数据（extra=forbid）。

    字段与 knowledge/README.md 的约定一致：
    - source / license 必填，保证内容治理合规（只纳入有权限使用的内容）；
    - stage 为适用创作阶段（story_bible / outline / writer），供 D-04 过滤。
    """

    model_config = {"extra": "forbid"}

    title: str = Field(..., description="文档标题", min_length=1)
    source: str = Field(..., description="内容来源（合规必填）", min_length=1)
    license: str = Field(..., description="授权许可证（合规必填）", min_length=1)
    category: KnowledgeCategory = Field(..., description="知识分类")
    language: str = Field(default="zh", description="语言代码", min_length=2, max_length=16)
    genre: str = Field(default="", description="题材（如 都市/战神/赘婿）")
    stage: str = Field(default="", description="适用创作阶段（story_bible/outline/writer）")
    tags: list[str] = Field(default_factory=list, description="检索标签")
    version: str = Field(default="1.0.0", description="文档版本", min_length=1)


class KnowledgeMetadataError(Exception):
    """知识文档元数据解析或校验失败。"""


class EmbeddingResult(BaseModel):
    """一次向量化（Embedding）的结果（D-03）。

    vectors 与输入文本顺序一一对应；model 记录实际使用的模型，
    dimension 为实际向量维度（用于与 pgvector 模型维度一致性校验）。
    """

    vectors: list[list[float]] = Field(..., description="与输入顺序对应的向量列表")
    model: str = Field(..., description="使用的模型名")
    dimension: int = Field(..., description="实际向量维度")
    duration_ms: int = Field(0, description="调用耗时（毫秒）")
    calls: int = Field(1, description="实际 API 调用次数")
    cached_count: int = Field(0, description="本次命中缓存的文本数")


# --- frontmatter 解析（供 loader 复用） ---

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n?", re.DOTALL)


def parse_frontmatter(raw_text: str) -> dict[str, Any]:
    """解析 Markdown/YAML 文本开头的 `---` 包裹 frontmatter。

    Returns:
        元数据 dict。

    Raises:
        KnowledgeMetadataError: 缺少 frontmatter 或 YAML 解析失败。
    """
    match = _FRONTMATTER_RE.match(raw_text)
    if match is None:
        raise KnowledgeMetadataError("文档缺少 frontmatter（须以 --- 开头并包含元数据块）")
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as e:
        raise KnowledgeMetadataError(f"frontmatter YAML 解析失败: {e}") from e
    if not isinstance(data, dict):
        raise KnowledgeMetadataError("frontmatter 内容必须是键值映射")
    return data


def extract_doc_metadata(raw_text: str) -> KnowledgeDocMetadata:
    """从文档原始文本解析并校验元数据。

    Raises:
        KnowledgeMetadataError: 解析或校验失败（缺 source/license/title/category 等）。
    """
    try:
        return KnowledgeDocMetadata.model_validate(parse_frontmatter(raw_text))
    except KnowledgeMetadataError:
        raise
    except Exception as e:  # pydantic.ValidationError 及其子类
        raise KnowledgeMetadataError(f"知识文档元数据校验失败: {e}") from e


# --- 语料版本 ---


def knowledge_root() -> Path:
    """返回语料根目录（仓库根 / knowledge）。"""
    return _KNOWLEDGE_ROOT


def corpus_version_path() -> Path:
    """返回语料版本文件路径 knowledge/VERSION。"""
    return _KNOWLEDGE_ROOT / "VERSION"


def load_corpus_version() -> str:
    """读取当前语料版本（knowledge/VERSION），缺失时返回默认版本。

    语料版本会写入 RetrievalTrace（D-04），用于追溯检索依据。
    """
    path = corpus_version_path()
    if path.exists():
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            return raw
    return DEFAULT_CORPUS_VERSION
