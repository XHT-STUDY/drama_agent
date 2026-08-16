"""导入内容路由 — 纯函数路由表 (G-04)。

route_import(content_type) → 目标动作（确定性映射，无副作用，可直接单元测试）。

路由决策（DEV_PLAN §11.3 G-04）:
- idea_or_notes → create   创作灵感直接进创作流程（需求归一化入口）
- outline       → create   大纲进创作流程（作为创作输入）
- full_script   → evaluate 完整剧本直接评估
- reference     → hold     参考资料仅归档，不自动入库创作管线
- unknown       → needs_user_input  无法判断，需要用户确认
"""

from __future__ import annotations

from typing import Literal

from app.domain.enums import ContentType

ImportRoute = Literal["create", "evaluate", "hold", "needs_user_input"]

_ROUTE_MAP: dict[ContentType, ImportRoute] = {
    "idea_or_notes": "create",
    "outline": "create",
    "full_script": "evaluate",
    "reference": "hold",
    "unknown": "needs_user_input",
}


def route_import(content_type: ContentType) -> ImportRoute:
    """按内容类别返回目标动作。"""
    return _ROUTE_MAP[content_type]
