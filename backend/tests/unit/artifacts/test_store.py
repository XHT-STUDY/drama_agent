"""B-04 ArtifactStore 单元测试（mock DB）。

验证 Store 的核心逻辑不依赖真实数据库。
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.artifacts.store import ArtifactStore


@pytest.mark.asyncio
class TestArtifactStoreCreate:
    """ArtifactStore.create 逻辑。"""

    @pytest.fixture
    def store(self) -> ArtifactStore:
        return ArtifactStore()

    @pytest.fixture
    def mock_db(self) -> AsyncMock:
        return AsyncMock()

    async def test_create_assigns_version_1_first_time(self, store: ArtifactStore) -> None:
        """首次创建的版本号为 1。"""
        mock_db = AsyncMock()
        mock_repo = MagicMock()
        mock_repo.get_max_version = AsyncMock(return_value=None)
        mock_repo.find_by_input_hash = AsyncMock(return_value=None)
        mock_repo.add = AsyncMock()
        mock_repo.session = mock_db

        # Patch _get_repo to return our mock
        with patch.object(store, "_get_repo", return_value=mock_repo):
            project_id = uuid.uuid4()
            artifact = await store.create(
                mock_db,
                project_id=project_id,
                artifact_type="script_draft",
                content={"scenes": []},
            )

            assert artifact.version == 1
            assert artifact.project_id == project_id
            assert artifact.type == "script_draft"
            assert artifact.checksum is not None
            assert len(artifact.checksum) == 64

    async def test_create_increments_version(self, store: ArtifactStore) -> None:
        """已存在版本时递增。"""
        mock_db = AsyncMock()
        mock_repo = MagicMock()
        mock_repo.get_max_version = AsyncMock(return_value=5)
        mock_repo.find_by_input_hash = AsyncMock(return_value=None)
        mock_repo.add = AsyncMock()
        mock_repo.session = mock_db

        with patch.object(store, "_get_repo", return_value=mock_repo):
            artifact = await store.create(
                mock_db,
                project_id=uuid.uuid4(),
                artifact_type="script_draft",
                content={"scenes": []},
            )
            assert artifact.version == 6

    async def test_create_returns_existing_on_input_hash_match(self, store: ArtifactStore) -> None:
        """相同 input_hash 返回已有记录（幂等）。"""
        mock_db = AsyncMock()
        existing = MagicMock()
        existing.id = uuid.uuid4()
        existing.version = 1

        mock_repo = MagicMock()
        mock_repo.find_by_input_hash = AsyncMock(return_value=existing)
        mock_repo.session = mock_db

        with patch.object(store, "_get_repo", return_value=mock_repo):
            result = await store.create(
                mock_db,
                project_id=uuid.uuid4(),
                artifact_type="script_draft",
                content={"scenes": []},
                source_artifact_ids=[{"artifact_id": str(uuid.uuid4())}],
            )
            assert result is existing  # 返回已有记录
            mock_repo.add.assert_not_called()  # 未创建新记录
