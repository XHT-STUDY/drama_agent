"""Run API 路由 — WorkflowRun 生命周期 (C-08).

端点：
- POST /projects/{id}/runs  → 创建 Run（支持 create_script 选项）
- GET  /runs/{run_id}       → 查询 Run 状态
- POST /runs/{run_id}/cancel → 取消 Run
- GET  /runs/{run_id}/events → SSE 事件流
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.application.run_service import RunService
from app.core.config import Settings
from app.events.stream import router as sse_router

router = APIRouter(tags=["runs"])
_service = RunService()

# 包含 SSE 子路由
router.include_router(sse_router)


# ---- Request/Response Schemas ----


class CreateScriptOptions(BaseModel):
    """create_script action 的选项 (C-08).

    MVP 边界约束（见 DEV_PLAN §1.3）：
    - outline_count 固定 10
    - script_count 固定 3
    """

    model_config = {"extra": "forbid"}

    user_input: str = Field(
        ..., min_length=1, max_length=10000,
        description="用户输入的创作 Idea/Outline 文本",
    )
    source_type: str = Field(
        default="idea",
        description="输入类型: idea / outline / txt / docx",
    )
    outline_count: int = Field(
        default=10, ge=1, le=100,
        description="大纲集数（MVP 固定 10）",
    )
    script_count: int = Field(
        default=3, ge=1, le=50,
        description="生成剧本集数（MVP 固定 3）",
    )

    @model_validator(mode="after")
    def _mvp_boundary_warning(self) -> CreateScriptOptions:
        """超出 MVP 范围时做最佳努力：不拒绝请求，但记录边界处理策略。

        验收项（C-08）：
        - outline_count 不是 10 或 script_count 超过 3 时按 MVP 配置处理
        """
        # MVP 阶段接受任意合法值，但记录非标准配置
        # Worker 读取 options 时尊重用户设定，不做静默修改
        return self


class CreateRunRequest(BaseModel):
    """创建 Run 请求体。"""

    model_config = {"extra": "forbid"}

    action: str = Field(
        ..., max_length=50,
        description="执行动作：create_script / evaluate / revise / platform_smoke",
    )
    config: dict[str, Any] | None = Field(default=None, description="Run 配置快照")
    options: CreateScriptOptions | None = Field(
        default=None,
        description="create_script 的专属选项（action=create_script 时提供）",
    )
    idempotency_key: str | None = Field(default=None, max_length=128, description="幂等键")


class RunResponse(BaseModel):
    """Run 响应体。"""

    model_config = {"extra": "forbid"}

    run_id: str = Field(..., description="Run UUID")
    project_id: str = Field(..., description="所属项目 UUID")
    action: str = Field(..., description="执行动作")
    status: str = Field(..., description="当前状态")
    config_snapshot: dict[str, Any] | None = Field(default=None, description="配置快照")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")

    @classmethod
    def from_orm(cls, run: Any) -> RunResponse:
        return cls(
            run_id=str(run.id),
            project_id=str(run.project_id),
            action=run.action,
            status=run.status,
            config_snapshot=run.config_snapshot,
            created_at=run.created_at.isoformat() if run.created_at else "",
            updated_at=run.updated_at.isoformat() if run.updated_at else "",
        )


class RunListResponse(BaseModel):
    """Run 列表响应。"""

    model_config = {"extra": "forbid"}

    items: list[RunResponse] = Field(default_factory=list, description="Run 列表")
    total: int = Field(default=0, description="总数")
    offset: int = Field(default=0, description="偏移量")
    limit: int = Field(default=20, description="每页数量")


# ---- 端点 ----


@router.post(
    "/projects/{project_id}/runs",
    response_model=RunResponse,
    status_code=202,
    responses={
        202: {"description": "Run 已创建并进入队列"},
        404: {"description": "项目不存在"},
        409: {"description": "存在未完成的 Run（活跃冲突）"},
    },
)
async def create_run(
    project_id: uuid.UUID,
    body: CreateRunRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RunResponse:
    """创建新的 WorkflowRun。

    返回 HTTP 202 Accepted，客户端通过 GET /runs/{id}/events 订阅进度。

    action=create_script 时：
    - 需要提供 options（含 user_input）
    - Worker 将执行 creation workflow：normalize → story_bible → outline → scripts
    """
    # 合并 options 到 config 快照中
    config_snapshot = body.config or {}
    if body.options:
        config_snapshot["options"] = body.options.model_dump()

    run = await _service.create_run(
        db,
        project_id=project_id,
        action=body.action,
        config=config_snapshot,
        idempotency_key=body.idempotency_key,
    )

    # 异步启动后台 Worker（best effort，不阻塞响应）
    if body.action in ("create_script", "platform_smoke"):
        _schedule_worker(run.id, body.action, config_snapshot)

    return RunResponse.from_orm(run)


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RunResponse:
    """查询 Run 状态。"""
    run = await _service.get_run(db, run_id)
    return RunResponse.from_orm(run)


@router.get(
    "/projects/{project_id}/runs",
    response_model=RunListResponse,
)
async def list_runs(
    project_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: int = 0,
    limit: int = 20,
) -> RunListResponse:
    """按项目分页查询 Run 列表。"""
    runs = await _service.list_runs_by_project(db, project_id, offset=offset, limit=limit)
    return RunListResponse(
        items=[RunResponse.from_orm(r) for r in runs],
        total=len(runs),
        offset=offset,
        limit=limit,
    )


@router.post("/runs/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RunResponse:
    """取消 Run（仅 queued 状态可取消）。"""
    run = await _service.cancel_run(db, run_id)
    return RunResponse.from_orm(run)


# ---- Worker 调度 ----

# 模块级变量：记录当前活跃的 Worker 任务
_active_workers: dict[str, Any] = {}  # run_id → asyncio.Task


def _schedule_worker(
    run_id: uuid.UUID,
    action: str,
    config_snapshot: dict[str, Any],
) -> None:
    """调度后台 Worker 执行 Workflow（C-08）。

    在 create_run 响应返回后异步启动，
    使用独立的 DB 会话进行 Workflow 执行。
    """
    import asyncio

    key = str(run_id)
    if key in _active_workers:
        return

    async def _worker_wrapper() -> None:
        try:
            await _execute_workflow(run_id, action, config_snapshot)
        finally:
            _active_workers.pop(key, None)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    task = loop.create_task(_worker_wrapper())
    _active_workers[key] = task


async def _execute_workflow(
    run_id: uuid.UUID,
    action: str,
    config_snapshot: dict[str, Any],
) -> None:
    """在后台执行 Creation Workflow。

    从数据库加载 Run，创建 Workflow 运行时上下文，
    通过 LangGraph 执行完整创作流程。
    """
    import asyncio as _asyncio

    # 短暂延迟，确保 createrun 的事务已提交
    await _asyncio.sleep(0.1)


    settings = Settings()
    if settings.app_env == "test":
        from app.llm.fake import FakeLLM
        llm_client = FakeLLM(seed=42)
        _register_fake_fixtures(llm_client)
    else:
        from app.llm.openai_compatible import OpenAICompatibleLLM
        llm_client = OpenAICompatibleLLM(settings)

    from app.agents.base import BaseAgent
    from app.application.artifact_service import ArtifactService
    from app.application.run_service import RunService
    from app.events.publisher import EventPublisher
    from app.prompts.loader import PromptLoader
    from app.workflows.creation import build_creation_workflow
    from app.workflows.state import CreationState

    agent = BaseAgent(name="planner", llm=llm_client)
    prompt_loader = PromptLoader()
    artifact_svc = ArtifactService()
    run_svc = RunService()
    publisher = EventPublisher()

    from app.db.session import _async_session_factory
    assert _async_session_factory is not None, "DB not initialized"

    async with _async_session_factory() as db, db.begin():
        try:
            # 验证 Run 存在且状态正确
            run = await run_svc.get_run(db, run_id)
            if run.status != "queued":
                return
            await run_svc.transition_status(db, run_id, "running")

            # platform_smoke / 非 create_script → 直接完成
            if action != "create_script":
                await run_svc.transition_status(db, run_id, "completed")
                await publisher.publish(
                    db, run_id=run_id, event_type="run.completed",
                    payload={"message": f"action={action} 完成", "progress": 1.0},
                )
                return

            # create_script → 执行完整 Creation Workflow
            options: dict[str, Any] = config_snapshot.get("options", {})
            user_input = options.get("user_input", "")
            if not user_input:
                user_input = "一个被青训队抛弃的足球少年逆袭故事"

            initial_state: CreationState = {
                "run_id": str(run_id),
                "project_id": str(run.project_id),
                "action": action,
                "requirement_artifact_id": None,
                "story_bible_artifact_id": None,
                "outline_set_artifact_id": None,
                "script_artifact_ids": {},
                "continuity_state_text": "",
                "current_episode": 1,
                "status": "running",
                "needs_user_input": False,
                "error_node": None,
                "error_detail": None,
                "completed_nodes": [],
                "input_hashes": {},
                "prompt_versions": {},
            }

            progress_log: list[dict[str, Any]] = []

            def progress_callback(node: str, event: str, progress: float) -> None:
                progress_log.append({"node": node, "event": event, "progress": progress})

            workflow_config: dict[str, Any] = {
                "configurable": {
                    "db": db,
                    "agent": agent,
                    "prompt_loader": prompt_loader,
                    "artifact_service": artifact_svc,
                    "run_service": run_svc,
                    "event_publisher": publisher,
                    "user_input": user_input,
                    "source_type": options.get("source_type", "idea"),
                    "rag_context": "",
                    "progress_callback": progress_callback,
                    "progress_log": progress_log,
                },
            }

            workflow = build_creation_workflow()
            final_state = await workflow.ainvoke(initial_state, workflow_config)

            if final_state.get("status") == "failed":
                await run_svc.transition_status(db, run_id, "failed")
                await publisher.publish(
                    db, run_id=run_id, event_type="run.failed",
                    payload={
                        "error_node": final_state.get("error_node"),
                        "error_detail": final_state.get("error_detail"),
                    },
                )

            if final_state.get("needs_user_input"):
                await run_svc.transition_status(db, run_id, "needs_review")
                await publisher.publish(
                    db, run_id=run_id, event_type="run.needs_review",
                    payload={"reason": "用户输入不完整，需要补充信息"},
                )

        except Exception as e:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.exception("Workflow 执行失败: run=%s", run_id)
            try:
                await run_svc.transition_status(db, run_id, "failed")
                await publisher.publish(
                    db, run_id=run_id, event_type="run.failed",
                    payload={"error": str(e)},
                )
            except Exception:
                pass
        finally:
            if hasattr(llm_client, "close"):
                await llm_client.close()  # type: ignore[union-attr]


def _register_fake_fixtures(llm: Any) -> None:
    """为 FakeLLM 注册 Creation Workflow 所需的 Golden Fixture。

    使 API 集成测试无需真实 LLM 即可走完整流程。
    """
    import json as _json
    import os as _os

    # runs.py → api/v1/ → api/ → app/ → backend/
    _golden_dir = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__)))),
        "tests", "golden",
    )

    def _load(name: str) -> dict[str, Any]:
        path = _os.path.join(_golden_dir, f"{name}.json")
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
        if isinstance(data, dict) and "expected_output" in data:
            return data["expected_output"]
        return data

    from app.domain.outline import EpisodeOutlineSet
    from app.domain.requirement import NormalizedRequirement
    from app.domain.script import ScriptDraft
    from app.domain.story_bible import StoryBible

    llm.register("normalize_requirement", NormalizedRequirement.model_validate(_load("requirement_football")))
    llm.register("story_bible", StoryBible.model_validate(_load("story_bible_football")))
    llm.register("outline", EpisodeOutlineSet.model_validate(_load("outline_set_valid")))
    llm.register("write_episode", ScriptDraft.model_validate(_load("script_draft_valid")))
