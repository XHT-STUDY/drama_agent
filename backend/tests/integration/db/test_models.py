"""B-02 ORM 模型集成测试。

验证模型的 CRUD、唯一约束和 check constraints。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.integration
@pytest.mark.asyncio
class TestProjectModel:
    """Project 模型 CRUD 与约束。"""

    async def test_create_project(self, test_session: AsyncSession) -> None:
        """可以创建 Project 并立即查询。"""
        from app.db.models.project import Project

        project = Project(title="足球少年", target_episode_count=10)
        test_session.add(project)
        await test_session.flush()

        result = await test_session.execute(select(Project).where(Project.title == "足球少年"))
        found = result.scalar_one()
        assert found.target_episode_count == 10
        assert found.status == "draft"
        assert found.id is not None

    async def test_project_defaults(self, test_session: AsyncSession) -> None:
        """Project 默认值正确。"""
        from app.db.models.project import Project

        project = Project()
        test_session.add(project)
        await test_session.flush()

        assert project.title == ""
        assert project.status == "draft"
        assert project.target_episode_count == 10
        assert project.current_episode_count == 0
        assert project.deleted_at is None

    async def test_project_soft_delete(self, test_session: AsyncSession) -> None:
        """Project 支持软删除（设置 deleted_at）。"""
        from datetime import UTC, datetime

        from app.db.models.project import Project

        project = Project(title="待删除项目")
        test_session.add(project)
        await test_session.flush()

        # 软删除
        project.deleted_at = datetime.now(UTC)
        await test_session.flush()

        assert project.deleted_at is not None


@pytest.mark.integration
@pytest.mark.asyncio
class TestArtifactConstraints:
    """Artifact 约束验证。"""

    async def test_artifact_unique_constraint(self, test_session: AsyncSession) -> None:
        """相同的 (project_id, type, episode_number, version) 不能重复插入。"""
        import uuid

        import pytest

        from app.db.models.artifact import Artifact

        project_id = uuid.uuid4()

        # 需要先有 project
        from app.db.models.project import Project

        proj = Project(id=project_id, title="测试项目")
        test_session.add(proj)
        await test_session.flush()

        a1 = Artifact(
            project_id=project_id,
            type="script_draft",
            episode_number=1,
            version=1,
            content={"scenes": []},
        )
        test_session.add(a1)
        await test_session.flush()

        a2 = Artifact(
            project_id=project_id,
            type="script_draft",
            episode_number=1,
            version=1,
            content={"scenes": []},
        )
        test_session.add(a2)

        # 唯一约束冲突
        with pytest.raises(Exception):  # noqa: B017
            await test_session.flush()

    async def test_artifact_version_positive(self, test_session: AsyncSession) -> None:
        """version <= 0 应违反 check constraint。"""
        import uuid

        import pytest

        from app.db.models.artifact import Artifact
        from app.db.models.project import Project

        project_id = uuid.uuid4()
        test_session.add(Project(id=project_id))
        await test_session.flush()

        # version=0 违反了 ck_artifacts_version_positive
        a = Artifact(
            project_id=project_id,
            type="script_draft",
            episode_number=1,
            version=0,
            content={},
        )
        test_session.add(a)
        with pytest.raises(Exception):  # noqa: B017
            await test_session.flush()

    async def test_artifact_episode_positive(self, test_session: AsyncSession) -> None:
        """episode_number < 1 应违反 check constraint。"""
        import uuid

        import pytest

        from app.db.models.artifact import Artifact
        from app.db.models.project import Project

        project_id = uuid.uuid4()
        test_session.add(Project(id=project_id))
        await test_session.flush()

        a = Artifact(
            project_id=project_id,
            type="script_draft",
            episode_number=0,
            version=1,
            content={},
        )
        test_session.add(a)
        with pytest.raises(Exception):  # noqa: B017
            await test_session.flush()


@pytest.mark.integration
@pytest.mark.asyncio
class TestWorkflowEventUnique:
    """WorkflowEvent 约束验证。"""

    async def test_workflow_event_unique_run_sequence(self, test_session: AsyncSession) -> None:
        """相同的 (run_id, sequence) 不能重复插入。"""
        import uuid

        import pytest

        from app.db.models.project import Project
        from app.db.models.workflow_event import WorkflowEvent
        from app.db.models.workflow_run import WorkflowRun

        project_id = uuid.uuid4()
        run_id = uuid.uuid4()

        test_session.add(Project(id=project_id))
        test_session.add(WorkflowRun(id=run_id, project_id=project_id, action="test"))
        await test_session.flush()

        e1 = WorkflowEvent(run_id=run_id, sequence=1, type="node.started")
        test_session.add(e1)
        await test_session.flush()

        e2 = WorkflowEvent(run_id=run_id, sequence=1, type="node.completed")
        test_session.add(e2)
        with pytest.raises(Exception):  # noqa: B017
            await test_session.flush()


@pytest.mark.integration
@pytest.mark.asyncio
class TestArtifactLinkConstraints:
    """ArtifactLink 检查约束。"""

    async def test_artifact_link_no_self_ref(self, test_session: AsyncSession) -> None:
        """source_id == target_id 应违反 check constraint。"""
        import uuid

        import pytest

        from app.db.models.artifact import Artifact
        from app.db.models.artifact_link import ArtifactLink
        from app.db.models.project import Project

        project_id = uuid.uuid4()
        artifact_id = uuid.uuid4()

        test_session.add(Project(id=project_id))
        await test_session.flush()
        test_session.add(Artifact(id=artifact_id, project_id=project_id, type="script_draft", content={}))
        await test_session.flush()

        link = ArtifactLink(source_id=artifact_id, target_id=artifact_id, relation="self")
        test_session.add(link)
        with pytest.raises(Exception):  # noqa: B017
            await test_session.flush()
