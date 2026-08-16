"""CORS 回归测试（I-03）。

覆盖：
- cors_origins 配置解析（* → 通配；逗号列表 → 白名单）；
- CORSMiddleware 行为：允许来源返回 access-control-allow-origin，
  不允许来源不返回该头（浏览器同源策略才生效）。

与 main.py 一致使用 allow_credentials=True 装配中间件。
不依赖 DB / Redis / LLM。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings

_ALLOWED = "http://allowed.example.com"
_DISALLOWED = "http://evil.example.com"


class TestCorsConfigParse:
    def test_wildcard(self) -> None:
        assert Settings(cors_origins="*").get_cors_origins() == ["*"]
        assert Settings(cors_origins=" * ").get_cors_origins() == ["*"]

    def test_comma_list(self) -> None:
        assert Settings(cors_origins="http://a.com, http://b.com").get_cors_origins() == [
            "http://a.com",
            "http://b.com",
        ]

    def test_empty_list_removes_stray_entries(self) -> None:
        assert Settings(cors_origins="http://a.com,,http://b.com").get_cors_origins() == [
            "http://a.com",
            "http://b.com",
        ]
        # 空串 → 无来源（默认拒绝所有跨域，防御性兜底）
        assert Settings(cors_origins="").get_cors_origins() == []


def _build_app(origins: list[str]) -> FastAPI:
    """按 main.py 的中间件装配方式构造最小应用。"""
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    return app


class TestCorsMiddleware:
    async def test_allowed_origin_returns_header(self) -> None:
        app = _build_app([_ALLOWED])
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health", headers={"Origin": _ALLOWED})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == _ALLOWED

    async def test_disallowed_origin_no_header(self) -> None:
        app = _build_app([_ALLOWED])
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health", headers={"Origin": _DISALLOWED})
        assert resp.status_code == 200  # 请求仍服务，但浏览器不会放行响应
        assert "access-control-allow-origin" not in resp.headers

    async def test_wildcard_origin_returns_header(self) -> None:
        app = _build_app(["*"])
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health", headers={"Origin": _DISALLOWED})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin")
