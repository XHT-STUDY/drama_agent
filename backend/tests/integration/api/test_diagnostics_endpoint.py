"""I-02 GET /runs/{id}/diagnostics 运行诊断契约测试。

覆盖（I-02 验收 #1/#2）：
- 节点时间线：node.started → node.completed/failed 的耗时与终态
- LLM 统计：run.llm_stats 事件的 calls / prompt_tokens / completion_tokens
- 失败信息：run.failed 事件的 error_code（error_node 回退最近 node.failed）
- 未匹配任何事件的节点：status=started、duration_ms=None
- run_id 不存在 → 404 RUN_NOT_FOUND

全部使用 FakeLLM（APP_ENV=test），无真实 LLM 调用。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient


async def _seed_run_and_events(
    *,
    with_llm_stats: bool = True,
) -> tuple[str, str]:
    """在测试 DB 创建 Project + Run + 事件序列，返回 (project_id, run_id)。

    事件时间线（相对 now）：
      normalize   started at -4s → completed at -2s   (2000ms)
      outline     started at -2s                      (无终态 → status=started)
      write_episodes started -2s → failed at -1s      (1000ms, 节点失败)
      run.llm_stats（可选）
      run.failed（error_code=RUN_BUDGET_EXCEEDED）
    """
    from datetime import datetime as dt

    from app.db.models.project import Project
    from app.db.models.workflow_event import WorkflowEvent
    from app.db.models.workflow_run import WorkflowRun
    from app.db.session import _async_session_factory

    now = dt.now(UTC)
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()

    def _event(sequence: int, etype: str, payload: dict[str, Any], at: datetime) -> WorkflowEvent:
        return WorkflowEvent(
            id=uuid.uuid4(), run_id=run_id, sequence=sequence,
            type=etype, payload=payload, created_at=at,
        )

    events = [
        _event(1, "node.started", {"node": "normalize", "progress": 0.1}, now - timedelta(seconds=4)),
        _event(2, "node.completed", {"node": "normalize", "progress": 0.2}, now - timedelta(seconds=2)),
        _event(3, "node.started", {"node": "outline", "progress": 0.3}, now - timedelta(seconds=2)),
        _event(4, "node.started", {"node": "write_episodes", "progress": 0.4}, now - timedelta(seconds=2)),
        _event(5, "node.failed", {"node": "write_episodes", "error": "预算超限"}, now - timedelta(seconds=1)),
    ]
    if with_llm_stats:
        events.append(
            _event(6, "run.llm_stats",
                   {"calls": 5, "prompt_tokens": 1200, "completion_tokens": 800},
                   now - timedelta(seconds=1))
        )
    events.append(
        _event(len(events) + 1, "run.failed",
               {"error": "预算超限", "error_code": "RUN_BUDGET_EXCEEDED"},
               now - timedelta(seconds=1))
    )

    async with _async_session_factory() as db:
        db.add(Project(id=project_id, title="I-02 诊断测试", status="draft"))
        await db.flush()
        db.add(
            WorkflowRun(
                id=run_id, project_id=project_id, action="create_script",
                status="failed",
            )
        )
        for ev in events:
            db.add(ev)
        await db.commit()
    return str(project_id), str(run_id)


@pytest.mark.integration
@pytest.mark.asyncio
class TestDiagnosticsEndpoint:
    async def test_diagnostics_node_timeline(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """节点时间线：耗时 / 终态 / 未完成节点 status=started。"""
        _, run_id = await _seed_run_and_events()
        resp = await async_client.get(f"/api/v1/runs/{run_id}/diagnostics")
        assert resp.status_code == 200
        data = resp.json()

        assert data["run_id"] == run_id
        assert data["status"] == "failed"
        assert data["total_duration_ms"] is not None

        nodes = {n["node_name"]: n for n in data["nodes"]}
        assert nodes["normalize"]["status"] == "completed"
        assert 1000 <= nodes["normalize"]["duration_ms"] <= 3000  # ≈2000ms
        assert nodes["write_episodes"]["status"] == "failed"
        assert 500 <= nodes["write_episodes"]["duration_ms"] <= 1500  # ≈1000ms
        # outline 只有 started 无终态
        assert nodes["outline"]["status"] == "started"
        assert nodes["outline"]["duration_ms"] is None

    async def test_diagnostics_llm_stats_and_errors(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """LLM 统计 + 失败信息（error_code + 回退的失败节点名）。"""
        _, run_id = await _seed_run_and_events()
        resp = await async_client.get(f"/api/v1/runs/{run_id}/diagnostics")
        assert resp.status_code == 200
        data = resp.json()

        assert data["llm_calls"] == 5
        assert data["llm_tokens"] == {"prompt": 1200, "completion": 800}

        assert len(data["errors"]) == 1
        err = data["errors"][0]
        assert err["error_code"] == "RUN_BUDGET_EXCEEDED"
        # run.failed 无 error_node → 回退最近 node.failed 的 write_episodes
        assert err["node_name"] == "write_episodes"

    async def test_diagnostics_without_llm_stats(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """无 run.llm_stats 事件 → llm_calls / llm_tokens 为 None。"""
        _, run_id = await _seed_run_and_events(with_llm_stats=False)
        resp = await async_client.get(f"/api/v1/runs/{run_id}/diagnostics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["llm_calls"] is None
        assert data["llm_tokens"] is None

    async def test_diagnostics_run_not_found(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """run_id 不存在 → 404 RUN_NOT_FOUND。"""
        resp = await async_client.get(
            f"/api/v1/runs/{uuid.uuid4()}/diagnostics"
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "RUN_NOT_FOUND"
