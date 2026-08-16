"""I-02 GET /metrics 运维端点契约测试。

覆盖：
- metrics_enabled=true（默认）：200 + Prometheus 文本格式，含关键指标 TYPE/HELP
- metrics_enabled=false：404（埋点仍累积，便于按需开启）
- 指标渲染不含高基数标签（run_id/project_id 不入输出）

全部使用 FakeLLM（APP_ENV=test），无真实 LLM 调用。
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.observability.metrics import (
    artifact_created_total,
    registry,
    workflow_runs_total,
)


@pytest.mark.integration
@pytest.mark.asyncio
class TestMetricsEndpoint:
    async def test_metrics_returns_prometheus_text(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """默认开启：200 + Prometheus 文本格式 + 关键指标类型存在。"""
        registry.reset()
        workflow_runs_total.inc(action="create_script", status="queued")
        artifact_created_total.inc(artifact_type="script")
        try:
            resp = await async_client.get("/metrics")
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/plain")
            body = resp.text
            assert "# TYPE workflow_runs_total counter" in body
            assert "# TYPE artifact_created_total counter" in body
            assert '# HELP workflow_runs_total' in body
            # 渲染出的实际标签值（含计数）
            assert 'workflow_runs_total{action="create_script",status="queued"} 1' in body
            assert 'artifact_created_total{artifact_type="script"} 1' in body
        finally:
            registry.reset()

    async def test_metrics_no_high_cardinality_labels(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """输出不得包含高基数标签 run_id / project_id（指标爆炸防护）。"""
        registry.reset()
        try:
            resp = await async_client.get("/metrics")
            assert resp.status_code == 200
            assert "run_id" not in resp.text
            assert "project_id" not in resp.text
        finally:
            registry.reset()

    async def test_metrics_disabled_returns_404(
        self, app: Any, async_client: AsyncClient
    ) -> None:
        """metrics_enabled=false → 404（埋点仍累积，不阻塞主流程）。"""
        registry.reset()
        try:
            app.state.settings.metrics_enabled = False
            resp = await async_client.get("/metrics")
            assert resp.status_code == 404
        finally:
            app.state.settings.metrics_enabled = True
            registry.reset()
