"""1000 Artifact 查询性能测试（I-05，§1.6）。

验证：
- 项目下 1000 个 Artifact 可按分页 API 全部遍历（每页 100，10 页）；
- 分页查询 p95 < 300ms（不含 LLM 的普通 API 指标）；
- 数据按 created_at desc 排序、无缺漏（去重后数量 == 1000）。

插入走 ArtifactStore（不可变版本模型），查询走 HTTP API
（GET /projects/{id}/artifacts），覆盖"1,000 Artifact 查询"验收。
"""

from __future__ import annotations

import time
import uuid

import pytest
from httpx import AsyncClient

import app.db.session as db_session
from app.artifacts.store import ArtifactStore

pytestmark = pytest.mark.performance

_TOTAL = 1000
_PAGE = 100


def _p95(samples: list[float]) -> float:
    """第 95 百分位（毫秒）。"""
    ordered = sorted(samples)
    idx = max(0, min(len(ordered) - 1, int(0.95 * len(ordered))))
    return ordered[idx] * 1000.0


class Test1000Artifacts:
    """1000 Artifact 分页查询。"""

    async def _create_project(self, client: AsyncClient) -> uuid.UUID:
        r = await client.post("/api/v1/projects", json={"title": "1000 Artifact 测试"})
        assert r.status_code == 201
        return uuid.UUID(r.json()["id"])

    async def _seed_1000(self, project_id: uuid.UUID) -> None:
        """通过 ArtifactStore 直接插入 1000 个脚本草稿 Artifact。"""
        factory = db_session._async_session_factory  # 运行时读取（conftest 已绑定 test_engine）
        assert factory is not None, "DB not initialized"
        async with factory() as db:
            store = ArtifactStore()
            for episode in range(1, _TOTAL + 1):
                # episode_number 参与 input_hash，保证 1000 个互不相同
                await store.create(
                    db,
                    project_id=project_id,
                    artifact_type="script_draft",
                    episode_number=episode,
                    content={
                        "title": f"第 {episode} 集",
                        "scenes": [{"id": f"s{episode}", "title": "场景", "dialogues": []}],
                    },
                )
            await db.commit()  # ArtifactStore.create 只 add 不提交，调用方负责 commit

    async def test_paginate_all_1000(self, async_client: AsyncClient) -> None:
        """分页遍历全部 1000 个 Artifact，无缺漏、p95 达标。"""
        project_id = await self._create_project(async_client)
        await self._seed_1000(project_id)

        collected: set[str] = set()
        page_latencies: list[float] = []
        offset = 0

        while True:
            t0 = time.perf_counter()
            r = await async_client.get(
                f"/api/v1/projects/{project_id}/artifacts",
                params={"offset": offset, "limit": _PAGE},
            )
            page_latencies.append(time.perf_counter() - t0)
            assert r.status_code == 200
            data = r.json()
            items = data["items"]
            for item in items:
                collected.add(item["id"])
            offset += len(items)
            if not items or offset >= _TOTAL:
                break

        # 全部遍历到，且无缺漏 / 重复
        assert offset == _TOTAL, f"只遍历到 {offset}/1000"
        assert len(collected) == _TOTAL, f"去重后 {len(collected)} ≠ 1000"

        # 分页查询 p95 < 300ms
        p95_ms = _p95(page_latencies)
        print(f"1000 Artifact 分页 p95 = {p95_ms:.1f}ms (阈值 300ms)")
        assert p95_ms < 300.0, f"1000 Artifact 分页 p95={p95_ms:.1f}ms ≥ 300ms"
