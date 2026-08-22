"""持久化 WorkflowRun 幂等收据、调度租约与恢复索引。

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加 durable run 字段；存在活跃冲突时显式中止。"""
    bind = op.get_bind()
    conflicts = bind.execute(
        sa.text(
            """
            SELECT project_id::text
            FROM workflow_runs
            WHERE status IN ('queued', 'running')
            GROUP BY project_id
            HAVING count(*) > 1
            ORDER BY project_id
            """
        )
    ).scalars().all()
    if conflicts:
        project_ids = ", ".join(conflicts)
        raise RuntimeError(
            "0006 无法创建项目活跃 Run 唯一索引；"
            f"请先人工处理以下 project_id 的冲突: {project_ids}"
        )

    op.add_column(
        "workflow_runs",
        sa.Column(
            "idempotency_key",
            sa.String(128),
            nullable=True,
            comment="项目与 action 范围内的持久化幂等键",
        ),
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "request_hash",
            sa.String(64),
            nullable=True,
            comment="规范化 action/config 请求的 SHA256",
        ),
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "lease_owner",
            sa.String(100),
            nullable=True,
            comment="当前 WorkflowDispatcher 租约持有者",
        ),
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Dispatcher 租约过期时间",
        ),
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Dispatcher 领取/恢复次数",
        ),
    )
    op.create_index(
        "uq_workflow_runs_idempotency",
        "workflow_runs",
        ["project_id", "action", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "uq_workflow_runs_active_project",
        "workflow_runs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    """删除 durable run 索引与字段。"""
    op.drop_index("uq_workflow_runs_active_project", table_name="workflow_runs")
    op.drop_index("uq_workflow_runs_idempotency", table_name="workflow_runs")
    op.drop_column("workflow_runs", "attempt_count")
    op.drop_column("workflow_runs", "lease_expires_at")
    op.drop_column("workflow_runs", "lease_owner")
    op.drop_column("workflow_runs", "request_hash")
    op.drop_column("workflow_runs", "idempotency_key")
