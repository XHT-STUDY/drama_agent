"""0003 上传文件解析元数据列。

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-16

为 uploads 表增加 G-03 解析元数据列（original_name / parse_status /
char_count / warnings）。0001 的 uploads 表仅有 project_id/path/sha256/
mime_type/size_bytes；G-03 上传 API 需要记录客户端原始名（仅展示）、
解析状态、文本字符数与解析告警。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加上传解析元数据列。"""
    op.add_column(
        "uploads",
        sa.Column(
            "original_name",
            sa.String(255),
            server_default="",
            nullable=False,
            comment="客户端原始文件名（仅展示，永不用于磁盘路径）",
        ),
    )
    op.add_column(
        "uploads",
        sa.Column(
            "parse_status",
            sa.String(20),
            server_default="parsed",
            nullable=False,
            comment="解析状态（parsed/failed）",
        ),
    )
    op.add_column(
        "uploads",
        sa.Column(
            "char_count",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
            comment="解析出的文本字符数",
        ),
    )
    op.add_column(
        "uploads",
        sa.Column(
            "warnings",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment="解析告警（不阻断入库）",
        ),
    )


def downgrade() -> None:
    """撤销上传解析元数据列。"""
    op.drop_column("uploads", "warnings")
    op.drop_column("uploads", "char_count")
    op.drop_column("uploads", "parse_status")
    op.drop_column("uploads", "original_name")
