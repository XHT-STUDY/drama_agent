"""CreationWorkflow — LangGraph 创作工作流 (C-07).

将 C-02 ~ C-06 的各 Skill 串联为完整创作流程：
normalize → retrieve → story_bible → outline → write_episodes → finalize

节点约束（见 DEV_PLAN §7.2）：
- State 只存 Artifact ID（大文本不存 State）
- 每节点发布 node.started / node.completed 事件
- 已完成节点重试时复用（completed_nodes 跳过）
- 预留 evaluate_after_creation 分支
"""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.workflows.nodes import (
    evaluate_episodes_node,
    finalize_node,
    normalize_node,
    outline_node,
    retrieve_node,
    story_bible_node,
    write_episodes_node,
)
from app.workflows.state import CreationState

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


def _should_finalize_after_eval(state: CreationState) -> Literal["finalize", "__end__"]:
    """evaluate_episodes 后的路由决策 (E-04)。

    - 存在需修订的集 → 暂停在修订决策点（Phase F 实现实际修订）
    - 否则 → finalize（Run 完成）
    """
    if state.get("needs_revision_decision"):
        logger.info("存在需修订的集，暂停在修订决策点，等待 Phase F")
        return "__end__"
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
                          ├─ (需修订) → END（暂停在修订决策点）
                          └─ (否则) → finalize → END

    Returns:
        已编译的 LangGraph StateGraph
    """
    builder = StateGraph(CreationState)

    # 添加节点
    builder.add_node("normalize", normalize_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("story_bible", story_bible_node)
    builder.add_node("outline", outline_node)
    builder.add_node("write_episodes", write_episodes_node)
    builder.add_node("evaluate_episodes", evaluate_episodes_node)
    builder.add_node("finalize", finalize_node)

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
        _should_finalize_after_eval,
        {"finalize": "finalize", "__end__": END},
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
