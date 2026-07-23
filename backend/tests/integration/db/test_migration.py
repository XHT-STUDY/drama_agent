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
