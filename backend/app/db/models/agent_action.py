"""AgentAction ORM 模型。

只有需要确认的 plan 分支创建 Action；它保存来源快照、执行状态和结果审计。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin


class AgentAction(Base, UUIDMixin):
    """等待确认或正在执行的受约束 Agent 动作。"""

    __tablename__ = "agent_actions"
    __table_args__ = (
        UniqueConstraint("agent_turn_id", name="uq_agent_actions_agent_turn_id"),
        UniqueConstraint("run_id", name="uq_agent_actions_run_id"),
        UniqueConstraint(
            "parent_action_id",
            "replan_depth",
            name="uq_agent_actions_parent_replan_depth",
        ),
        CheckConstraint(
            "replan_depth IN (0, 1)",
            name="ck_agent_actions_replan_depth",
        ),
        CheckConstraint(
            "(replan_depth = 0 AND parent_action_id IS NULL) OR "
            "(replan_depth = 1 AND parent_action_id IS NOT NULL)",
            name="ck_agent_actions_parent_depth",
        ),
        CheckConstraint(
            "status IN ('proposed', 'queued', 'running', 'completed', "
            "'needs_review', 'failed', 'cancelled', 'stale', 'rejected')",
            name="ck_agent_actions_status",
        ),
        CheckConstraint(
            "intent IN ('create_script', 'explain', 'revise_outline', 'revise_script', 'evaluate')",
            name="ck_agent_actions_intent",
        ),
        Index("ix_agent_actions_project_status", "project_id", "status"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"),
        comment="所属项目 ID",
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT"),
        index=True,
        comment="所属会话 ID",
    )
    agent_turn_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_turns.id", ondelete="RESTRICT"),
        comment="创建本计划的 AgentTurn ID",
    )
    parent_action_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_actions.id", ondelete="RESTRICT"),
        default=None,
        comment="一次后续计划的父 Action ID",
    )
    replan_depth: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="再规划深度，只允许 0 或 1",
    )
    intent: Mapped[str] = mapped_column(
        String(32),
        comment="白名单 Agent 意图",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="proposed",
        comment="Action 状态",
    )
    requires_confirmation: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        comment="是否需要用户确认",
    )
    plan: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        comment="服务端持久化的结构化 Action Plan",
    )
    source_artifact_ids: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        default=list,
        comment="来源 Artifact 的版本和 checksum 快照",
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        default=None,
        comment="结构化 AgentOutcome",
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        default=None,
        comment="确认后创建的 WorkflowRun ID",
    )

    def __repr__(self) -> str:
        return f"<AgentAction id={self.id!s} intent={self.intent!r} status={self.status!r}>"
