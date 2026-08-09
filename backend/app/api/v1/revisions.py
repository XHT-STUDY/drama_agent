"""Revision API 路由 — 修订闭环契约 (F-06).

端点：
- POST /projects/{project_id}/revisions                      发起修订（202，返回 Run）
- GET  /projects/{project_id}/revisions                      列出项目修订计划
- GET  /projects/{project_id}/revisions/{plan_artifact_id}   计划详情 + 解析结果链

发起修订支持两种模式：
- 自动修订：不指定剧本，确定性选最低分集（仅 need_revision=true 的集）;
- 单集修订：通过 script_artifact_id 指定一个合法剧本版本（任意版本，不校验最新），
  可选 user_instruction（不能绕过锁定事实，由 RevisionPlanSkill 硬性并入 preserve）。

校验顺序（POST 同步拒绝，不排队）：
1. 指定剧本存在 / type=script_draft / status=valid → 404 SCRIPT_NOT_FOUND;
2. 指定剧本属于当前项目 → 403 CROSS_PROJECT_ACCESS;
3. 指定剧本恰好绑定 valid 评估 → 404 EVALUATION_NOT_FOUND（"已过期评估不匹配"拒绝）。
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.api.v1.runs import RunResponse, schedule_worker
from app.application.artifact_service import ArtifactService
from app.application.evaluation_service import EvaluationService
from app.application.revision_service import RevisionService
from app.application.run_service import RunService
from app.core.errors import AppError, NotFoundError
from app.domain.revision import RevisionPlan

router = APIRouter(tags=["revisions"])
_service = RevisionService()
_artifact_svc = ArtifactService()
_eval_svc = EvaluationService()
_run_svc = RunService()


# ---- Request Schemas ----


class CreateRevisionRequest(BaseModel):
    """发起修订请求体。

    两种模式：
    - script_artifact_id 缺省 → 自动修订（确定性选最低分集）;
    - script_artifact_id 提供 → 为"该剧本所在集"生成修订计划（任意合法版本）。
    """

    model_config = {"extra": "forbid"}

    script_artifact_id: uuid.UUID | None = Field(
        default=None,
        description="指定待修订的剧本版本（合法任意版本，集号由该版本决定）；缺省时自动选最低分集",
    )
    user_instruction: str | None = Field(
        default=None,
        max_length=2000,
        description="用户补充要求（不可违反锁定事实；锁定事实由服务端硬性兜底）",
    )
    idempotency_key: str | None = Field(
        default=None, max_length=128, description="幂等键（相同键返回已有 Run）"
    )


# ---- 端点 ----


@router.post(
    "/projects/{project_id}/revisions",
    response_model=RunResponse,
    status_code=202,
    responses={
        202: {"description": "修订 Run 已创建并进入队列"},
        404: {"description": "项目/剧本/评估不存在"},
        403: {"description": "跨项目访问被拒绝"},
    },
)
async def create_revision(
    project_id: uuid.UUID,
    body: CreateRevisionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RunResponse:
    """发起修订（自动或单集指定），返回 202 + Run。

    客户端通过 GET /runs/{run_id} 轮询状态、POST /runs/{run_id}/events
    订阅 SSE 进度。
    """
    options: dict[str, Any] = {
        "user_instruction": body.user_instruction,
        "episode_number": None,
    }
    if body.script_artifact_id is not None:
        # 1. 脚本存在 / type / status 校验（404 SCRIPT_NOT_FOUND）
        try:
            script = await _artifact_svc.get_version(db, body.script_artifact_id)
        except NotFoundError:
            raise NotFoundError(
                detail=f"剧本不存在: {body.script_artifact_id}",
                code="SCRIPT_NOT_FOUND",
            ) from None
        if script.type != "script_draft" or script.status != "valid":
            raise NotFoundError(
                detail=f"指定的 Artifact 不是合法剧本版本: {body.script_artifact_id}",
                code="SCRIPT_NOT_FOUND",
            )
        # 2. 跨项目防护（403 CROSS_PROJECT_ACCESS）
        if script.project_id != project_id:
            raise AppError(
                detail="不允许对其它项目的剧本发起修订",
                status_code=403,
                code="CROSS_PROJECT_ACCESS",
            )
        # 3. 该剧本恰好绑定 valid 评估（404 EVALUATION_NOT_FOUND）
        bound = await _eval_svc.get_evaluation_for_script(
            db, project_id, body.script_artifact_id
        )
        if bound is None:
            raise NotFoundError(
                detail=f"剧本 {body.script_artifact_id} 尚无绑定评估，无法发起修订",
                code="EVALUATION_NOT_FOUND",
            )
        options["script_artifact_id"] = str(body.script_artifact_id)
        options["episode_number"] = script.episode_number

    config_snapshot: dict[str, Any] = {"options": options}
    run = await _run_svc.create_run(
        db,
        project_id=project_id,
        action="revise",
        config=config_snapshot,
        idempotency_key=body.idempotency_key,
    )

    # 异步启动后台 Worker（best effort，不阻塞响应）
    schedule_worker(run.id, "revise", config_snapshot)

    return RunResponse.from_orm(run)


@router.get("/projects/{project_id}/revisions")
async def list_revisions(
    project_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """列出项目全部修订计划，按集号升序、版本升序排列。"""
    items = await _service.list_project_revision_plans(db, project_id)
    page = items[offset : offset + limit]
    return {
        "items": [a.to_dict() for a in page],
        "total": len(items),
        "offset": offset,
        "limit": limit,
    }


@router.get("/projects/{project_id}/revisions/{plan_artifact_id}")
async def get_revision_detail(
    project_id: uuid.UUID,
    plan_artifact_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """获取修订计划详情，并解析结果链（防御式置空）。

    结果链（沿 ArtifactLink 反查）:
    - source_script / source_evaluation: 计划引用的原稿与其评估;
    - candidate_script: relation="revises" 指向该计划的候选新稿;
    - continuity_check: 候选稿派生出的连续性检查结果;
    - new_evaluation: 绑定候选稿的重评报告;
    - diff_ids: Diff 两端（base=原稿, target=候选稿），供前端调用 /artifacts/diff。
    """
    plan_artifact = await _artifact_svc.get_version(db, plan_artifact_id)
    if plan_artifact.project_id != project_id:
        raise AppError(
            detail="不允许访问其它项目的修订计划",
            status_code=403,
            code="CROSS_PROJECT_ACCESS",
        )
    if plan_artifact.type != "revision_plan":
        raise NotFoundError(
            detail=f"Artifact 不是修订计划: {plan_artifact_id}",
            code="ARTIFACT_NOT_FOUND",
        )

    plan = RevisionPlan.model_validate(plan_artifact.content)

    chain: dict[str, Any] = {
        "source_script": None,
        "source_evaluation": None,
        "candidate_script": None,
        "continuity_check": None,
        "new_evaluation": None,
        "diff_ids": None,
    }

    # 原稿与评估（权威 ID，防御式置空）
    with contextlib.suppress(NotFoundError):
        chain["source_script"] = (
            await _artifact_svc.get_version(db, plan.source_script_artifact_id)
        ).to_dict()
    with contextlib.suppress(NotFoundError):
        chain["source_evaluation"] = (
            await _artifact_svc.get_version(db, plan.source_evaluation_artifact_id)
        ).to_dict()

    # 候选稿（revises 关系；按版本升序取最新）
    candidates = await _artifact_svc.find_referencing_artifacts(
        db, plan_artifact_id, relation="revises", artifact_type="script_draft"
    )
    candidate = candidates[-1] if candidates else None
    if candidate is not None:
        chain["candidate_script"] = candidate.to_dict()

        # 连续性检查结果（候选稿派生）
        checks = await _artifact_svc.find_referencing_artifacts(
            db, candidate.id,
            relation="derived_from", artifact_type="continuity_check",
        )
        if checks:
            chain["continuity_check"] = checks[-1].to_dict()

        # 绑定候选稿的重评报告
        new_eval = await _eval_svc.get_evaluation_for_script(
            db, project_id, candidate.id
        )
        if new_eval is not None:
            chain["new_evaluation"] = new_eval.to_dict()

        # Diff 两端
        chain["diff_ids"] = {
            "base": str(plan.source_script_artifact_id),
            "target": str(candidate.id),
        }

    result = plan_artifact.to_dict()
    result["result_chain"] = chain
    return result
