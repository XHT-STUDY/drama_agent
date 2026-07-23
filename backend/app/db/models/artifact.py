"""Artifact ORM 模型 — 不可变资产表。

对应 DEV_PLAN §6.1 artifacts 表。
约束：
- (project_id, type, episode_number, version) 唯一
- version > 0
- episode_number >= 1
- content 不可 UPDATE（应用层保证）
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin


class Artifact(Base, UUIDMixin):
    """创作资产——StoryBible、大纲、剧本、评估报告等。

    所有资产不可原地修改（immutable），
    修订通过创建新版本记录完成。
    默认查询返回最新 valid 版本。
    """

    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "type", "episode_number", "version",
            name="uq_artifacts_project_type_episode_version",
        ),
        CheckConstraint("version > 0", name="ck_artifacts_version_positive"),
        CheckConstraint("episode_number >= 1", name="ck_artifacts_episode_positive"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"),
        index=True,
        comment="所属项目 ID",
    )
    type: Mapped[str] = mapped_column(
        String(50),
        index=True,
        comment="资产类型：story_bible / episode_outline_set / script_draft 等",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        comment="版本号（事务内递增分配）",
    )
    episode_number: Mapped[int] = mapped_column(
        Integer,
        default=1,
        comment="关联集数（非剧本类资产为 1）",
    )
    content: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        comment="业务内容（写入前须通过 Pydantic Schema 校验）",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="draft",
        comment="校验状态：draft → valid 或 draft → invalid",
    )
    content_schema_version: Mapped[str] = mapped_column(
        String(20),
        default="1.0",
        comment="content 对应的 Pydantic Schema 版本号",
    )
    prompt_version: Mapped[str] = mapped_column(
        String(20),
        default="",
        comment="生成此 Artifact 的 Prompt 版本号",
    )
    input_hash: Mapped[str | None] = mapped_column(
        String(64),
        default=None,
        comment="输入 Artifact ID 集合的 SHA256 哈希（用于幂等去重）",
    )
    checksum: Mapped[str | None] = mapped_column(
        String(64),
        default=None,
        comment="content 规范化 JSON 的 SHA256 校验和",
    )
    source_artifact_ids: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        default=None,
        comment="来源 Artifact 引用列表：[{artifact_id, version, relation}]",
    )

    def __repr__(self) -> str:
        return (
            f"<Artifact id={self.id!s} type={self.type!r} "
            f"v{self.version} ep={self.episode_number}>"
        )
