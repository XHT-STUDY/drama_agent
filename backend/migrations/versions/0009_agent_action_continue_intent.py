"""agent_actions.intent 白名单加入 continue（对话式确认门续跑，B）。

continue 意图不创建新 Run，而是恢复停在 stage_gate 的既有 Run；
模型约束（AgentIntent）与 ck_agent_actions_intent 同步扩展。

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_INTENT_CHECK = (
    "intent IN ('create_script', 'explain', 'revise_outline', "
    "'revise_script', 'evaluate')"
)
_NEW_INTENT_CHECK = (
    "intent IN ('create_script', 'explain', 'revise_outline', "
    "'revise_script', 'evaluate', 'continue')"
)


def upgrade() -> None:
    op.drop_constraint("ck_agent_actions_intent", "agent_actions", type_="check")
    op.create_check_constraint(
        "ck_agent_actions_intent", "agent_actions", _NEW_INTENT_CHECK
    )


def downgrade() -> None:
    # 回滚前须先清理 continue 意图的行，否则约束重建失败
    op.execute("DELETE FROM agent_actions WHERE intent = 'continue'")
    op.drop_constraint("ck_agent_actions_intent", "agent_actions", type_="check")
    op.create_check_constraint(
        "ck_agent_actions_intent", "agent_actions", _OLD_INTENT_CHECK
    )
