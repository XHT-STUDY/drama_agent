"""SQLAlchemy 公共基类与 Mixin。

提供所有 ORM 模型共用的：
- DeclarativeBase（Base）
- UUID 主键 + 创建/更新时间戳（UUIDMixin）
- 软删除标记（SoftDeleteMixin）

所有表模型继承 Base + UUIDMixin；
需要软删除的模型（projects、conversations）额外继承 SoftDeleteMixin。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。

    不包含任何默认列，列由 Mixin 或具体模型定义。
    """

    pass


class UUIDMixin:
    """UUID v4 主键 + 自动时间戳。

    所有持久化表统一使用 UUID 作为主键（DEV_PLAN §5.1），
    created_at / updated_at 使用带时区的 UTC 时间。
    """

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        comment="UUID v4 主键",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        comment="创建时间（UTC）",
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        comment="最后更新时间（UTC）",
    )


class SoftDeleteMixin:
    """软删除标记。

    包含此 Mixin 的表不会物理删除记录，
    而是设置 deleted_at 时间戳。
    MVP 阶段仅 projects 和 conversations 使用软删除（DEV_PLAN §6.2）。
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
        comment="软删除时间（UTC），NULL 表示未删除",
    )
