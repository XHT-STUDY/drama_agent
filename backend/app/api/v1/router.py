"""DramaAgent API v1 路由聚合。

注册所有 v1 端点：
- /health/live  — Kubernetes liveness probe
- /health/ready — Kubernetes readiness probe（含 DB/Redis 检查）
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request

from app.core.errors import ServiceUnavailableError
from app.core.logging import get_logger

router = APIRouter(tags=["health"])
logger = get_logger(__name__)


# ---- 依赖健康检查辅助函数 ----


async def _check_database(dsn: str) -> dict[str, Any]:
    """检查 PostgreSQL 数据库连通性。

    使用 asyncpg 直连（不依赖 SQLAlchemy ORM），
    执行 SELECT 1 作为探活查询。

    Returns:
        {"name": "database", "status": "ok"|"unavailable", "latency_ms": ..., "error": ...}
    """
    start = time.monotonic()
    try:
        import asyncpg

        # 将 SQLAlchemy 风格的 DSN 转为 asyncpg 可用的格式
        pg_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(dsn=pg_dsn, timeout=5)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        return {"name": "database", "status": "ok", "latency_ms": latency_ms}
    except ImportError:
        logger.warning("asyncpg 未安装，跳过数据库健康检查")
        return {"name": "database", "status": "skipped", "error": "asyncpg not installed"}
    except Exception as exc:
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        logger.warning("数据库健康检查失败: %s", exc)
        return {"name": "database", "status": "unavailable", "latency_ms": latency_ms, "error": str(exc)}


async def _check_redis(url: str) -> dict[str, Any]:
    """检查 Redis 连通性。

    使用 redis-py 的异步客户端执行 PING 命令。

    Returns:
        {"name": "redis", "status": "ok"|"unavailable", "latency_ms": ..., "error": ...}
    """
    start = time.monotonic()
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(url, socket_connect_timeout=5)
        try:
            await r.ping()
        finally:
            await r.aclose()
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        return {"name": "redis", "status": "ok", "latency_ms": latency_ms}
    except ImportError:
        logger.warning("redis-py 未安装，跳过 Redis 健康检查")
        return {"name": "redis", "status": "skipped", "error": "redis-py not installed"}
    except Exception as exc:
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        logger.warning("Redis 健康检查失败: %s", exc)
        return {"name": "redis", "status": "unavailable", "latency_ms": latency_ms, "error": str(exc)}


# ---- 健康检查端点 ----


@router.get("/health/live")
async def health_live(request: Request) -> dict[str, str]:
    """存活检查 — 不依赖任何外部服务，仅确认进程在运行。

    用于 Kubernetes liveness probe。
    始终返回 200 OK，不做任何外部调用。
    """
    return {"status": "ok"}


@router.get("/health/ready")
async def health_ready(request: Request) -> dict[str, Any]:
    """就绪检查 — 验证关键外部依赖是否可用。

    检查 PostgreSQL 和 Redis 的连通性。
    全部可用返回 200；任一不可用返回 503 并指明失败的依赖名称。

    用于 Kubernetes readiness probe。
    """
    from app.api.dependencies import get_settings

    settings = get_settings(request)
    checks: list[dict[str, Any]] = []

    # 并行检查所有依赖
    db_check = await _check_database(settings.database_url)
    redis_check = await _check_redis(settings.redis_url)
    checks = [db_check, redis_check]

    # 筛选出不可用的依赖（skipped 视为可用——未安装库是环境问题，非运行时故障）
    failed = [c for c in checks if c["status"] == "unavailable"]

    if failed:
        dependency_names = ", ".join(c["name"] for c in failed)
        raise ServiceUnavailableError(
            detail=f"依赖不可用: {dependency_names}",
            code="SERVICE_UNAVAILABLE",
        )

    return {"status": "ok", "checks": checks}
