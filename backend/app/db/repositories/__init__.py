"""DramaAgent Repository 模式。

提供通用 Repository 协议和 SQLAlchemy 实现。
具体实体的 Repository 在后续任务中添加。
"""

from app.db.repositories.base import BaseRepository, Repository
from app.db.repositories.knowledge import KnowledgeRepository

__all__ = [
    "Repository",
    "BaseRepository",
    "KnowledgeRepository",
]
