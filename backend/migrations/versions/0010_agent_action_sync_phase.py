"""agent_actions 增加 last_synced_phase（分段创作的多幕次终态回写）。

分段创作（L-3/L-4）让一个 Action 跨越多个 Run 幕次：门上暂停（needs_review）
→ 续跑 → 终态（completed/failed）。此前 Action 只回写一次，门上消息占用
唯一结果位后续跑的结局被幂等守卫吞掉（失败对用户不可见）。
last_synced_phase 记录最后一次回写的幕次键，区分"真重入"与"新幕次"。

存量行为：列为 NULL 的旧行保持冻结语义（不重复回写），迁移零数据变更。

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PHASE_COMMENT = (
    "最后一次终态回写的 Run 幕次键；NULL = 旧代码写入的存量行"
)


def upgrade() -> None:
    op.add_column(
        "agent_actions",
        sa.Column("last_synced_phase", sa.String(64), nullable=True, comment=_PHASE_COMMENT),
    )


def downgrade() -> None:
    op.drop_column("agent_actions", "last_synced_phase")
