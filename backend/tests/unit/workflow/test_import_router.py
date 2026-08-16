"""route_import 纯函数契约测试 (G-04).

路由决策（无副作用，可直接单测）:
- idea_or_notes → create      创作灵感直接进创作流程
- outline       → create      大纲进创作流程
- full_script   → evaluate    完整剧本直接评估
- reference     → hold        参考资料仅归档，不自动入库创作管线
- unknown       → needs_user_input   无法判断，需要用户确认

契约：ContentType 全部 5 类都必须有确定路由，不允许漏映射。
"""

from __future__ import annotations

from typing import get_args

import pytest

from app.domain.enums import ContentType
from app.workflows.router import _ROUTE_MAP, ImportRoute, route_import


@pytest.mark.unit
class TestRouteImport:
    """导入路由映射。"""

    @pytest.mark.parametrize(
        ("content_type", "expected"),
        [
            ("idea_or_notes", "create"),
            ("outline", "create"),
            ("full_script", "evaluate"),
            ("reference", "hold"),
            ("unknown", "needs_user_input"),
        ],
    )
    def test_maps_each_content_type(self, content_type: str, expected: str) -> None:
        """每一类内容都有确定的目标动作。"""
        assert route_import(content_type) == expected  # type: ignore[arg-type]

    def test_covers_all_content_types(self) -> None:
        """契约：路由表覆盖全部 ContentType，不允许未知类别。"""
        literal_values = get_args(ContentType)
        assert set(_ROUTE_MAP.keys()) == set(literal_values)

    def test_route_is_subset_of_import_route_literal(self) -> None:
        """契约：所有路由值都是 ImportRoute 字面量之一。"""
        allowed = set(get_args(ImportRoute))
        assert set(_ROUTE_MAP.values()).issubset(allowed)

    def test_known_routes_exhaustive(self) -> None:
        """契约：ImportRoute 只允许 create/evaluate/hold/needs_user_input。"""
        assert get_args(ImportRoute) == (
            "create",
            "evaluate",
            "hold",
            "needs_user_input",
        )

    def test_reference_never_enters_creation_pipeline(self) -> None:
        """reference 语义：仅归档（hold），绝不自动进入创作管线。"""
        assert route_import("reference") == "hold"
        assert route_import("reference") not in ("create", "evaluate")

    def test_unknown_requires_user_input(self) -> None:
        """unknown 语义：停在 needs_user_input，交由用户确认。"""
        assert route_import("unknown") == "needs_user_input"
