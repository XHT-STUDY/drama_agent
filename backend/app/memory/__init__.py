"""Memory 模块 — 连续性管理与上下文组装 (C-06).

提供:
- ContinuityManager: 跨集连续性状态管理
- ContextBuilder: 按预算组装 Skill 上下文
"""

from app.memory.context_builder import ContextBuilder, ContextManifest
from app.memory.continuity import ContinuityManager

__all__ = [
    "ContinuityManager",
    "ContextBuilder",
    "ContextManifest",
]
