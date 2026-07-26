"""初始迁移：创建全部基础表 + pgvector 扩展。

Revision ID: 0001
Revises: None
Create Date: 2026-07-23

对应 DEV_PLAN §6.1 表清单：
- projects, conversations, messages
- workflow_runs, workflow_events
- artifacts, artifact_links
- uploads
- knowledge_documents, knowledge_chunks
- llm_calls
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建所有基础表。"""
    # ---- pgvector 扩展 ----
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ---- projects ----
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(), primary_key=True, comment="UUID v4 主键"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="创建时间（UTC）"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="最后更新时间（UTC）"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True, comment="软删除时间（UTC），NULL 表示未删除"),
        sa.Column("title", sa.String(200), server_default="", nullable=False, comment="项目标题"),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False, comment="项目状态"),
        sa.Column("target_episode_count", sa.Integer(), server_default="10", nullable=False, comment="目标总集数"),
        sa.Column("current_episode_count", sa.Integer(), server_default="0", nullable=False, comment="当前已完成集数"),
    )

    # ---- conversations ----
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(), primary_key=True, comment="UUID v4 主键"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="创建时间（UTC）"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="最后更新时间（UTC）"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True, comment="软删除时间（UTC），NULL 表示未删除"),
        sa.Column("project_id", postgresql.UUID(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, comment="所属项目 ID"),
        sa.Column("title", sa.String(200), server_default="", nullable=False, comment="会话标题"),
    )
    op.create_index("ix_conversations_project_id", "conversations", ["project_id"])

    # ---- messages ----
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(), primary_key=True, comment="UUID v4 主键"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="创建时间（UTC）"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="最后更新时间（UTC）"),
        sa.Column("conversation_id", postgresql.UUID(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, comment="所属会话 ID"),
        sa.Column("role", sa.String(20), nullable=False, comment="消息角色"),
        sa.Column("content", sa.Text(), server_default="", nullable=False, comment="消息正文"),
        sa.Column("sequence", sa.Integer(), server_default="0", nullable=False, comment="消息序号"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_conversation_created", "messages", ["conversation_id", "created_at"])

    # ---- workflow_runs ----
    op.create_table(
        "workflow_runs",
        sa.Column("id", postgresql.UUID(), primary_key=True, comment="UUID v4 主键"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="创建时间（UTC）"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="最后更新时间（UTC）"),
        sa.Column("project_id", postgresql.UUID(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, comment="所属项目 ID"),
        sa.Column("action", sa.String(50), nullable=False, comment="执行动作"),
        sa.Column("status", sa.String(20), server_default="queued", nullable=False, comment="运行状态"),
        sa.Column("state_summary", postgresql.JSONB(), nullable=True, comment="LangGraph State 摘要"),
        sa.Column("config_snapshot", postgresql.JSONB(), nullable=True, comment="Run 创建时的配置快照"),
    )
    op.create_index("ix_workflow_runs_project_id", "workflow_runs", ["project_id"])
    op.create_index("ix_workflow_runs_project_created", "workflow_runs", ["project_id", sa.text("created_at DESC")])

    # ---- workflow_events ----
    op.create_table(
        "workflow_events",
        sa.Column("id", postgresql.UUID(), primary_key=True, comment="UUID v4 主键"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="创建时间（UTC）"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="最后更新时间（UTC）"),
        sa.Column("run_id", postgresql.UUID(), sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, comment="所属 Run ID"),
        sa.Column("sequence", sa.Integer(), nullable=False, comment="事件在 Run 内的递增序号"),
        sa.Column("type", sa.String(50), nullable=False, comment="事件类型"),
        sa.Column("payload", postgresql.JSONB(), nullable=True, comment="事件负载"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_workflow_events_run_sequence"),
    )
    op.create_index("ix_workflow_events_run_id", "workflow_events", ["run_id"])

    # ---- artifacts ----
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(), primary_key=True, comment="UUID v4 主键"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="创建时间（UTC）"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="最后更新时间（UTC）"),
        sa.Column("project_id", postgresql.UUID(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, comment="所属项目 ID"),
        sa.Column("type", sa.String(50), nullable=False, comment="资产类型"),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False, comment="版本号"),
        sa.Column("episode_number", sa.Integer(), server_default="1", nullable=False, comment="关联集数"),
        sa.Column("content", postgresql.JSONB(), server_default="{}", nullable=False, comment="业务内容"),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False, comment="校验状态"),
        sa.Column("content_schema_version", sa.String(20), server_default="1.0", nullable=False, comment="Schema 版本号"),
        sa.Column("prompt_version", sa.String(20), server_default="", nullable=False, comment="Prompt 版本号"),
        sa.Column("input_hash", sa.String(64), nullable=True, comment="输入哈希"),
        sa.Column("checksum", sa.String(64), nullable=True, comment="内容校验和"),
        sa.Column("source_artifact_ids", postgresql.JSONB(), nullable=True, comment="来源 Artifact 引用"),
        sa.UniqueConstraint("project_id", "type", "episode_number", "version", name="uq_artifacts_project_type_episode_version"),
        sa.CheckConstraint("version > 0", name="ck_artifacts_version_positive"),
        sa.CheckConstraint("episode_number >= 1", name="ck_artifacts_episode_positive"),
    )
    op.create_index("ix_artifacts_project_id", "artifacts", ["project_id"])
    op.create_index("ix_artifacts_type", "artifacts", ["type"])

    # ---- artifact_links ----
    op.create_table(
        "artifact_links",
        sa.Column("id", postgresql.UUID(), primary_key=True, comment="UUID v4 主键"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="创建时间（UTC）"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="最后更新时间（UTC）"),
        sa.Column("source_id", postgresql.UUID(), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False, comment="依赖源 Artifact ID"),
        sa.Column("target_id", postgresql.UUID(), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False, comment="被依赖 Artifact ID"),
        sa.Column("relation", sa.String(50), nullable=False, comment="关系类型"),
        sa.CheckConstraint("source_id != target_id", name="ck_artifact_links_no_self_ref"),
    )
    op.create_index("ix_artifact_links_source_id", "artifact_links", ["source_id"])
    op.create_index("ix_artifact_links_target_id", "artifact_links", ["target_id"])

    # ---- uploads ----
    op.create_table(
        "uploads",
        sa.Column("id", postgresql.UUID(), primary_key=True, comment="UUID v4 主键"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="创建时间（UTC）"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="最后更新时间（UTC）"),
        sa.Column("project_id", postgresql.UUID(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, comment="所属项目 ID"),
        sa.Column("path", sa.String(500), nullable=False, comment="文件存储路径"),
        sa.Column("sha256", sa.String(64), nullable=False, comment="文件 SHA256 哈希"),
        sa.Column("mime_type", sa.String(100), nullable=False, comment="MIME 类型"),
        sa.Column("size_bytes", sa.BigInteger(), server_default="0", nullable=False, comment="文件大小"),
    )
    op.create_index("ix_uploads_project_id", "uploads", ["project_id"])

    # ---- knowledge_documents ----
    op.create_table(
        "knowledge_documents",
        sa.Column("id", postgresql.UUID(), primary_key=True, comment="UUID v4 主键"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="创建时间（UTC）"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="最后更新时间（UTC）"),
        sa.Column("category", sa.String(100), server_default="", nullable=False, comment="文档分类"),
        sa.Column("title", sa.String(300), server_default="", nullable=False, comment="文档标题"),
        sa.Column("license", sa.String(100), server_default="", nullable=False, comment="文档许可证"),
        sa.Column("content", sa.Text(), server_default="", nullable=False, comment="文档原始全文"),
    )

    # ---- knowledge_chunks ----
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", postgresql.UUID(), primary_key=True, comment="UUID v4 主键"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="创建时间（UTC）"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="最后更新时间（UTC）"),
        sa.Column("document_id", postgresql.UUID(), sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False, comment="所属知识文档 ID"),
        sa.Column("content", sa.Text(), server_default="", nullable=False, comment="Chunk 文本内容"),
        sa.Column("embedding", Vector(1536), nullable=True, comment="向量嵌入（pgvector）"),
        sa.Column("metadata", postgresql.JSONB(), nullable=True, comment="Chunk 元数据"),
        sa.Column("chunk_index", sa.Integer(), server_default="0", nullable=False, comment="Chunk 在文档内的序号"),
    )
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])

    # ---- llm_calls ----
    op.create_table(
        "llm_calls",
        sa.Column("id", postgresql.UUID(), primary_key=True, comment="UUID v4 主键"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="创建时间（UTC）"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="最后更新时间（UTC）"),
        sa.Column("run_id", postgresql.UUID(), sa.ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True, comment="所属 Run ID"),
        sa.Column("node_name", sa.String(100), nullable=False, comment="调用节点"),
        sa.Column("model", sa.String(100), nullable=False, comment="模型名标识"),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False, comment="第几次尝试"),
        sa.Column("prompt_version", sa.String(20), server_default="", nullable=False, comment="Prompt 版本号"),
        sa.Column("input_artifact_ids", postgresql.JSONB(), nullable=True, comment="输入 Artifact ID 列表"),
        sa.Column("usage", postgresql.JSONB(), nullable=True, comment="Token 用量"),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False, comment="调用状态"),
        sa.Column("duration_ms", sa.Integer(), nullable=True, comment="调用耗时（毫秒）"),
    )
    op.create_index("ix_llm_calls_run_id", "llm_calls", ["run_id"])


def downgrade() -> None:
    """删除所有基础表。"""
    op.drop_table("llm_calls")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
    op.drop_table("uploads")
    op.drop_table("artifact_links")
    op.drop_table("artifacts")
    op.drop_table("workflow_events")
    op.drop_table("workflow_runs")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("projects")
    # 不删除 pgvector 扩展（可能被其他数据库共享）
