"""observability/tracing 单元测试（I-02）。

验证 contextvar span 上下文的压入/恢复、字段继承与任务隔离。
不依赖 DB / Redis / LLM。
"""

from __future__ import annotations

import asyncio

from app.observability.tracing import (
    clear,
    get_span,
    push_node,
    push_request,
    push_run,
)


class TestSpanContext:
    def teardown_method(self) -> None:
        clear()

    def test_empty_default(self) -> None:
        span = get_span()
        assert span.request_id is None
        assert span.run_id is None
        assert span.node_name is None

    def test_push_run_inherits_request(self) -> None:
        with push_request("req-123"), push_run("run-abc"):
            span = get_span()
            assert span.request_id == "req-123"
            assert span.run_id == "run-abc"
        # 退出后恢复
        assert get_span().run_id is None

    def test_push_node_inherits_run(self) -> None:
        with push_run("run-abc"), push_node("normalize"):
            assert get_span().node_name == "normalize"
            assert get_span().run_id == "run-abc"
        assert get_span().node_name is None

    def test_nested_push_restores_outer(self) -> None:
        with push_run("run-outer"):
            with push_node("normalize"):
                with push_node("outline"):
                    assert get_span().node_name == "outline"
                assert get_span().node_name == "normalize"
            assert get_span().node_name is None
        assert get_span().run_id is None

    def test_task_isolation(self) -> None:
        """contextvar 跨 asyncio.Task 隔离：一个任务内压入不影响另一个。"""

        async def worker(name: str) -> str | None:
            with push_node(name):
                await asyncio.sleep(0.01)
                return get_span().node_name

        async def main() -> tuple[str | None, str | None]:
            results = await asyncio.gather(worker("normalize"), worker("outline"))
            return results

        a, b = asyncio.run(main())
        assert a == "normalize"
        assert b == "outline"

    def test_clear(self) -> None:
        with push_run("run-abc"):
            clear()
            assert get_span().run_id is None
