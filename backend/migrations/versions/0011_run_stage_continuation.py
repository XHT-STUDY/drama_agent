"""W1-01 分批续跑：workflow_runs.stage_generation + agent_actions.run_id 部分唯一索引。

两个问题：
1. 每次领取都累加 attempt_count（上限 3），合法的门确认续跑与故障重试
   共享同一预算——四批续写必然耗尽。stage_generation 只在合法门继续时
   递增，续跑入队时 attempt 置零，批次推进与故障重试分账。
2. agent_actions.run_id 全局唯一阻止 continue 审计 Action 关联同一 Run
   （聊天 continue 意图确认在 IntegrityError 上不可用）。改为"非 continue
   Action 每 Run 至多一个"的部分唯一索引，continue Action 可关联原 Run。

downgrade 拒绝破坏性回滚：已有多 Action 关联同一 Run 时恢复全局唯一约束
会失败，此时必须保留本迁移。

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column(
            "stage_generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="门确认续跑的世代号：每次合法 continue 递增，故障重试不变更",
        ),
    )
    op.drop_constraint("uq_agent_actions_run_id", "agent_actions", type_="unique")
    op.create_index(
        "uq_agent_actions_run_id_owner",
        "agent_actions",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("run_id IS NOT NULL AND intent <> 'continue'"),
    )


def downgrade() -> None:
    # 恢复全局唯一约束前必须确认没有 Run 被多个 Action（含 continue 审计
    # Action）关联——存在即拒绝回滚，保留 heads 语义优于丢历史关联。
    conflicts = op.get_bind().execute(
        sa.text(
            "SELECT run_id FROM agent_actions "
            "WHERE run_id IS NOT NULL "
            "GROUP BY run_id HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if conflicts:
        raise RuntimeError(
            f"存在 {len(conflicts)} 个 Run 被多个 AgentAction 关联，"
            "恢复全局唯一约束会丢失 continue 审计关联；拒绝回滚"
        )
    op.drop_index("uq_agent_actions_run_id_owner", table_name="agent_actions")
    op.create_unique_constraint("uq_agent_actions_run_id", "agent_actions", ["run_id"])
    op.drop_column("workflow_runs", "stage_generation")
