"""普通 API 延迟性能测试（I-05，§1.6：不含 LLM 的普通 API p95 < 300ms）。

目标端点均为确定性、无 LLM 的普通 API：
- GET /health/ready：PostgreSQL + Redis 连通性检查（代表 DB 读）
- GET /api/v1/projects：空库列表查询（代表 DB 读 + 序列化）
- POST /api/v1/projects：单行写入（代表 DB 写）

测试方法：
1. 预热若干请求（排除连接初始化 / session 工厂冷启动）；
2. 连续测量 N 次请求耗时；
3. 计算 p95，断言 < 300ms。

注意：本文件所有测试标记 performance，默认 pytest 跳过（addopts），
`make perf` 显式运行。阈值遵循 §1.6 非功能指标。
"""

from __future__ import annotations

import time

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.performance


def _p95(samples: list[float]) -> float:
    """计算第 95 百分位（毫秒）。"""
    ordered = sorted(samples)
    idx = max(0, min(len(ordered) - 1, int(0.95 * len(ordered))))
    return ordered[idx] * 1000.0  # 秒 → 毫秒


class TestPlainAPILatency:
    """不含 LLM 的普通 API p95 门禁。"""

    async def test_health_ready_p95(self, async_client: AsyncClient) -> None:
        """GET /health/ready p95 < 300ms。"""
        for _ in range(5):
            r = await async_client.get("/api/v1/health/ready")
            assert r.status_code == 200

        samples: list[float] = []
        for _ in range(50):
            t0 = time.perf_counter()
            r = await async_client.get("/api/v1/health/ready")
            samples.append(time.perf_counter() - t0)
            assert r.status_code == 200

        p95_ms = _p95(samples)
        print(f"GET /health/ready p95 = {p95_ms:.1f}ms (阈值 300ms)")
        assert p95_ms < 300.0, f"GET /health/ready p95={p95_ms:.1f}ms ≥ 300ms"

    async def test_list_projects_p95(self, async_client: AsyncClient) -> None:
        """GET /api/v1/projects（DB 读 + 序列化）p95 < 300ms。"""
        for _ in range(5):
            r = await async_client.get("/api/v1/projects")
            assert r.status_code == 200

        samples: list[float] = []
        for _ in range(50):
            t0 = time.perf_counter()
            r = await async_client.get("/api/v1/projects")
            samples.append(time.perf_counter() - t0)
            assert r.status_code == 200

        p95_ms = _p95(samples)
        print(f"GET /projects p95 = {p95_ms:.1f}ms (阈值 300ms)")
        assert p95_ms < 300.0, f"GET /projects p95={p95_ms:.1f}ms ≥ 300ms"

    async def test_create_project_p95(self, async_client: AsyncClient) -> None:
        """POST /api/v1/projects（DB 写）p95 < 300ms。"""
        for _ in range(5):
            r = await async_client.post("/api/v1/projects", json={"title": "预热"})
            assert r.status_code == 201

        samples: list[float] = []
        for _ in range(50):
            t0 = time.perf_counter()
            r = await async_client.post(
                "/api/v1/projects", json={"title": "延迟测量"}
            )
            samples.append(time.perf_counter() - t0)
            assert r.status_code == 201

        p95_ms = _p95(samples)
        print(f"POST /projects p95 = {p95_ms:.1f}ms (阈值 300ms)")
        assert p95_ms < 300.0, f"POST /projects p95={p95_ms:.1f}ms ≥ 300ms"
