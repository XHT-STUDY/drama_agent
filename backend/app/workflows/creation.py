"""CreationWorkflow — LangGraph 创作工作流 (C-07, F-05).

将 C-02 ~ C-06 的各 Skill 串联为完整创作流程：
normalize → retrieve → story_bible → outline → write_episodes
→ evaluate_episodes → (需修订 → select_revision → revise → continuity_check
→ re_evaluate → 循环或 finalize；否则 → finalize)

节点约束（见 DEV_PLAN §7.2）：
- State 只存 Artifact ID（大文本不存 State）
- 每节点发布 node.started / node.completed 事件
- 已完成节点重试时复用（completed_nodes 跳过）
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, Literal, TypeVar

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.observability.metrics import workflow_node_duration_seconds
from app.observability.tracing import push_node
from app.workflows.nodes import (
    continuity_check_node,
    evaluate_episodes_node,
    finalize_node,
    normalize_node,
    outline_node,
    re_evaluate_node,
    retrieve_node,
    revise_node,
    select_revision_node,
    story_bible_node,
    write_episodes_node,
)
from app.workflows.revision import (
    _should_route_after_continuity,
    _should_route_after_revision,
)
from app.workflows.state import CreationState

_NodeFn = TypeVar("_NodeFn", bound=Callable[[CreationState], Awaitable[dict[str, Any]]])


def _timed_node(node_name: str, fn: _NodeFn) -> _NodeFn:
    """节点计时包装（I-02）：记录 workflow_node_duration_seconds{node}。

    只包计时不改变语义；节点异常（如 RunCancelledError）在 finally 中
    计时后原样向上抛。
    """

    @wraps(fn)
    async def _wrapped(state: CreationState) -> dict[str, Any]:
        start = time.monotonic()
        # I-02：节点执行期间压入 tracing 上下文，LLM 埋点可读当前节点名
        with push_node(node_name):
            try:
                return await fn(state)
            finally:
                workflow_node_duration_seconds.observe(
                    time.monotonic() - start, node=node_name
                )

    return _wrapped  # type: ignore[return-value]

logger = logging.getLogger(__name__)


def _should_continue_after_normalize(state: CreationState) -> Literal["retrieve", "__end__"]:
    """normalize 后的路由决策。

    - needs_user_input → 终止（返回给用户补充信息）
    - 失败 → 终止
    - 正常 → continue to retrieve
    """
    if state.get("needs_user_input"):
        logger.info("用户输入不完整，工作流暂停")
        return "__end__"
    if state.get("status") == "failed":
        logger.warning("normalize 失败，工作流终止")
        return "__end__"
    return "retrieve"


def _should_evaluate(state: CreationState) -> Literal["evaluate_episodes", "__end__"]:
    """write_episodes 后的路由决策 (E-04)。

    剧本写完后自动进入逐集评估。
    """
    if state.get("status") == "failed":
        logger.warning("write_episodes 失败，工作流终止")
        return "__end__"
    return "evaluate_episodes"


def _should_route_after_eval(state: CreationState) -> Literal["select_revision", "finalize", "__end__"]:
    """evaluate_episodes 后的路由决策 (E-04, F-05)。

    - 存在需修订的集 → select_revision（进入自动修订分支）
    - 失败 → __end__
    - 全部通过 → finalize（Run 完成）
    """
    if state.get("needs_revision_decision"):
        logger.info("存在需修订的集，进入自动修订分支")
        return "select_revision"
    if state.get("status") == "failed":
        logger.warning("评估失败，工作流终止")
        return "__end__"
    return "finalize"


def build_creation_workflow() -> CompiledStateGraph:
    """构建 Creation Workflow 的 LangGraph 状态图。

    图结构:
        normalize
          ├─ (needs_user_input / failed) → END
          └─ (ok) → retrieve → story_bible → outline → write_episodes
               ├─ (failed) → END
               └─ (ok) → evaluate_episodes
                          ├─ (需修订) → select_revision → revise → continuity_check
                          │              ├─ (pass) → re_evaluate
                          │              │            ├─ (仍有低分且未满轮) → select_revision
                          │              │            ├─ (全部通过) → finalize → END
                          │              │            └─ (满轮仍低分 / 显著下降) → END
                          │              └─ (fail / 转人工审查) → END
                          └─ (否则) → finalize → END

    修订分支的节点注册与路由见 F-05（路由函数复用于 revision.py）。

    Returns:
        已编译的 LangGraph StateGraph
    """
    builder = StateGraph(CreationState)

    # 添加节点（I-02：统一计时包装，产出 workflow_node_duration_seconds）
    builder.add_node("normalize", _timed_node("normalize", normalize_node))
    builder.add_node("retrieve", _timed_node("retrieve", retrieve_node))
    builder.add_node("story_bible", _timed_node("story_bible", story_bible_node))
    builder.add_node("outline", _timed_node("outline", outline_node))
    builder.add_node("write_episodes", _timed_node("write_episodes", write_episodes_node))
    builder.add_node("evaluate_episodes", _timed_node("evaluate_episodes", evaluate_episodes_node))
    builder.add_node("select_revision", _timed_node("select_revision", select_revision_node))
    builder.add_node("revise", _timed_node("revise", revise_node))
    builder.add_node("continuity_check", _timed_node("continuity_check", continuity_check_node))
    builder.add_node("re_evaluate", _timed_node("re_evaluate", re_evaluate_node))
    builder.add_node("finalize", _timed_node("finalize", finalize_node))

    # 设置入口
    builder.set_entry_point("normalize")

    # 添加边
    builder.add_conditional_edges(
        "normalize",
        _should_continue_after_normalize,
        {"retrieve": "retrieve", "__end__": END},
    )
    builder.add_edge("retrieve", "story_bible")
    builder.add_edge("story_bible", "outline")
    builder.add_edge("outline", "write_episodes")
    builder.add_conditional_edges(
        "write_episodes",
        _should_evaluate,
        {"evaluate_episodes": "evaluate_episodes", "__end__": END},
    )
    builder.add_conditional_edges(
        "evaluate_episodes",
        _should_route_after_eval,
        {"select_revision": "select_revision", "finalize": "finalize", "__end__": END},
    )
    # 修订分支（F-05）
    builder.add_edge("select_revision", "revise")
    builder.add_edge("revise", "continuity_check")
    builder.add_conditional_edges(
        "continuity_check",
        _should_route_after_continuity,
        {"re_evaluate": "re_evaluate", "__end__": END},
    )
    builder.add_conditional_edges(
        "re_evaluate",
        _should_route_after_revision,
        {"select_revision": "select_revision", "finalize": "finalize", "__end__": END},
    )
    builder.add_edge("finalize", END)

    # 编译（MVP 阶段不使用 checkpointer，后续阶段添加）
    return builder.compile()


# 模块级单例（惰性构建）
_workflow: CompiledStateGraph | None = None


def get_creation_workflow() -> CompiledStateGraph:
    """获取已编译的 Creation Workflow 模块级单例。"""
    global _workflow
    if _workflow is None:
        _workflow = build_creation_workflow()
    return _workflow
