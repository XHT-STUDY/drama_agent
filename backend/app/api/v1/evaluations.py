"""Evaluation API 路由 — 评估报告查询 (E-03).

端点：
- GET /projects/{id}/evaluations                     列出项目评估报告（按集/版本排序）
- GET /projects/{id}/evaluations/for-script/{sid}    查询绑定到指定剧本版本的评估

说明：评估的"发起"通过 POST /projects/{id}/runs (action=evaluate) 触发
（见 runs.py，E-04 接入 evaluation_workflow）。本路由只提供查询能力。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.application.evaluation_service import EvaluationService
from app.core.errors import NotFoundError

router = APIRouter(tags=["evaluations"])
_service = EvaluationService()


@router.get("/projects/{project_id}/evaluations")
async def list_evaluations(
    project_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """列出项目全部评估报告，按集号升序、版本升序排列。"""
    items = await _service.list_project_evaluations(db, project_id)
    page = items[offset : offset + limit]
    return {
        "items": [a.to_dict() for a in page],
        "total": len(items),
        "offset": offset,
        "limit": limit,
    }


@router.get("/projects/{project_id}/evaluations/for-script/{script_artifact_id}")
async def get_evaluation_for_script(
    project_id: uuid.UUID,
    script_artifact_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """查询绑定到指定剧本版本的评估报告。

    修订后新剧本版本会指向新的 script_artifact_id，
    因此可据此区分"原稿评估"与"修订稿评估"。

    Raises:
        NotFoundError: 该剧本版本尚无评估报告。
    """
    artifact = await _service.get_evaluation_for_script(db, project_id, script_artifact_id)
    if artifact is None:
        raise NotFoundError(
            detail=f"剧本 {script_artifact_id} 尚无评估报告",
            code="EVALUATION_NOT_FOUND",
        )
    return artifact.to_dict()
