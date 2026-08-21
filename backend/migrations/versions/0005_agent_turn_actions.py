"""J-01 AgentTurn、AgentAction 与消息审计字段。

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-21

升级增加请求级幂等收据、可确认动作审计和消息展示元数据。
降级属于 destructive 结构回滚，会永久删除 Agent 审计数据与消息元数据。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 Agent 持久化表并补齐消息并发约束。"""
    op.add_column(
        "messages",
        sa.Column(
            "kind",
            sa.String(30),
            nullable=False,
            server_default="text",
            comment="消息类型",
        ),
    )
    op.add_column(
        "messages",
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="AgentTurn、Action、Run 与 Artifact 展示引用",
        ),
    )
    op.create_unique_constraint(
        "uq_messages_conversation_sequence",
        "messages",
        ["conversation_id", "sequence"],
    )

    op.create_table(
        "agent_turns",
        sa.Column("id", postgresql.UUID(), primary_key=True, comment="UUID v4 主键"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="创建时间（UTC）",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="最后更新时间（UTC）",
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
            comment="所属项目 ID",
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(),
            sa.ForeignKey("conversations.id", ondelete="RESTRICT"),
            nullable=False,
            comment="所属会话 ID",
        ),
        sa.Column(
            "user_message_id",
            postgresql.UUID(),
            sa.ForeignKey("messages.id", ondelete="RESTRICT"),
            nullable=False,
            comment="触发本 Turn 的用户消息 ID",
        ),
        sa.Column(
            "idempotency_key",
            sa.String(128),
            nullable=False,
            comment="项目内唯一的请求幂等键",
        ),
        sa.Column(
            "request_hash",
            sa.String(64),
            nullable=False,
            comment="规范化请求载荷的 SHA256",
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="received",
            comment="Turn 状态",
        ),
        sa.Column(
            "turn_type",
            sa.String(20),
            nullable=True,
            comment="最终响应类型：clarification / answer / plan",
        ),
        sa.Column(
            "planner_output",
            postgresql.JSONB(),
            nullable=True,
            comment="结构化 Planner 输出快照",
        ),
        sa.Column(
            "response_message_id",
            postgresql.UUID(),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
            comment="最终 assistant 消息 ID",
        ),
        sa.Column(
            "planning_lease_owner",
            sa.String(100),
            nullable=True,
            comment="当前 Planner 租约持有者",
        ),
        sa.Column(
            "planning_lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Planner 租约过期时间",
        ),
        sa.Column(
            "planning_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Planner 租约领取次数",
        ),
        sa.Column(
            "error_code",
            sa.String(80),
            nullable=True,
            comment="失败时机器可读错误码",
        ),
        sa.Column(
            "error_detail",
            sa.String(2000),
            nullable=True,
            comment="失败详情（已截断）",
        ),
        sa.UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_agent_turns_project_idempotency",
        ),
        sa.UniqueConstraint(
            "user_message_id",
            name="uq_agent_turns_user_message_id",
        ),
        sa.CheckConstraint(
            "status IN ('received', 'planning', 'needs_input', 'answered', 'action_proposed', 'failed')",
            name="ck_agent_turns_status",
        ),
        sa.CheckConstraint(
            "turn_type IS NULL OR turn_type IN ('clarification', 'answer', 'plan')",
            name="ck_agent_turns_turn_type",
        ),
    )
    op.create_index(
        "ix_agent_turns_conversation_id",
        "agent_turns",
        ["conversation_id"],
    )
    op.create_index(
        "ix_agent_turns_project_status",
        "agent_turns",
        ["project_id", "status"],
    )

    op.create_table(
        "agent_actions",
        sa.Column("id", postgresql.UUID(), primary_key=True, comment="UUID v4 主键"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="创建时间（UTC）",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="最后更新时间（UTC）",
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
            comment="所属项目 ID",
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(),
            sa.ForeignKey("conversations.id", ondelete="RESTRICT"),
            nullable=False,
            comment="所属会话 ID",
        ),
        sa.Column(
            "agent_turn_id",
            postgresql.UUID(),
            sa.ForeignKey("agent_turns.id", ondelete="RESTRICT"),
            nullable=False,
            comment="创建本计划的 AgentTurn ID",
        ),
        sa.Column(
            "parent_action_id",
            postgresql.UUID(),
            sa.ForeignKey("agent_actions.id", ondelete="RESTRICT"),
            nullable=True,
            comment="一次后续计划的父 Action ID",
        ),
        sa.Column(
            "replan_depth",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="再规划深度，只允许 0 或 1",
        ),
        sa.Column(
            "intent",
            sa.String(32),
            nullable=False,
            comment="白名单 Agent 意图",
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="proposed",
            comment="Action 状态",
        ),
        sa.Column(
            "requires_confirmation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
            comment="是否需要用户确认",
        ),
        sa.Column(
            "plan",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="服务端持久化的结构化 Action Plan",
        ),
        sa.Column(
            "source_artifact_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="来源 Artifact 的版本和 checksum 快照",
        ),
        sa.Column(
            "result",
            postgresql.JSONB(),
            nullable=True,
            comment="结构化 AgentOutcome",
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(),
            sa.ForeignKey("workflow_runs.id", ondelete="SET NULL"),
            nullable=True,
            comment="确认后创建的 WorkflowRun ID",
        ),
        sa.UniqueConstraint(
            "agent_turn_id",
            name="uq_agent_actions_agent_turn_id",
        ),
        sa.UniqueConstraint("run_id", name="uq_agent_actions_run_id"),
        sa.UniqueConstraint(
            "parent_action_id",
            "replan_depth",
            name="uq_agent_actions_parent_replan_depth",
        ),
        sa.CheckConstraint(
            "replan_depth IN (0, 1)",
            name="ck_agent_actions_replan_depth",
        ),
        sa.CheckConstraint(
            "(replan_depth = 0 AND parent_action_id IS NULL) OR "
            "(replan_depth = 1 AND parent_action_id IS NOT NULL)",
            name="ck_agent_actions_parent_depth",
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'queued', 'running', 'completed', "
            "'needs_review', 'failed', 'cancelled', 'stale', 'rejected')",
            name="ck_agent_actions_status",
        ),
        sa.CheckConstraint(
            "intent IN ('create_script', 'explain', 'revise_outline', 'revise_script', 'evaluate')",
            name="ck_agent_actions_intent",
        ),
    )
    op.create_index(
        "ix_agent_actions_conversation_id",
        "agent_actions",
        ["conversation_id"],
    )
    op.create_index(
        "ix_agent_actions_project_status",
        "agent_actions",
        ["project_id", "status"],
    )


def downgrade() -> None:
    """Destructive：删除 Agent 审计表和消息元数据，数据不可恢复。"""
    op.drop_table("agent_actions")
    op.drop_table("agent_turns")
    op.drop_constraint(
        "uq_messages_conversation_sequence",
        "messages",
        type_="unique",
    )
    op.drop_column("messages", "metadata")
    op.drop_column("messages", "kind")
