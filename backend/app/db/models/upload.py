"""Upload ORM 模型 — 上传文件元数据。

对应 DEV_PLAN §6.1 uploads 表。
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin


class Upload(Base, UUIDMixin):
    """用户上传的文件。

    仅存储元数据（路径、哈希、类型），
    文件本体存放在本地文件系统或对象存储中。
    """

    __tablename__ = "uploads"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"),
        index=True,
        comment="所属项目 ID",
    )
    path: Mapped[str] = mapped_column(
        String(500),
        comment="文件存储路径（相对于 UPLOAD_FILE_ROOT）",
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

    def __repr__(self) -> str:
        return f"<Upload id={self.id!s} mime={self.mime_type!r} size={self.size_bytes}>"
