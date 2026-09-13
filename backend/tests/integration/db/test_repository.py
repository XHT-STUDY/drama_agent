"""B-02 Repository 模式集成测试。

验证 BaseRepository CRUD 操作。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.project import Project
from app.db.repositories.base import BaseRepository


@pytest.mark.integration
@pytest.mark.asyncio
class TestBaseRepository:
    """BaseRepository 通用 CRUD 操作。"""

    @pytest.fixture
    def repo(self, test_session: AsyncSession) -> BaseRepository:
        """创建针对 Project 的 BaseRepository。"""
        return BaseRepository(test_session, Project)

    async def test_add_and_get(self, repo: BaseRepository, test_session: AsyncSession) -> None:
        """add 后可以通过 get 查询到实体。"""
        project = Project(title="Repo 测试项目")
        added = await repo.add(project)

        assert added.id is not None
        assert added.title == "Repo 测试项目"

        found = await repo.get(added.id)
        assert found is not None
        assert found.title == "Repo 测试项目"

    async def test_get_nonexistent_returns_none(self, repo: BaseRepository) -> None:
        """查询不存在的 ID 返回 None。"""
        result = await repo.get(uuid.uuid4())
        assert result is None

    async def test_list_pagination(self, repo: BaseRepository, test_session: AsyncSession) -> None:
        """list 支持 offset/limit 分页。"""
        for i in range(5):
            test_session.add(Project(title=f"分页项目 {i}"))
        await test_session.flush()

        page1 = await repo.list(offset=0, limit=2)
        assert len(page1) == 2

        page2 = await repo.list(offset=2, limit=2)
        assert len(page2) == 2

        page3 = await repo.list(offset=4, limit=2)
        assert len(page3) == 1

    async def test_list_with_filters(self, repo: BaseRepository, test_session: AsyncSession) -> None:
        """list 支持关键字过滤。"""
        test_session.add(Project(title="过滤测试", status="draft"))
        test_session.add(Project(title="另一个", status="planning"))
        await test_session.flush()

        results = await repo.list(status="draft")
        assert all(r.status == "draft" for r in results)

    async def test_count(self, repo: BaseRepository, test_session: AsyncSession) -> None:
        """count 返回正确数量。"""
        for _ in range(3):
            test_session.add(Project(title="计数测试"))
        await test_session.flush()

        total = await repo.count()
        assert total == 3

    async def test_update(self, repo: BaseRepository, test_session: AsyncSession) -> None:
        """update 可以修改实体字段。"""
        project = Project(title="原标题")
        test_session.add(project)
        await test_session.flush()

        # 修改标题
        found = await repo.get(project.id)
        assert found is not None
        found.title = "新标题"
        updated = await repo.update(found)

        assert updated.title == "新标题"

        # 刷新后确认
        await test_session.refresh(updated)
        assert updated.title == "新标题"

    async def test_soft_delete(self, repo: BaseRepository, test_session: AsyncSession) -> None:
        """soft_delete 设置 deleted_at 为非空。"""
        project = Project(title="软删测试")
        test_session.add(project)
        await test_session.flush()

        await repo.soft_delete(project.id)

        # 验证 deleted_at 已设置
        await test_session.refresh(project)
        assert project.deleted_at is not None


@pytest.mark.integration
@pytest.mark.asyncio
class TestInputHashProjectScopingW101:
    """W1-01：input_hash 幂等查询限定项目——无源产物（哈希载荷不含
    project_id）不允许跨项目"复用"，否则 A 项目的导入分类/会话摘要
    会被 B 项目幂等命中（跨项目数据串用）。"""

    async def test_sourceless_dedup_does_not_leak_across_projects(
        self, test_session: AsyncSession
    ) -> None:
        from app.artifacts.store import ArtifactStore
        from app.db.models.artifact import Artifact

        projects = []
        for i in range(2):
            project = Project(title=f"项目{i}", target_episode_count=3)
            test_session.add(project)
            projects.append(project)
        await test_session.flush()

        store = ArtifactStore()
        content = {"content_type": "outline", "reason": "same upload text"}
        dedup_extra = "import:同一段上传文本"  # 无源产物的幂等因子
        a1 = await store.create(
            test_session,
            project_id=projects[0].id,
            artifact_type="import_classification",
            episode_number=1,
            status="valid",
            content=content,
            dedup_extra=dedup_extra,
        )
        a2 = await store.create(
            test_session,
            project_id=projects[1].id,
            artifact_type="import_classification",
            episode_number=1,
            status="valid",
            content=content,
            dedup_extra=dedup_extra,
        )
        assert a1.id != a2.id, "同内容不同项目必须是两份 Artifact"
        assert a1.project_id == projects[0].id
        assert a2.project_id == projects[1].id

        # 项目内重复创建仍幂等命中（既有行为不变）
        a1_again = await store.create(
            test_session,
            project_id=projects[0].id,
            artifact_type="import_classification",
            episode_number=1,
            status="valid",
            content=content,
            dedup_extra=dedup_extra,
        )
        assert a1_again.id == a1.id
        await test_session.rollback()

        del Artifact  # noqa: F841 - 仅确保模型导入可用
