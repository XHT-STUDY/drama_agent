"""Task 5：数据库驱动的 WorkflowDispatcher 恢复与竞争测试。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.run_service import RunService
from app.application.workflow_dispatcher import WorkflowDispatcher
from app.core.errors import AppError
from app.db.models.project import Project
from app.db.models.workflow_run import WorkflowRun


@pytest.mark.integration
@pytest.mark.asyncio
class TestWorkflowDispatcherRecovery:
    async def _queued_run(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        status: str = "queued",
        lease_expires_at: datetime | None = None,
    ) -> WorkflowRun:
        async with factory() as db, db.begin():
            project = Project(title=f"dispatcher-{uuid.uuid4()}")
            db.add(project)
            await db.flush()
            run = WorkflowRun(
                project_id=project.id,
                action="platform_smoke",
                status=status,
                config_snapshot={},
                lease_owner="dead-worker" if status == "running" else None,
                lease_expires_at=lease_expires_at,
            )
            db.add(run)
            await db.flush()
            run_id = run.id
        async with factory() as db:
            return await RunService().get_run(db, run_id)

    async def test_queued_run_is_claimed_after_service_restart(self, test_engine: Any) -> None:
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        run = await self._queued_run(factory)
        restarted = WorkflowDispatcher(factory, owner="restarted", executor=lambda *_: asyncio.sleep(0))

        claimed = await restarted.claim_next()

        assert claimed is not None
        assert claimed.id == run.id
        assert claimed.status == "running"
        assert claimed.lease_owner == "restarted"
        assert claimed.attempt_count == 1

    async def test_expired_running_lease_resumes_from_last_checkpoint(self, test_engine: Any) -> None:
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        run = await self._queued_run(
            factory,
            status="running",
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        async with factory() as db, db.begin():
            persisted = await RunService().get_run(db, run.id)
            persisted.state_summary = {"completed_nodes": ["normalize_requirement"]}

        restarted = WorkflowDispatcher(factory, owner="recovery", executor=lambda *_: asyncio.sleep(0))
        claimed = await restarted.claim_next()

        assert claimed is not None
        assert claimed.id == run.id
        assert claimed.state_summary == {"completed_nodes": ["normalize_requirement"]}
        assert claimed.attempt_count == 1

    async def test_two_dispatchers_cannot_claim_same_run(self, test_engine: Any) -> None:
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        run = await self._queued_run(factory)
        first = WorkflowDispatcher(factory, owner="one", executor=lambda *_: asyncio.sleep(0))
        second = WorkflowDispatcher(factory, owner="two", executor=lambda *_: asyncio.sleep(0))

        claims = await asyncio.gather(first.claim_next(), second.claim_next())

        claimed = [item for item in claims if item is not None]
        assert len(claimed) == 1
        assert claimed[0].id == run.id

    async def test_old_lease_owner_cannot_write_terminal_status(self, test_engine: Any) -> None:
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        run = await self._queued_run(factory)
        first = WorkflowDispatcher(factory, owner="one", executor=lambda *_: asyncio.sleep(0))
        claimed = await first.claim_next()
        assert claimed is not None

        async with factory() as db, db.begin():
            persisted = await RunService().get_run(db, run.id)
            persisted.lease_owner = "two"

        async with factory() as db, db.begin():
            with pytest.raises(AppError) as exc_info:
                await RunService().transition_status(
                    db,
                    run.id,
                    "completed",
                    lease_owner="one",
                )
        assert exc_info.value.code == "WORKFLOW_LEASE_LOST"

        async with factory() as db:
            persisted = await RunService().get_run(db, run.id)
            assert persisted.status == "running"
            assert persisted.lease_owner == "two"
