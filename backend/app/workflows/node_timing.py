"""工作流节点计时与日志装饰器（I-02 增强）。

所有工作流（creation / revision / evaluation / import_file /
outline_revision / conversational_revision）的节点统一经 timed_node 包裹：

- 节点执行期间压入 tracing 上下文（push_node），使节点内 LLM 埋点与
  日志自动携带 node 名；
- 记录 workflow_node_duration_seconds{node} 指标；
- 输出节点开始 / 完成（含耗时）/ 异常日志，Run 全链路可观测。

只包计时与日志，不改变语义；节点异常（如 RunCancelledError）在
finally 中计时后原样向上抛。函数类型 _F 原样透传，保证 LangGraph
add_node 的 TypedDict 状态类型推断不受包装影响。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar, cast

from app.observability.metrics import workflow_node_duration_seconds
from app.observability.tracing import push_node

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Awaitable[dict[str, Any]]])


def timed_node(node_name: str, fn: _F) -> _F:
    """节点计时 + 日志包装：push_node 上下文、耗时指标、开始/完成日志。"""

    @wraps(fn)
    async def _wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        start = time.monotonic()
        # 节点执行期间压入 tracing 上下文，LLM 埋点/日志可读当前节点名
        with push_node(node_name):
            logger.info("节点开始: %s", node_name)
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                # RunCancelledError 等控制流异常同样记录后原样上抛
                logger.warning(
                    "节点异常: %s (%s: %.120s)",
                    node_name,
                    type(exc).__name__,
                    exc,
                )
                raise
            finally:
                workflow_node_duration_seconds.observe(
                    time.monotonic() - start, node=node_name
                )
            logger.info("节点完成: %s 耗时=%.1fs", node_name, time.monotonic() - start)
            return result

    return cast(_F, _wrapped)
