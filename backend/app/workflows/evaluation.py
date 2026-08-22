"""EvaluationWorkflow — LangGraph 评估工作流 (E-04).

独立评估入口（action=evaluate）：对 State 中已写集剧本进行逐集评估，
评估完成后若存在需修订的集，标记 needs_revision_decision 并暂停在修订决策点
（Phase F 实现实际修订）。
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.workflows.nodes.evaluate_episode import evaluate_episodes_node
from app.workflows.state import CreationState

logger = logging.getLogger(__name__)


def _should_pause_for_revision(state: CreationState) -> Literal["__end__"]:
    """评估完成后的路由决策（当前仅 END）。

    Phase F 将在此处加入 revise 分支。
    """
    if state.get("needs_revision_decision"):
        logger.info("存在需修订的集，暂停在修订决策点")
    return "__end__"


def build_evaluation_workflow(
    *, checkpointer: Any | None = None
) -> CompiledStateGraph[CreationState]:
    """构建评估工作流的 LangGraph 状态图。

    图结构:
        evaluate_episodes
          └─ (有低分集 → 修订决策点暂停；否则 END)

    Returns:
        已编译的 LangGraph StateGraph
    """
    builder = StateGraph(CreationState)

    builder.add_node("evaluate_episodes", evaluate_episodes_node)
    builder.set_entry_point("evaluate_episodes")
    builder.add_conditional_edges(
        "evaluate_episodes",
        _should_pause_for_revision,
        {"__end__": END},
    )

    return builder.compile(checkpointer=checkpointer)


# 模块级单例（惰性构建）
_workflow: CompiledStateGraph[CreationState] | None = None


def get_evaluation_workflow() -> CompiledStateGraph[CreationState]:
    """获取已编译的 Evaluation Workflow 模块级单例。"""
    global _workflow
    if _workflow is None:
        _workflow = build_evaluation_workflow()
    return _workflow
