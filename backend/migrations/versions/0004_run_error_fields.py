"""0004 workflow_runs 失败错误码与详情列。

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-16

I-01 验收项"所有失败有 error_code"：Run 失败时把机器可读错误码
（RUN_BUDGET_EXCEEDED / LLM_TIMEOUT 等）与错误详情持久化到
workflow_runs 表，并在 RunResponse 暴露，便于前端分支处理与排查。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加 workflow_runs.error_code / error_detail 列。"""
    op.add_column(
        "workflow_runs",
        sa.Column(
            "error_code",
            sa.String(50),
            nullable=True,
            comment="失败时的机器可读错误码（I-01）",
        ),
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "error_detail",
            sa.String(2000),
            nullable=True,
            comment="失败时的错误详情（I-01，已截断）",
        ),
    )


def downgrade() -> None:
    """撤销错误码与详情列。"""
    op.drop_column("workflow_runs", "error_detail")
    op.drop_column("workflow_runs", "error_code")
