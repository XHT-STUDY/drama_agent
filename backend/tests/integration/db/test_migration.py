"""B-02 数据库迁移测试。

验证 Alembic migration 的 upgrade/downgrade 循环。
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def _import_migration() -> Any:
    """动态导入 0001_initial 模块（模块名以数字开头）。
    返回类型为 Any 因为 import_module 的返回类型是 ModuleType 的泛化。
    """
    return importlib.import_module("migrations.versions.0001_initial")


def _import_migration_0002() -> Any:
    """动态导入 0002_knowledge 模块（D-02）。"""
    return importlib.import_module("migrations.versions.0002_knowledge")


def _import_migration_0005() -> Any:
    """动态导入 0005 Agent 持久化迁移（J-01）。"""
    return importlib.import_module("migrations.versions.0005_agent_turn_actions")


def _run_alembic_command(*args: str) -> subprocess.CompletedProcess[str]:
    """在 backend/ 目录下运行 alembic 命令。"""
    backend_dir = Path(__file__).resolve().parent.parent.parent.parent
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(backend_dir),
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.integration
class TestAlembicMigration:
    """Alembic 迁移结构验证。

    注意：这些测试不连接实际数据库（避免环境依赖），
    但验证迁移脚本的语法正确性和结构完整性。
    """

    def test_migration_file_exists(self) -> None:
        """初始迁移文件存在且可导入。"""
        module = _import_migration()

        # 验证 revision 标识符
        assert hasattr(module, "revision")
        assert hasattr(module, "down_revision")
        assert hasattr(module, "upgrade")
        assert hasattr(module, "downgrade")

    def test_migration_has_all_tables_in_upgrade(self) -> None:
        """upgrade() 函数创建所有 11 张表。"""
        import inspect

        module = _import_migration()

        source = inspect.getsource(module.upgrade)
        expected_tables = [
            "projects",
            "conversations",
            "messages",
            "workflow_runs",
            "workflow_events",
            "artifacts",
            "artifact_links",
            "uploads",
            "knowledge_documents",
            "knowledge_chunks",
            "llm_calls",
        ]
        for table in expected_tables:
            assert f'"{table}"' in source or f"'{table}'" in source or table in source, (
                f"Migration upgrade 缺少表: {table}"
            )

    def test_migration_has_pgvector_extension(self) -> None:
        """迁移包含 CREATE EXTENSION vector。"""
        import inspect

        module = _import_migration()

        source = inspect.getsource(module.upgrade)
        assert "CREATE EXTENSION" in source
        assert "vector" in source

    def test_downgrade_drops_all_tables(self) -> None:
        """downgrade() 函数删除所有表。"""
        import inspect

        module = _import_migration()

        source = inspect.getsource(module.downgrade)
        expected_tables = [
            "projects",
            "conversations",
            "messages",
            "workflow_runs",
            "workflow_events",
            "artifacts",
            "artifact_links",
            "uploads",
            "knowledge_documents",
            "knowledge_chunks",
            "llm_calls",
        ]
        for table in expected_tables:
            assert table in source, f"Migration downgrade 缺少表: {table}"

    def test_revision_chain_valid(self) -> None:
        """Revision 链正确：0001 的 down_revision 为 None。"""
        module = _import_migration()

        assert module.revision == "0001"
        assert module.down_revision is None

    def test_alembic_config_exists(self) -> None:
        """alembic.ini 文件存在。"""
        backend_dir = Path(__file__).resolve().parent.parent.parent.parent
        alembic_ini = backend_dir / "alembic.ini"
        assert alembic_ini.exists(), "alembic.ini 不存在"
        assert alembic_ini.is_file()


@pytest.mark.integration
class TestAlembicMigration0002:
    """D-02 知识库迁移（0002）结构验证。

    与 0001 一样，这些测试不连接实际数据库，只做静态结构校验。
    """

    def test_migration_file_exists(self) -> None:
        """0002 迁移文件存在且可导入。"""
        module = _import_migration_0002()
        assert hasattr(module, "revision")
        assert hasattr(module, "down_revision")
        assert hasattr(module, "upgrade")
        assert hasattr(module, "downgrade")

    def test_revision_chain_valid(self) -> None:
        """Revision 链正确：0002 的 down_revision 为 0001。"""
        module = _import_migration_0002()
        assert module.revision == "0002"
        assert module.down_revision == "0001"

    def test_upgrade_adds_metadata_columns(self) -> None:
        """upgrade() 增加全部 D-01 元数据列。"""
        import inspect

        module = _import_migration_0002()
        source = inspect.getsource(module.upgrade)
        expected_columns = [
            "source",
            "language",
            "genre",
            "stage",
            "tags",
            "version",
            "corpus_version",
            "document_hash",
        ]
        for column in expected_columns:
            assert column in source, f"0002 upgrade 缺少元数据列: {column}"

    def test_upgrade_creates_hnsw_index(self) -> None:
        """upgrade() 建立 pgvector HNSW cosine 向量索引。"""
        import inspect

        module = _import_migration_0002()
        source = inspect.getsource(module.upgrade)
        assert "hnsw" in source, "0002 upgrade 缺少 HNSW 向量索引"
        assert "vector_cosine_ops" in source, "0002 upgrade 向量索引缺少 cosine 运算"

    def test_upgrade_creates_category_index(self) -> None:
        """upgrade() 为 category 过滤列建立索引。"""
        import inspect

        module = _import_migration_0002()
        source = inspect.getsource(module.upgrade)
        assert "ix_knowledge_documents_category" in source

    def test_downgrade_symmetric(self) -> None:
        """downgrade() 对称移除全部新增列与索引。"""
        import inspect

        module = _import_migration_0002()
        source = inspect.getsource(module.downgrade)
        expected_columns = [
            "source",
            "language",
            "genre",
            "stage",
            "tags",
            "version",
            "corpus_version",
            "document_hash",
        ]
        for column in expected_columns:
            assert f'"{column}"' in source, f"0002 downgrade 未移除列: {column}"
        assert "ix_knowledge_chunks_embedding_hnsw" in source
        assert "ix_knowledge_documents_category" in source


@pytest.mark.integration
class TestAlembicMigration0005:
    """J-01 AgentTurn、AgentAction 与消息契约迁移。"""

    def test_revision_chain_valid(self) -> None:
        """0005 必须接在当前迁移头 0004 之后。"""
        module = _import_migration_0005()
        assert module.revision == "0005"
        assert module.down_revision == "0004"

    def test_upgrade_creates_agent_tables_and_message_columns(self) -> None:
        """upgrade 创建两张 Agent 表并扩展 messages。"""
        import inspect

        module = _import_migration_0005()
        source = inspect.getsource(module.upgrade)
        for required in [
            "agent_turns",
            "agent_actions",
            "kind",
            "metadata",
            "uq_messages_conversation_sequence",
            "planning_lease_owner",
            "replan_depth",
        ]:
            assert required in source

    def test_constraints_cover_idempotency_run_and_replan(self) -> None:
        """迁移必须包含幂等、Run 关联与再规划深度约束。"""
        import inspect

        source = inspect.getsource(_import_migration_0005().upgrade)
        assert "uq_agent_turns_project_idempotency" in source
        assert "uq_agent_actions_run_id" in source
        assert "ck_agent_actions_replan_depth" in source
        assert "ck_agent_actions_parent_depth" in source
        assert "uq_agent_actions_parent_replan_depth" in source

    def test_downgrade_is_explicitly_destructive_and_symmetric(self) -> None:
        """downgrade 对称删结构，并明确 Agent 审计数据会丢失。"""
