"""B-02 数据库会话集成测试。

验证 AsyncSession 生命周期：创建、提交、回滚。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.integration
@pytest.mark.asyncio
class TestAsyncSession:
    """异步会话基本操作。"""

    async def test_session_can_execute_select(self, test_session: AsyncSession) -> None:
        """会话可以执行简单的 SELECT 语句。"""
        result = await test_session.execute(text("SELECT 1 AS num"))
        row = result.one()
        assert row.num == 1

    async def test_session_can_insert_and_query(self, test_session: AsyncSession) -> None:
        """会话可以插入数据并查询。"""
        from app.db.models.project import Project

        project = Project(title="测试项目")
        test_session.add(project)
        await test_session.flush()

        # 查询验证
        from sqlalchemy import select

        result = await test_session.execute(
            select(Project).where(Project.title == "测试项目")
        )
        found = result.scalar_one()
        assert found.title == "测试项目"
        assert found.id is not None

    async def test_session_rollback_on_error(self, test_session: AsyncSession) -> None:
        """会话在异常时回滚，数据不持久化。"""
        from app.db.models.project import Project

        # 先插入一条
        project = Project(title="回滚测试")
        test_session.add(project)
        await test_session.flush()

        # 验证插入可见
        from sqlalchemy import func, select

        count_result = await test_session.execute(
            select(func.count()).select_from(Project).where(Project.title == "回滚测试")
        )
        assert count_result.scalar() == 1

        # 显式回滚
        await test_session.rollback()

        # 新会话中应该看不到（因为 transaction 回滚了）
        # 但这里我们在同一 session 中，数据在 flush 后可见但在 rollback 后应不可见
        count_after = await test_session.execute(
            select(func.count()).select_from(Project).where(Project.title == "回滚测试")
        )
        assert count_after.scalar() == 0
