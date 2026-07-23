"""Alembic 异步迁移环境配置。

从 app.core.config.Settings 读取数据库 URL，
使用 SQLAlchemy 异步引擎运行迁移。
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db.base import Base

# 导入所有模型确保 Alembic 能发现它们
import app.db.models  # noqa: F401

# Alembic Config 对象
config = context.config

# 配置日志
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 从 Settings 读取目标数据库 URL（异步）
settings = Settings()
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本而不连接数据库。

    用于审查迁移 SQL 或在不便连接数据库的环境中生成迁移脚本。
    """
    url = settings.database_url_sync
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Any) -> None:  # noqa: ANN401
    """在给定的同步连接上执行迁移。"""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """在线模式：连接数据库并执行迁移。

    使用异步引擎创建连接，然后切换到同步连接运行迁移。
    """
    connectable = create_async_engine(
        settings.database_url,
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


# 选择在线/离线模式
if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
