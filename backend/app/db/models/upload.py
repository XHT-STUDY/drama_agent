"""Upload ORM 模型 — 上传文件元数据。

对应 DEV_PLAN §6.1 uploads 表。
G-03 扩展：original_name / parse_status / char_count / warnings（解析元数据列）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin


class Upload(Base, UUIDMixin):
    """用户上传的文件。

    仅存储元数据（路径、哈希、类型、解析结果），
    文件本体存放在本地文件系统或对象存储中。

    安全约束（G-03）：`path` 是服务端生成的存储键（UUID 文件名），
    客户端原始文件名只进 `original_name`（仅用于展示），永不用于磁盘路径。
    """

    __tablename__ = "uploads"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"),
        index=True,
        comment="所属项目 ID",
    )
    path: Mapped[str] = mapped_column(
        String(500),
        comment="文件存储键（服务端生成的相对路径，不包含客户端输入）",
    )
    sha256: Mapped[str] = mapped_column(
        String(64),
        comment="文件 SHA256 哈希（用于项目内去重）",
    )
    mime_type: Mapped[str] = mapped_column(
        String(100),
        comment="MIME 类型（如 text/plain、application/vnd.openxmlformats...）",
    )
    size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        comment="文件大小（字节）",
    )
    original_name: Mapped[str] = mapped_column(
        String(255),
        default="",
        comment="客户端原始文件名（仅展示，永不用于磁盘路径）",
    )
    parse_status: Mapped[str] = mapped_column(
        String(20),
        default="parsed",
        comment="解析状态（parsed/failed）",
    )
    char_count: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        comment="解析出的文本字符数",
    )
    warnings: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        comment="解析告警（不阻断入库）",
    )

    def __repr__(self) -> str:
        return f"<Upload id={self.id!s} mime={self.mime_type!r} size={self.size_bytes}>"
