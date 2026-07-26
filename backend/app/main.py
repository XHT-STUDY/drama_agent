"""DramaAgent FastAPI 应用工厂。

使用 create_app(settings) 创建应用实例，
统一管理 middleware、异常处理、路由注册和结构化日志。

模块边界：main.py 仅负责组装，不包含业务逻辑。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.router import router as v1_router
from app.core.config import Settings, load_settings
from app.core.errors import _request_id_ctx, register_exception_handlers
from app.core.logging import get_logger, setup_logging

logger = get_logger(__name__)


# ---- 中间件 ----

class RequestIDMiddleware(BaseHTTPMiddleware):
    """为每个 HTTP 请求分配唯一 ID。

    - 优先复用客户端传入的 X-Request-ID 头；
    - 若未传入则生成 UUID4；
    - 将 request_id 写入 contextvar 供日志/错误模块获取；
    - 在响应头 X-Request-ID 中返回。
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        _request_id_ctx.set(request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """精简请求日志中间件（替代 uvicorn.access）。

    格式: METHOD /path → STATUS (XXms)
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = round((time.monotonic() - start) * 1000)
        logger.info(
            "%s %s → %s (%dms)",
            request.method, request.url.path, response.status_code, elapsed_ms,
        )
        return response


# ---- 生命周期 ----


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    """应用生命周期管理。

    启动：初始化 DB 引擎 + 结构化日志。
    关闭：释放 DB 连接池资源。
    """
    settings: Settings = app.state.settings
    log_fmt = "json" if settings.app_env == "production" else "console"
    setup_logging(settings.log_level, fmt=log_fmt)

    # 初始化数据库引擎
    from app.db.session import init_db
    init_db(settings)

    logger.info(
        "应用启动",
        extra={
            "app_env": settings.app_env,
            "host": settings.app_host,
            "port": settings.app_port,
        },
    )
    yield

    # 关闭数据库连接池
    from app.db.session import close_db
    await close_db()
    logger.info("应用关闭")


# ---- 应用工厂 ----


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建并配置 FastAPI 应用实例。

    Args:
        settings: 应用配置。为 None 时从环境变量自动加载。

    Returns:
        已配置中间件、异常处理器和路由的 FastAPI 实例。
    """
    if settings is None:
        settings = load_settings()

    # 生产环境禁用交互式文档
    docs_url: str | None = None if settings.app_env == "production" else "/docs"
    redoc_url: str | None = None if settings.app_env == "production" else "/redoc"

    app = FastAPI(
        title="DramaAgent",
        description="面向中文短剧创作的对话型 Agent 系统",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_tags=[
            {"name": "health", "description": "健康检查端点"},
        ],
    )

    # 将配置挂载到 app.state，供依赖注入使用
    app.state.settings = settings

    # ---- 注册中间件（注册顺序即执行顺序：外→内） ----

    # CORS 中间件（最外层）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request ID 中间件（CORS 内层，确保所有请求都有 request_id）
    app.add_middleware(RequestIDMiddleware)

    # 请求日志中间件（最内层，精确计时）
    app.add_middleware(RequestLoggingMiddleware)

    # ---- 注册异常处理器 ----
    register_exception_handlers(app)

    # ---- 注册路由 ----
    app.include_router(v1_router, prefix="/api/v1")

    logger.info("FastAPI 应用实例创建完成")
    return app
