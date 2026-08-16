"""100 并发 SSE 连接性能测试（I-05，§1.6：首事件延迟 < 1s）。

httpx ASGITransport 会缓冲完整响应体，无法对无限 SSE 流做逐块读取；
因此本测试把 FastAPI app 起在真实的本地 uvicorn 服务上
（127.0.0.1 随机端口，进程内，无外网），用真实网络 transport 验证：

- 100 个并发 EventSource 连接都能收到首个事件块（`: connected`），
  首块延迟 p95 < 1s；
- 全部连接关闭后 sse_connections_active gauge 回落基线（连接释放）。

设计说明：
- 使用**合成 run_id**（UUID，DB 无该 Run）：SSE 端点不做 run_id 存在性
  校验，会正常返回 `: connected` 并维持连接。这样把"连接并发处理能力"
  从"工作流执行负载"中隔离出来——若创建真实 Run，后台 Worker 会立即
  跑完整 create_script（FakeLLM 多轮调用），污染连接延迟测量并拖垮
  teardown。本测试只验证 SSE 端点自身的连接处理（I-05 验收"基础版本"）。

依赖：`make up`（PostgreSQL + Redis 已启动）。标记 performance，默认跳过。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
import uvicorn
from httpx import AsyncClient

from app.observability.metrics import sse_connections_active

pytestmark = pytest.mark.performance

_CONCURRENCY = 100


@pytest_asyncio.fixture
async def running_server(app: Any) -> AsyncGenerator[tuple[int, AsyncClient], None]:
    """在随机本地端口启动 uvicorn 服务，yield (port, client)。"""
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_level="warning", access_log=False
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())

    try:
        for _ in range(100):
            if server.started and server.servers:
                break
            await asyncio.sleep(0.05)
        assert server.started, "uvicorn 启动超时"
        port = server.servers[0].sockets[0].getsockname()[1]

        async with AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10.0) as client:
            yield port, client
    finally:
        server.should_exit = True
        # 客户端断开后，服务端 SSE 生成器（含 BaseHTTPMiddleware 响应任务）
        # 的取消是正常的；CancelledError 继承 BaseException，须按 BaseException 抑制。
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(task, timeout=15)


def _synthetic_run_id() -> uuid.UUID:
    """合成 run_id（DB 无该 Run，SSE 端点不校验存在性）。"""
    return uuid.uuid4()


class TestConcurrentSSE:
    """100 并发 SSE 连接的建立、读取与释放。"""

    async def test_100_concurrent_connections_receive_first_event(
        self, running_server: tuple[int, AsyncClient]
    ) -> None:
        """100 个并发连接都收到首个事件块，且首块延迟 p95 < 1s。"""
        _port, client = running_server
        url = f"/api/v1/runs/{_synthetic_run_id()}/events"

        async def _open_and_read_first_chunk() -> tuple[float, bytes]:
            t0 = time.perf_counter()
            async with client.stream("GET", url) as resp:
                assert resp.status_code == 200
                assert resp.headers.get("content-type", "").startswith(
                    "text/event-stream"
                )
                async for chunk in resp.aiter_bytes():
                    return time.perf_counter() - t0, chunk
                return time.perf_counter() - t0, b""

        results = await asyncio.gather(
            *[_open_and_read_first_chunk() for _ in range(_CONCURRENCY)]
        )

        for _latency, chunk in results:
            assert chunk, "SSE 连接未收到任何事件块"
            assert b"connected" in chunk, f"首块内容异常: {chunk[:80]!r}"

        latencies = sorted(lat for lat, _ in results)
        p95 = latencies[int(0.95 * (len(latencies) - 1))] * 1000.0
        print(f"100 并发 SSE 首块 p95 = {p95:.1f}ms (阈值 1000ms)")
        assert p95 < 1000.0, f"100 并发首块 p95={p95:.1f}ms ≥ 1000ms"

        # 给服务端留出撤销 SSE 生成器的收尾时间
        await asyncio.sleep(0.2)

    async def test_connections_released_after_close(
        self, running_server: tuple[int, AsyncClient]
    ) -> None:
        """全部连接关闭后 sse_connections_active 回到基线（连接释放）。"""
        _port, client = running_server
        url = f"/api/v1/runs/{_synthetic_run_id()}/events"

        baseline = sse_connections_active.get()

        async def _open_and_close() -> None:
            async with client.stream("GET", url) as resp:
                assert resp.status_code == 200
                async for _chunk in resp.aiter_bytes():
                    break  # 收到首块即关闭

        await asyncio.gather(*[_open_and_close() for _ in range(_CONCURRENCY)])

        # 给服务端留出撤销 SSE 生成器 / 关闭会话的收尾时间，避免 teardown 竞态
        await asyncio.sleep(0.2)

        for _ in range(40):
            if sse_connections_active.get() <= baseline:
                break
            await asyncio.sleep(0.05)
        assert sse_connections_active.get() <= baseline, (
            f"SSE 连接未释放: active={sse_connections_active.get()} baseline={baseline}"
        )
