"""Revision Workflow — 修订分支 (F-05).

供 creation.py 内联引用路由决策函数与节点注册，并提供独立的
build_revision_workflow()（F-06 铺垫：MVP 主流程仍内联于 creation 图）。

路由决策（确定性，不调用 LLM）:
- after continuity: fail / 已转人工审查 → END（暂停在 needs_review）;
  pass → re_evaluate（对已提升为 valid 的候选稿重评）;
- after re_evaluate: 失败 / 显著下降 → END（暂停在 needs_review）;
  仍有需修订集且轮次未满 → select_revision（下一轮自动修订）;
  已满轮仍低分 → END（暂停在 needs_review 人工复核）; 全部通过 → finalize。

注意：`completed_nodes` 是扁平列表，同一节点合法二次访问无法表达——
MAX_REVISION_ROUNDS=1 下成立；未来 MAX>1 需引入"轮次键名"（见计划风险）。
"""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core.config import Settings
from app.workflows.nodes import (
    continuity_check_node,
    re_evaluate_node,
    revise_node,
    select_revision_node,
)
from app.workflows.state import CreationState

logger = logging.getLogger(__name__)

# 自动修订最大轮数（模块级取一次；当前配置为 1）
_MAX_REVISION_ROUNDS = Settings().max_revision_rounds


def _should_route_after_select(state: CreationState) -> Literal["revise", "__end__"]:
    """select_revision 后的路由决策（F-06 独立图）。

    - revision_candidate_episode 非 None（自动选中或用户预置）→ revise；
    - 无候选（自动路径全部评估通过）→ __end__，避免空转连续性/重评。

    注意：此条件边**只**加在独立的 build_revision_workflow() 上。
    creation.py 内联主流程的 select_revision 仅在评估已判定需修订时可达，
    加条件边有把 run 卡在 running 的风险——两处刻意不对称。
    """
    if state.get("revision_candidate_episode") is None:
        logger.info("无待修订集，独立修订工作流直接结束")
        return "__end__"
    return "revise"


def _should_route_after_continuity(state: CreationState) -> Literal["re_evaluate", "__end__"]:
    """continuity_check 后的路由决策。

    - 工作流失败 / 连续性违规（已转人工审查）→ __end__（暂停在 needs_review）
    - 连续性通过 → re_evaluate（对提升为 valid 的候选稿重新评估）
    """
    if state.get("status") == "failed":
        logger.warning("连续性检查失败，工作流终止")
        return "__end__"
    if state.get("needs_manual_review"):
        logger.info("连续性检查未通过，暂停转人工复核")
        return "__end__"
    return "re_evaluate"


def _should_route_after_revision(state: CreationState) -> Literal["select_revision", "finalize", "__end__"]:
    """re_evaluate 后的路由决策。

    - 工作流失败 / 修订后显著下降（转人工审查）→ __end__
    - 仍有需修订集且轮次未满 → select_revision（下一轮自动修订）
    - 仍有需修订集但已满轮 → __end__（暂停在 needs_review 人工复核）
    - 全部通过 → finalize
    """
    if state.get("status") == "failed":
        logger.warning("重评失败，工作流终止")
        return "__end__"
    if state.get("needs_manual_review"):
        logger.info("修订后显著下降，暂停转人工复核")
        return "__end__"
    if state.get("needs_revision_decision"):
        round_no = state.get("revision_round", 0)
        if round_no < _MAX_REVISION_ROUNDS:
            logger.info(
                "仍存在需修订集（round=%d < max=%d），进入下一轮自动修订",
                round_no, _MAX_REVISION_ROUNDS,
            )
            return "select_revision"
        logger.info(
            "修订轮次已用满（round=%d >= max=%d），暂停人工复核",
            round_no, _MAX_REVISION_ROUNDS,
        )
        return "__end__"
    return "finalize"


def build_revision_workflow() -> CompiledStateGraph[CreationState, None, CreationState, CreationState]:
    """构建独立的修订工作流（F-05）。

    图结构:
        select_revision → revise → continuity_check
            ├─ (pass) → re_evaluate
            │            ├─ (仍有低分且未满轮) → select_revision（下一轮）
            │            ├─ (全部通过) → END（"finalize" 映射）
            │            └─ (满轮仍低分 / 显著下降) → END（暂停人工复核）
            └─ (fail / 转人工审查) → END

    独立图用于 F-06 的独立修订能力；MVP 主流程仍内联于 creation.py
    （creation.py 复用上面的两个路由函数，避免环形 import）。
    """
    builder = StateGraph(CreationState)

    builder.add_node("select_revision", select_revision_node)
    builder.add_node("revise", revise_node)
    builder.add_node("continuity_check", continuity_check_node)
    builder.add_node("re_evaluate", re_evaluate_node)

    builder.set_entry_point("select_revision")

    # F-06：自动路径无候选（全部评估通过）时直接 END，避免空转
    builder.add_conditional_edges(
        "select_revision",
        _should_route_after_select,
        {"revise": "revise", "__end__": END},
    )
    builder.add_edge("revise", "continuity_check")
    builder.add_conditional_edges(
        "continuity_check",
        _should_route_after_continuity,
        {"re_evaluate": "re_evaluate", "__end__": END},
    )
    builder.add_conditional_edges(
        "re_evaluate",
        _should_route_after_revision,
        {"select_revision": "select_revision", "finalize": END, "__end__": END},
    )

    return builder.compile()
