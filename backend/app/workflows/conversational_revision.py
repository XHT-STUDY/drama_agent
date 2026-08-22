"""Conversational Revision Workflow — 对话式剧本修订子图（J-06）。

图结构:
    prepare_target → ensure_evaluation → revise → continuity_check
        ├─ (pass) → re_evaluate → END
        └─ (fail / 转人工审查) → END

与 build_revision_workflow() 的区别:
- 目标由服务端解析的 source script ID 决定（AgentAction 确认时已做快照
  过期检测），不经 select_revision 自动选集;
- 目标剧本缺少绑定评估时先仅评估目标集（ensure_evaluation）;
- 用户约束写入 RevisionPlan（revise 节点经 user_instruction 传入）;
- 一次确认只做一轮修订：re_evaluate 后直接 END，不进入自动修订循环
  （重评仍低分由 Action lifecycle / 后续计划处理，J-09）。
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.workflows.nodes import (
    continuity_check_node,
    re_evaluate_node,
    revise_node,
)
from app.workflows.nodes.prepare_conversational_revision import (
    ensure_evaluation_node,
    prepare_target_node,
)
from app.workflows.revision import _should_route_after_continuity
from app.workflows.state import CreationState

logger = logging.getLogger(__name__)


def _route_after_re_evaluate(state: CreationState) -> Literal["__end__"]:
    """对话式修订是一次性执行：重评后直接 END（含失败/下降场景）。"""
    if state.get("status") == "failed":
        logger.warning("对话式修订重评失败，工作流终止")
    elif state.get("needs_manual_review"):
        logger.info("对话式修订重评显著下降，暂停人工复核")
    return "__end__"


def build_conversational_revision_workflow(
    *, checkpointer: Any | None = None
) -> CompiledStateGraph[CreationState, None, CreationState, CreationState]:
    """构建对话式剧本修订子图（action=revise_script）。"""
    builder = StateGraph(CreationState)

    builder.add_node("prepare_target", prepare_target_node)
    builder.add_node("ensure_evaluation", ensure_evaluation_node)
    builder.add_node("revise", revise_node)
    builder.add_node("continuity_check", continuity_check_node)
    builder.add_node("re_evaluate", re_evaluate_node)

    builder.set_entry_point("prepare_target")
    builder.add_edge("prepare_target", "ensure_evaluation")
    builder.add_edge("ensure_evaluation", "revise")
    builder.add_edge("revise", "continuity_check")
    builder.add_conditional_edges(
        "continuity_check",
        _should_route_after_continuity,
        {"re_evaluate": "re_evaluate", "__end__": END},
    )
    builder.add_conditional_edges(
        "re_evaluate",
        _route_after_re_evaluate,
        {"__end__": END},
    )

    return builder.compile(checkpointer=checkpointer)
