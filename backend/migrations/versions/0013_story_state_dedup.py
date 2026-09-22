"""M-03 剧情派生证据幂等索引。

episode_summary / continuity_state 的 v2 派生记录以
(project, type, episode, input_hash) 幂等——相同输入(源稿/前态/Prompt
版本/摘要 digest)只允许一份有效派生。应用层已按 input_hash 查询去重
(ArtifactStore D-05);本索引在数据库层兜底并发竞争:
同输入并发插入时唯一约束使后到者失败,由调用方按 input_hash 重读复用。

索引只覆盖 v2 派生(content_schema_version='2.0' 且 input_hash 非空),
不触及历史 v1 记录——旧数据可能存在重复 input_hash 为空的行,
不能因索引阻断升级(W3-02 迁移原则)。

downgrade:直接删除索引,不删除任何派生记录——降级不得让已存在的
v2 内容不可读。

Revision ID: 0013
Revises: 0011
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "uq_story_state_v2_dedup"
_WHERE = sa.text(
    "type IN ('episode_summary', 'continuity_state') "
    "AND content_schema_version = '2.0' "
    "AND input_hash IS NOT NULL"
)


def upgrade() -> None:
    op.create_index(
        _INDEX_NAME,
        "artifacts",
        ["project_id", "type", "episode_number", "input_hash"],
        unique=True,
        postgresql_where=_WHERE,
    )


def downgrade() -> None:
    # 只删索引不删数据:已存在的 v2 派生记录保持可读
    op.drop_index(_INDEX_NAME, "artifacts")
