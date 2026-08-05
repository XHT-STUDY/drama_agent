"""Repository 模式 — 通用数据访问抽象。

定义：
- Repository[T] Protocol：CRUD 接口契约
- BaseRepository：SQLAlchemy 通用实现

具体实体的 Repository（如 ProjectRepository、ArtifactRepository）
在后续任务中添加。

模块边界（DEV_PLAN §4.1）：
- repositories 只做数据持久化，不做业务评分和控制流。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

T = TypeVar("T")


# ---- Repository 协议 ----


class Repository(Protocol[T]):
    """通用 Repository 接口协议。

    用于类型标注和 mock 测试；
    具体实现通过 BaseRepository 提供。
    """

    async def get(self, id: uuid.UUID) -> T | None:
        """按主键获取单条记录。"""
        ...

    async def list(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        **filters: Any,
    ) -> list[T]:
        """分页列表查询，支持关键字过滤。"""
        ...

    async def add(self, entity: T) -> T:
        """新增一条记录并 flush。"""
        ...

    async def update(self, entity: T) -> T:
        """更新一条记录（merge 后 flush）。"""
        ...

    async def delete(self, id: uuid.UUID) -> None:
        """软删除记录（设置 deleted_at）。"""
        ...


# ---- SQLAlchemy 通用实现 ----


class BaseRepository:
    """SQLAlchemy 通用 Repository 实现。

    封装 SQLAlchemy 2.0 异步 CRUD 操作，
    子类通过覆写 _apply_filters 等方法扩展特定查询逻辑。

    Usage:
        repo = BaseRepository(session, Project)
        project = await repo.get(some_uuid)
    """

    def __init__(self, session: AsyncSession, model_class: Any) -> None:
        """初始化 Repository。

        Args:
            session: SQLAlchemy 异步会话
            model_class: 对应的 ORM 模型类
        """
        self.session = session
        self.model_class: Any = model_class

    # ---- 查询方法 ----

    async def get(self, id: uuid.UUID) -> Any | None:
        """按主键获取单条记录。

        对支持软删除的模型自动过滤 deleted_at IS NOT NULL 的记录。
        """
        stmt: Any = select(self.model_class).where(self.model_class.id == id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        order_by: Any | None = None,
        **filters: Any,
    ) -> list[Any]:
        """分页列表查询。

        使用 offset/limit 分页；
        关键字参数自动转为 WHERE 等值过滤条件。

        Args:
            offset: 偏移量
            limit: 每页数量
            order_by: 排序表达式，如 Model.created_at.desc()
            **filters: 等值过滤条件
        """
        stmt: Any = select(self.model_class)
        for key, value in filters.items():
            if hasattr(self.model_class, key):
                stmt = stmt.where(getattr(self.model_class, key) == value)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        stmt = stmt.offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, **filters: Any) -> int:
        """统计符合条件的记录数。"""
        stmt: Any = select(func.count()).select_from(self.model_class)
        for key, value in filters.items():
            if hasattr(self.model_class, key):
                stmt = stmt.where(getattr(self.model_class, key) == value)
        result = await self.session.execute(stmt)
        return result.scalar_one()  # type: ignore[no-any-return]

    # ---- 写入方法 ----

    async def add(self, entity: Any) -> Any:
        """新增一条记录。

        将实体加入会话并立即 flush，
        返回被添加的实体（此时 id 已填充）。
        """
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def update(self, entity: Any) -> Any:
        """更新一条记录。

        使用 merge 将脱管实体重新绑定到当前会话，
        然后 flush 变更。
        """
        merged = await self.session.merge(entity)
        await self.session.flush()
        return merged

    async def soft_delete(self, id: uuid.UUID) -> None:
        """软删除记录。

        仅对包含 SoftDeleteMixin 的模型有效；
        设置 deleted_at 为当前 UTC 时间。
        """
        entity = await self.get(id)
        if entity is not None and hasattr(entity, "deleted_at"):
            entity.deleted_at = datetime.now(UTC)
            await self.session.flush()
