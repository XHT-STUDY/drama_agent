"""DramaAgent API 依赖注入。

提供 FastAPI Depends() 可用的公共依赖：
- get_settings：获取应用配置
- get_request_id：获取当前请求 ID
"""

from __future__ import annotations

from fastapi import Request

from app.core.config import Settings


def get_settings(request: Request) -> Settings:
    """从 app.state 获取当前应用配置。

    配置实例在 create_app() 时挂载到 app.state.settings。

    Usage:
        @router.get("/example")
        async def example(settings: Settings = Depends(get_settings)): ...
    """
    return request.app.state.settings  # type: ignore[no-any-return]


def get_request_id(request: Request) -> str:
    """获取当前请求的唯一 ID。

    由 RequestIDMiddleware 分配并注入到响应头 X-Request-ID。
    """
    from app.core.errors import _request_id_ctx

    rid = _request_id_ctx.get()
    return rid if rid else "unknown"
