"""DramaAgent 数据库持久化层。

提供：
- SQLAlchemy ORM 模型（db.models）
- 异步会话管理（db.session）
- Repository 模式（db.repositories）
- 公共基类与 Mixin（db.base）

模块边界（见 DEV_PLAN.md §4.1）：
- repositories 只负责数据持久化，不涉及业务评分和控制流。
"""

from app.db.base import Base, SoftDeleteMixin, UUIDMixin
from app.db.session import close_db, get_db, init_db

__all__ = [
    "Base",
    "UUIDMixin",
    "SoftDeleteMixin",
    "init_db",
    "get_db",
    "close_db",
]
