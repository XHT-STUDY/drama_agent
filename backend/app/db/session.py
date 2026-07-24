"""异步数据库会话管理。

提供引擎初始化、会话工厂和 FastAPI 依赖注入函数。

使用方式：
- 应用启动时调用 init_db(settings) 一次；
- 请求中通过 FastAPI Depends(get_db) 获取异步会话；
- 应用关闭时调用 close_db() 释放连接池。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings

# 模块级单例：引擎和会话工厂在 init_db 时初始化
_engine = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None
_owns_engine = False  # init_db 创建的引擎才由 close_db 负责释放


def init_db(settings: Settings) -> None:
    """初始化异步数据库引擎和会话工厂。

    应在应用启动时调用一次（如 FastAPI lifespan 中）。
    重复调用会覆盖原有引擎（先关闭旧引擎）。

    如果测试代码已通过设置 _async_session_factory 完成初始化，
    则本函数为 no-op，不会覆盖测试引擎。

    Args:
        settings: 应用配置，从中读取 database_url 和 database_echo。
    """
    global _engine, _async_session_factory, _owns_engine

    # 已由测试 fixture 初始化，跳过
    if _async_session_factory is not None:
        return

    _engine = create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_size=10,
        max_overflow=20,
    )
    _async_session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    _owns_engine = True


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends: 为每个请求提供独立的数据库会话。

    会话在请求结束时自动关闭；
    expire_on_commit=False 确保 commit 后仍可访问已加载对象。

    Usage:
        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)): ...
    """
    if _async_session_factory is None:
        raise RuntimeError("数据库未初始化，请先调用 init_db(settings)")

    async with _async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_db() -> None:
    """关闭数据库引擎，释放所有连接池资源。

    应在应用关闭时调用（如 FastAPI lifespan 的 shutdown 阶段）。
    仅释放由 init_db 创建的引擎；测试 fixture 注入的引擎不受影响。
    """
    global _engine, _async_session_factory, _owns_engine
    if _owns_engine and _engine is not None:
        await _engine.dispose()
        _engine = None
        _async_session_factory = None
        _owns_engine = False
