"""进程内 span 关联上下文（I-02）。

用 contextvar 在异步调用链上传导 request_id → run_id → node_name，
供指标埋点读取当前节点（如 llm_calls_total{node}）与诊断关联。
不引入 OpenTelemetry；MVP 仅用于关联，不做采样与跨进程传播。

用法：
    with push_run(run_id):
        with push_node("normalize"):
            ...  # 当前 span 上下文可见
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class SpanContext:
    """当前调用链的关联信息。"""

    request_id: str | None = None
    run_id: str | None = None
    node_name: str | None = None


# ContextVar 默认值不可为可变对象（B039）：用 None，get_span 时兜底
_EMPTY = SpanContext()
_span: ContextVar[SpanContext | None] = ContextVar("drama_agent_span", default=None)


def get_span() -> SpanContext:
    """读取当前 span 上下文（协程/任务隔离）。"""
    ctx = _span.get()
    return ctx if ctx is not None else _EMPTY


def clear() -> None:
    """清空当前 span（任务结束清理，避免泄漏到下一个请求）。"""
    _span.set(None)


def _derive(**overrides: str | None) -> SpanContext:
    ctx = get_span()
    return SpanContext(
        request_id=overrides.get("request_id", ctx.request_id),
        run_id=overrides.get("run_id", ctx.run_id),
        node_name=overrides.get("node_name", ctx.node_name),
    )


@contextmanager
def push_request(request_id: str) -> Iterator[SpanContext]:
    """在调用链上设置 request_id（保留既有 run/node）。"""
    token = _span.set(_derive(request_id=request_id))
    try:
        yield get_span()
    finally:
        _span.reset(token)


@contextmanager
def push_run(run_id: str) -> Iterator[SpanContext]:
    """在调用链上设置 run_id（保留既有 request/node）。"""
    token = _span.set(_derive(run_id=run_id))
    try:
        yield get_span()
    finally:
        _span.reset(token)


@contextmanager
def push_node(node_name: str) -> Iterator[SpanContext]:
    """在调用链上设置当前节点名（保留既有 request/run）。"""
    token = _span.set(_derive(node_name=node_name))
    try:
        yield get_span()
    finally:
        _span.reset(token)
