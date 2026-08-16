"""上传文件 Repository（G-03）。

提供 Upload 行的创建、项目内列表与归属校验。
只做数据持久化，不做解析/校验（解析由 file_parser，落盘由 storage）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.upload import Upload
from app.db.repositories.base import BaseRepository


class UploadRepository(BaseRepository):
    """Upload 行存取。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化 Repository（绑定 Upload 模型）。"""
        super().__init__(session, Upload)

    async def list_by_project(
        self, project_id: uuid.UUID, *, offset: int = 0, limit: int = 100
    ) -> list[Upload]:
        """按项目列出上传文件（按创建时间倒序，最新的在前）。"""
        stmt = (
            select(Upload)
            .where(Upload.project_id == project_id)
            .order_by(Upload.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_project(
        self, project_id: uuid.UUID, upload_id: uuid.UUID
    ) -> Upload | None:
        """按 (project_id, upload_id) 获取单条上传记录（含归属校验）。"""
        stmt = select(Upload).where(
            Upload.id == upload_id,
            Upload.project_id == project_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
