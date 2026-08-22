"""Outline Revision Workflow — 对话式大纲修订（J-08，action=revise_outline）。

单节点图：revise_outline → END。

节点内完成 加载 source outline / Story Bible → OutlineReviserSkill →
新版本落库（invalid 诊断版本 / latest valid）→ OutlineImpactTool 影响分析。
不调用剧本生成或修订——受影响剧本只进入 follow-up 建议（J-09 决策）。
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.workflows.nodes.revise_outline import revise_outline_node
from app.workflows.state import CreationState


def build_outline_revision_workflow(
    *, checkpointer: Any | None = None
) -> CompiledStateGraph[CreationState, None, CreationState, CreationState]:
    """构建大纲修订工作流（单节点）。"""
    builder = StateGraph(CreationState)
    builder.add_node("revise_outline", revise_outline_node)
    builder.set_entry_point("revise_outline")
    builder.add_edge("revise_outline", END)
    return builder.compile(checkpointer=checkpointer)
