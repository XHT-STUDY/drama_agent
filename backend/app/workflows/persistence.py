"""LangGraph PostgreSQL checkpointer 生命周期与健康检查。"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection

from app.core.config import Settings


def checkpoint_dsn(settings: Settings) -> str:
    """把 SQLAlchemy asyncpg URL 转为 psycopg 可用 DSN。"""
    database_url = settings.database_url
    if settings.app_env == "test":
        database_url = os.environ.get("TEST_DATABASE_URL", database_url)
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@asynccontextmanager
async def open_workflow_checkpointer(
    settings: Settings,
) -> AsyncIterator[AsyncPostgresSaver]:
    """打开已初始化的 saver；运行期绝不执行 setup/DDL。"""
    async with AsyncPostgresSaver.from_conn_string(
        checkpoint_dsn(settings)
    ) as saver:
        yield saver


async def setup_checkpoint_schema(settings: Settings) -> None:
    """显式安装 LangGraph checkpoint 表，仅供迁移/运维 CLI 调用。"""
    async with open_workflow_checkpointer(settings) as saver:
        await saver.setup()


async def checkpoint_schema_ready(settings: Settings) -> bool:
    """只读检查必要表是否存在，适合应用启动期。"""
    async with await AsyncConnection.connect(
        checkpoint_dsn(settings),
        autocommit=True,
    ) as conn:
        cursor = await conn.execute(
            """
            SELECT to_regclass('public.checkpoints') IS NOT NULL
               AND to_regclass('public.checkpoint_writes') IS NOT NULL
               AND to_regclass('public.checkpoint_migrations') IS NOT NULL
            """
        )
        row = await cursor.fetchone()
        return bool(row and row[0])


async def verify_checkpoint_read_write(settings: Settings) -> None:
    """写入、读取并删除一个探针 checkpoint。"""
    thread_id = f"doctor-{uuid.uuid4()}"
    config: dict[str, Any] = {
        "configurable": {"thread_id": thread_id, "checkpoint_ns": ""}
    }
    metadata = cast(
        Any,
        {"source": "input", "step": -1, "parents": {}},
    )
    async with open_workflow_checkpointer(settings) as saver:
        saved_config = await saver.aput(
            cast(Any, config),
            empty_checkpoint(),
            metadata,
            {},
        )
        loaded = await saver.aget_tuple(saved_config)
        if loaded is None:
            raise RuntimeError("checkpoint 写入后无法读回")
        await saver.adelete_thread(thread_id)
