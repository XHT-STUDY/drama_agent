"""agent_actions.intent 白名单加入 use_external_tool（MCP-03）。

MCP 外部工具调用意图：不新增表/字段，仅扩展 CHECK 约束与领域模型同步；
对应 WorkflowRun action=mcp_tool_call（workflow_runs.action 无 CHECK 约束，
无需迁移）。

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_INTENT_CHECK = (
    "intent IN ('create_script', 'explain', 'revise_outline', "
    "'revise_script', 'evaluate', 'continue')"
)
_NEW_INTENT_CHECK = (
    "intent IN ('create_script', 'explain', 'revise_outline', "
    "'revise_script', 'evaluate', 'continue', 'use_external_tool')"
)


def upgrade() -> None:
    op.drop_constraint("ck_agent_actions_intent", "agent_actions", type_="check")
    op.create_check_constraint(
        "ck_agent_actions_intent", "agent_actions", _NEW_INTENT_CHECK
    )


def downgrade() -> None:
    # 回滚前须先清理 use_external_tool 意图的行，否则约束重建失败
    op.execute("DELETE FROM agent_actions WHERE intent = 'use_external_tool'")
    op.drop_constraint("ck_agent_actions_intent", "agent_actions", type_="check")
    op.create_check_constraint(
        "ck_agent_actions_intent", "agent_actions", _OLD_INTENT_CHECK
    )
