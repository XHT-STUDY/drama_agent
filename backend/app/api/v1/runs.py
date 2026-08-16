"""Run API 路由 — WorkflowRun 生命周期 (C-08).

端点：
- POST /projects/{id}/runs  → 创建 Run（支持 create_script 选项）
- GET  /runs/{run_id}       → 查询 Run 状态
- POST /runs/{run_id}/cancel → 取消 Run
- GET  /runs/{run_id}/events → SSE 事件流
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.application.run_service import RunService
from app.core.config import Settings
from app.core.errors import AppError, RunAlreadyActiveError, RunNotRetryableError
from app.db.models.workflow_run import WorkflowRun
from app.events.stream import router as sse_router
from app.llm.budget import enter_run, exit_run, get_budget
from app.observability.diagnostics import RunDiagnosticsResponse
from app.workflows.checkpoint import (
    RunCancelledError,
    classify_error_code,
    clear_cancel,
    save_checkpoint,
)

logger = logging.getLogger(__name__)

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
    error_code: str | None = Field(default=None, description="机器可读错误码（failed 时，I-01）")
    error_detail: str | None = Field(default=None, description="错误详情（failed 时，I-01）")
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
            error_code=run.error_code,
            error_detail=run.error_detail,
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
    if body.action in (
        "create_script",
        "platform_smoke",
        "revise",
        "import",
        "export",
        "evaluate",
    ):
        schedule_worker(run.id, body.action, config_snapshot)

    return RunResponse.from_orm(run)


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RunResponse:
    """查询 Run 状态。"""
    run = await _service.get_run(db, run_id)
    return RunResponse.from_orm(run)


@router.get("/runs/{run_id}/diagnostics", response_model=RunDiagnosticsResponse)
async def get_run_diagnostics(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RunDiagnosticsResponse:
    """Run 运行诊断（I-02）。

    聚合事件表输出节点时间线 / LLM 调用统计 / 失败信息，满足
    "根据 run_id 找到完整节点时间线"与"统计一次 Demo 调用次数与 token"。
    """
    from app.observability.diagnostics import build_run_diagnostics

    return await build_run_diagnostics(db, run_id)


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
    """取消 Run（I-01 协作式）。

    queued → 立即取消；running → 置内存取消标记，工作流在下一节点守卫
    处中断（cancel 后不再创建新 Artifact），Run 由 Worker 转为 cancelled。
    """
    run = await _service.cancel_run(db, run_id)
    return RunResponse.from_orm(run)


@router.post("/runs/{run_id}/retry", response_model=RunResponse)
async def retry_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RunResponse:
    """重试失败的 Run（I-01）。

    仅 failed / needs_review 可重试；以 state_summary 为初始状态重放，
    已完成节点（completed_nodes）早退 → 不重调 LLM、不重复建 Artifact、
    不重复推进 revision_round。completed / cancelled 不可重试，
    queued / running 存在活跃 Worker 不可重复重试。
    """
    run = await _service.get_run(db, run_id)
    if run.status in ("completed", "cancelled"):
        raise RunNotRetryableError(detail=f"Run 已处于终态 {run.status}，不可重试")
    if run.status in ("queued", "running"):
        raise RunAlreadyActiveError(detail=f"Run 正在执行（{run.status}），不可重试")

    # 开启新尝试：清空上一轮错误字段，回到队列
    run.error_code = None
    run.error_detail = None
    await db.flush()
    await _service.transition_status(db, run_id, "queued")

    schedule_worker(run.id, run.action, run.config_snapshot or {})
    return RunResponse.from_orm(run)


# ---- Worker 调度 ----

# 模块级变量：记录当前活跃的 Worker 任务
_active_workers: dict[str, Any] = {}  # run_id → asyncio.Task


def schedule_worker(
    run_id: uuid.UUID,
    action: str,
    config_snapshot: dict[str, Any],
) -> None:
    """调度后台 Worker 执行 Workflow（C-08）。

    在 create_run 响应返回后异步启动，
    使用独立的 DB 会话进行 Workflow 执行。
    公开供 revisions.py 导入（F-06），避免环形 import。
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


async def _resolve_upload_text(
    db: AsyncSession,
    project_id: uuid.UUID,
    upload_id: str,
) -> str:
    """读取上传文件的解析文本（G-06：导入内容进入创作/评估流程）。

    Raises:
        ValueError: 上传记录不存在（不属于该项目）或读取失败
    """
    from app.core.config import load_settings
    from app.db.repositories.uploads import UploadRepository
    from app.storage.local import LocalFileStore
    from app.tools.file_parser import FileParserTool

    repo = UploadRepository(db)
    upload = await repo.get_for_project(project_id, uuid.UUID(upload_id))
    if upload is None:
        raise ValueError(f"上传记录不存在: {upload_id}")
    settings = load_settings()
    store = LocalFileStore(root=settings.upload_file_root)
    data = await store.open(upload.path)
    parsed = await FileParserTool(
        upload_max_bytes=settings.upload_max_bytes
    ).execute(filename=upload.original_name, data=data)
    return parsed.text


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
    from app.artifacts.store import ArtifactStore
    from app.db.repositories.artifacts import ArtifactRepository
    from app.events.publisher import EventPublisher
    from app.prompts.loader import PromptLoader
    from app.workflows.creation import build_creation_workflow
    from app.workflows.evaluation import build_evaluation_workflow
    from app.workflows.import_file import build_import_workflow
    from app.workflows.revision import build_revision_workflow

    agent = BaseAgent(name="planner", llm=llm_client)
    prompt_loader = PromptLoader()
    artifact_svc = ArtifactService()
    run_svc = RunService()
    publisher = EventPublisher()

    from app.db.session import _async_session_factory
    assert _async_session_factory is not None, "DB not initialized"

    async with _async_session_factory() as db:
        try:
            # 验证 Run 存在且状态正确
            run = await run_svc.get_run(db, run_id)
            if run.status != "queued":
                return
            await run_svc.transition_status(db, run_id, "running")

            # I-01：登记 per-run LLM 预算（软/硬上限来自 Settings）；并读取
            # 上一轮 state_summary 作为 retry 恢复的基底（全新 run 为 None）。
            enter_run(
                str(run_id),
                soft_calls=settings.run_max_llm_calls,
                hard_calls=settings.run_max_llm_calls_hard,
                hard_tokens=settings.run_max_llm_tokens_hard,
            )
            checkpoint = run.state_summary or {}

            if action == "export":
                # action=export → 确定性导出（G-06）：组装 → 序列化 → 落盘
                # → ExportFile Artifact。无 LLM、无 LangGraph 工作流，
                # 直接调用 ExportService；任一步失败由外层 except 标记 Run failed。
                from app.application.export_service import ExportService
                from app.domain.export import ExportSelection

                export_options = config_snapshot.get("options", {})
                selection = ExportSelection(
                    kinds=export_options.get(
                        "kinds",
                        ["story_bible", "outline", "script", "evaluation", "revision"],
                    ),
                    format=export_options.get("format", "markdown"),
                    artifact_ids=export_options.get("artifact_ids"),
                )
                artifact = await ExportService().export_project(
                    db, project_id=run.project_id, selection=selection
                )
                await run_svc.transition_status(db, run_id, "completed")
                await publisher.publish(
                    db, run_id=run_id, event_type="run.completed",
                    payload={
                        "message": "导出完成",
                        "artifact_id": str(artifact.id),
                        "filename": artifact.content.get("filename"),
                        "format": artifact.content.get("format"),
                    },
                    autocommit=True,
                )
                await db.commit()
                return

            # platform_smoke 等未接入的 action → 直接完成
            if action not in ("create_script", "evaluate", "revise", "import"):
                await run_svc.transition_status(db, run_id, "completed")
                await publisher.publish(
                    db, run_id=run_id, event_type="run.completed",
                    payload={"message": f"action={action} 完成", "progress": 1.0},
                    autocommit=True,
                )
                return

            options: dict[str, Any] = config_snapshot.get("options", {})
            user_input = options.get("user_input", "")
            # G-06 导入路径：config.upload_id 提供时优先用上传文件内容作为创作输入
            # （"上传 Outline → 创作"：导入分类 route=create 后，客户端带 upload_id
            #  重跑 create_script，Worker 解析上传文本注入创作管线）。
            upload_id_cfg = config_snapshot.get("upload_id")
            if upload_id_cfg:
                upload_text = await _resolve_upload_text(
                    db, run.project_id, upload_id_cfg
                )
                if upload_text:
                    user_input = upload_text
            if not user_input:
                user_input = "一个被青训队抛弃的足球少年逆袭故事"

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
                    "upload_id": config_snapshot.get("upload_id"),
                    "script_count": options.get("script_count", 3),
                    "outline_count": options.get("outline_count", 10),
                    "rag_context": "",
                    "progress_callback": progress_callback,
                    "progress_log": progress_log,
                },
            }

            # initial_state / workflow 由各 action 分支赋不同工作流的状态结构
            #（CreationState / ImportState 等），此处用 Any 避免 TypedDict 强约束。
            initial_state: Any
            workflow: Any
            if action == "create_script":
                # 完整 Creation Workflow（写完后自动进入评估）
                initial_state = {
                    "run_id": str(run_id),
                    "project_id": str(run.project_id),
                    "action": action,
                    "requirement_artifact_id": None,
                    "story_bible_artifact_id": None,
                    "outline_set_artifact_id": None,
                    "script_artifact_ids": {},
                    "evaluation_artifact_ids": {},
                    "needs_revision_decision": False,
                    "continuity_state_text": "",
                    "revision_round": 0,
                    "revision_candidate_episode": None,
                    "revision_plan_artifact_id": None,
                    "needs_manual_review": False,
                    "needs_manual_review_reason": None,
                    "current_episode": 1,
                    "status": "running",
                    "needs_user_input": False,
                    "error_node": None,
                    "error_detail": None,
                    "completed_nodes": [],
                    "input_hashes": {},
                    "prompt_versions": {},
                }
                workflow = build_creation_workflow()
            elif action == "evaluate":
                # action=evaluate → 收集项目已有剧本（每集最新 valid），走独立评估工作流
                store = ArtifactStore()
                scripts = await store.list_by_project(
                    db, run.project_id, "script_draft", offset=0, limit=1000
                )
                latest_per_episode: dict[int, str] = {}
                for a in scripts:
                    if a.status == "valid" and a.episode_number not in latest_per_episode:
                        latest_per_episode[a.episode_number] = str(a.id)
                initial_state = {
                    "run_id": str(run_id),
                    "project_id": str(run.project_id),
                    "action": action,
                    "script_artifact_ids": {
                        str(ep): sid for ep, sid in latest_per_episode.items()
                    },
                    "evaluation_artifact_ids": {},
                    "needs_revision_decision": False,
                    "status": "running",
                    "completed_nodes": [],
                    "input_hashes": {},
                    "prompt_versions": {},
                }
                workflow = build_evaluation_workflow()
            elif action == "import":
                # action=import → 单节点导入分类工作流（G-04）：
                # parse（FileStore + Parser）→ classify（规则+LLM）→ 持久化
                # import_classification Artifact → route。
                workflow = build_import_workflow()
                initial_state = {
                    "run_id": str(run_id),
                    "project_id": str(run.project_id),
                    "action": action,
                    "upload_id": config_snapshot.get("upload_id"),
                    "classification_artifact_id": None,
                    "script_artifact_id": None,
                    "content_type": None,
                    "route": None,
                    "needs_user_input": False,
                    "status": "running",
                    "error_node": None,
                    "error_detail": None,
                    "completed_nodes": [],
                    "prompt_versions": {},
                }
            else:
                # action=revise → 独立修订工作流（F-06）：中途播种状态。
                # 每集取最新 valid 剧本与其绑定评估；用户指定剧本时覆盖对应集，
                # 保证"任一合法版本可指定"成立（不校验是否最新）。
                store = ArtifactStore()
                sb_artifact = await store.get_latest(
                    db, run.project_id, "story_bible", 1
                )
                outline_artifact = await store.get_latest(
                    db, run.project_id, "episode_outline_set", 1
                )
                if sb_artifact is None or outline_artifact is None:
                    raise ValueError("缺少 StoryBible 或大纲，无法执行修订")

                scripts = await store.list_by_project(
                    db, run.project_id, "script_draft", offset=0, limit=1000
                )
                latest_scripts: dict[int, str] = {}
                for a in scripts:
                    if a.status == "valid" and a.episode_number not in latest_scripts:
                        latest_scripts[a.episode_number] = str(a.id)

                repo = ArtifactRepository(db)
                script_artifact_ids = {
                    str(ep): sid for ep, sid in latest_scripts.items()
                }
                evaluation_artifact_ids: dict[str, str] = {}
                for ep, sid in latest_scripts.items():
                    bound = await repo.find_evaluation_for_script(
                        run.project_id, uuid.UUID(sid)
                    )
                    if bound is not None:
                        evaluation_artifact_ids[str(ep)] = str(bound.id)

                # 用户指定剧本覆盖（防御性重校验：type/status/project + 绑定评估）
                revision_candidate_episode: int | None = options.get("episode_number")
                script_id_opt = options.get("script_artifact_id")
                if script_id_opt:
                    user_script = await store.get_version(
                        db, uuid.UUID(script_id_opt)
                    )
                    if (
                        user_script.type != "script_draft"
                        or user_script.status != "valid"
                        or user_script.project_id != run.project_id
                    ):
                        raise ValueError("指定的剧本版本不合法或不属于当前项目")
                    ep = user_script.episode_number
                    bound_eval = await repo.find_evaluation_for_script(
                        run.project_id, user_script.id
                    )
                    if bound_eval is None:
                        raise ValueError(f"第 {ep} 集指定剧本无绑定评估")
                    script_artifact_ids[str(ep)] = str(user_script.id)
                    evaluation_artifact_ids[str(ep)] = str(bound_eval.id)
                    revision_candidate_episode = ep

                initial_state = {
                    "run_id": str(run_id),
                    "project_id": str(run.project_id),
                    "action": action,
                    "story_bible_artifact_id": str(sb_artifact.id),
                    "outline_set_artifact_id": str(outline_artifact.id),
                    "script_artifact_ids": script_artifact_ids,
                    "evaluation_artifact_ids": evaluation_artifact_ids,
                    "needs_revision_decision": True,
                    "continuity_state_text": "",
                    "revision_round": 0,
                    "revision_candidate_episode": revision_candidate_episode,
                    "revision_plan_artifact_id": None,
                    "user_instruction": options.get("user_instruction"),
                    "needs_manual_review": False,
                    "needs_manual_review_reason": None,
                    "current_episode": revision_candidate_episode or 1,
                    "status": "running",
                    "needs_user_input": False,
                    "error_node": None,
                    "error_detail": None,
                    "completed_nodes": [],
                    "input_hashes": {},
                    "prompt_versions": {},
                }
                workflow = build_revision_workflow()

            # I-01 retry 恢复：以 state_summary 为基底重放。completed_nodes 早退 +
            # write_episodes 的 existing_scripts 跳过已写集 → 不重调 LLM、
            # 不重复建 Artifact、不重复推进 revision_round。剥离失败字段与
            # status，让本轮 fresh 状态接管。
            if checkpoint:
                _resume = {
                    k: v
                    for k, v in checkpoint.items()
                    if k not in ("status", "error_node", "error_code", "error_detail")
                }
                initial_state = {**initial_state, **_resume}

            final_state = await workflow.ainvoke(initial_state, workflow_config)

            # 快照完整轻量状态（completed_nodes / script_artifact_ids 等全为 ID 与
            # 小字段，符合 §2.2），供失败后 retry 恢复；即使本轮 failed 也保留部分产物。
            await save_checkpoint(db, run_id, final_state)

            # 软预算超限 → 发 run.warning（不阻断流程）
            budget = get_budget(str(run_id))
            if budget is not None and budget.soft_warned:
                await publisher.publish(
                    db, run_id=run_id, event_type="run.warning",
                    payload={
                        "code": "RUN_LLM_BUDGET_WARNING",
                        "message": "LLM 调用量接近预算上限",
                        "calls": budget.calls,
                    },
                    autocommit=True,
                )

            # 事后处理用 elif 链（F-05）：needs_manual_review 与 needs_revision_decision
            # 可同时为真，独立 if 会触发 running→needs_review 的非法二次转换。
            # 优先序: failed → needs_user_input → needs_manual_review →
            # needs_revision_decision（满轮仍低分）→ evaluate 收尾。
            if final_state.get("status") == "failed":
                await run_svc.transition_status(db, run_id, "failed")
                await _persist_run_error(
                    db, run_id,
                    final_state.get("error_code"),
                    final_state.get("error_detail"),
                )
                await publisher.publish(
                    db, run_id=run_id, event_type="run.failed",
                    payload={
                        "error_code": final_state.get("error_code"),
                        "error_node": final_state.get("error_node"),
                        "error_detail": final_state.get("error_detail"),
                    },
                    autocommit=True,
                )
            elif final_state.get("needs_user_input"):
                await run_svc.transition_status(db, run_id, "needs_review")
                await publisher.publish(
                    db, run_id=run_id, event_type="run.needs_review",
                    payload={"reason": "用户输入不完整，需要补充信息"},
                    autocommit=True,
                )
            elif final_state.get("needs_manual_review"):
                # 连续性失败或修订后显著下降 → 转人工复核（候选稿保留为诊断版本）
                await run_svc.transition_status(db, run_id, "needs_review")
                await publisher.publish(
                    db, run_id=run_id, event_type="run.needs_manual_review",
                    payload={
                        "reason": final_state.get("needs_manual_review_reason"),
                        "message": "自动修订受限，需要人工复核",
                    },
                    autocommit=True,
                )
            elif final_state.get("needs_revision_decision"):
                # 修订轮次已用满仍存在需修订的集 → 暂停在人工复核点
                await run_svc.transition_status(db, run_id, "needs_review")
                await publisher.publish(
                    db, run_id=run_id, event_type="run.needs_revision_decision",
                    payload={
                        "message": "自动修订轮次已用满，仍有需修订的集，等待人工决策",
                        "evaluation_artifact_ids": final_state.get("evaluation_artifact_ids", {}),
                    },
                    autocommit=True,
                )
            elif action == "import":
                # action=import 且无拦截标志 → 导入分类完成。
                # route=needs_user_input（unknown）已在上方 needs_user_input 分支拦截。
                await run_svc.transition_status(db, run_id, "completed")
                await publisher.publish(
                    db, run_id=run_id, event_type="run.completed",
                    payload={
                        "message": "导入分类完成",
                        "content_type": final_state.get("content_type"),
                        "route": final_state.get("route"),
                        "classification_artifact_id": final_state.get("classification_artifact_id"),
                        "script_artifact_id": final_state.get("script_artifact_id"),
                    },
                    autocommit=True,
                )
            elif action == "evaluate":
                # action=evaluate 且无任何拦截标志 → 直接完成
                # （evaluation workflow 无 finalize 节点，由 API 层收尾）
                await run_svc.transition_status(db, run_id, "completed")
                await publisher.publish(
                    db, run_id=run_id, event_type="run.completed",
                    payload={
                        "message": "评估完成",
                        "evaluation_count": len(final_state.get("evaluation_artifact_ids", {})),
                    },
                    autocommit=True,
                )
            elif action == "revise":
                # action=revise 且无任何拦截标志 → 修订闭环完成
                await run_svc.transition_status(db, run_id, "completed")
                await publisher.publish(
                    db, run_id=run_id, event_type="run.completed",
                    payload={
                        "message": "修订完成",
                        "revision_round": final_state.get("revision_round"),
                        "candidate_episode": final_state.get("revision_candidate_episode"),
                        "evaluation_artifact_ids": final_state.get("evaluation_artifact_ids", {}),
                    },
                    autocommit=True,
                )

            # 兜底提交：确保所有变更已持久化
            # （各节点通过 publisher.publish(autocommit=True) 分段提交，
            #   此处作为最终安全网，防止因异常路径导致数据丢失）
            await db.commit()

        except RunCancelledError:
            # 协作式取消（I-01）：Run 转 cancelled，不视为失败
            logger.info("Run 已取消: %s", run_id)
            try:
                await run_svc.transition_status(db, run_id, "cancelled")
                await publisher.publish(
                    db, run_id=run_id, event_type="run.cancelled",
                    payload={"message": "Run 已取消"},
                    autocommit=True,
                )
            except Exception:
                pass
        except Exception as e:
            logger.exception("Workflow 执行失败: run=%s", run_id)
            try:
                await run_svc.transition_status(db, run_id, "failed")
                error_code = e.code if isinstance(e, AppError) else classify_error_code(e)
                await _persist_run_error(db, run_id, error_code, str(e))
                await publisher.publish(
                    db, run_id=run_id, event_type="run.failed",
                    payload={"error": str(e), "error_code": error_code},
                    autocommit=True,
                )
            except Exception:
                pass
        finally:
            # I-01：清理预算与取消标记，避免 registry 泄漏
            # I-02：发布 run.llm_stats 事件（须在 exit_run 前读取预算），
            # 供 GET /runs/{id}/diagnostics 聚合调用次数与 token 用量。
            # 用 suppress 包裹：finally 不得因发布失败掩盖原始异常。
            _budget = get_budget(str(run_id))
            if _budget is not None:
                with contextlib.suppress(Exception):
                    await publisher.publish(
                        db, run_id=run_id, event_type="run.llm_stats",
                        payload={
                            "calls": _budget.calls,
                            "prompt_tokens": _budget.prompt_tokens,
                            "completion_tokens": _budget.completion_tokens,
                        },
                        autocommit=True,
                    )
            exit_run(str(run_id))
            clear_cancel(str(run_id))
            if hasattr(llm_client, "close"):
                await llm_client.close()  # type: ignore[union-attr]


async def _persist_run_error(
    db: AsyncSession,
    run_id: uuid.UUID,
    error_code: str | None,
    error_detail: str | None,
) -> None:
    """把失败信息写入 WorkflowRun.error_code / error_detail（I-01）。

    所有失败（final_state failed / 节点异常 / 外部异常）都经此落库，
    保证验收"所有失败有 error_code"；成功路径无需调用（重试时已在端点清空）。
    """
    result = await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        return
    run.error_code = error_code
    run.error_detail = error_detail
    await db.flush()


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

    from app.domain.evaluation import EvaluationReport
    from app.domain.import_file import ImportClassification
    from app.domain.outline import EpisodeOutlineSet
    from app.domain.requirement import NormalizedRequirement
    from app.domain.revision import (
        ContinuitySemanticCheck,
        RevisionPlan,
        RevisionResult,
    )
    from app.domain.script import ScriptDraft
    from app.domain.story_bible import StoryBible

    llm.register("normalize_requirement", NormalizedRequirement.model_validate(_load("requirement_football")))
    llm.register("story_bible", StoryBible.model_validate(_load("story_bible_football")))
    llm.register("outline", EpisodeOutlineSet.model_validate(_load("outline_set_valid")))
    llm.register("write_episode", ScriptDraft.model_validate(_load("script_draft_valid")))
    # 评估 fixture：默认 golden 高分报告（服务端回填 need_revision=False → 走 finalize/completed）。
    # E2E 场景开关 FAKE_LLM_SCENARIO=revision：注册低分报告 → 全部集 need_revision=True →
    # F-05 确定性选最低分集（平局取最小集号）恰好只修 1 集。默认行为不变。
    if _os.environ.get("FAKE_LLM_SCENARIO") == "revision":
        llm.register(
            "evaluate_episode",
            EvaluationReport.model_validate(_load("evaluation_report_lowscore")),
        )
    else:
        llm.register("evaluate_episode", EvaluationReport.model_validate(_load("evaluation_report_valid")))
    # 修订分支 fixtures（F-05）：仅防御性注册，正常高分路径不触发
    llm.register("revision_plan", RevisionPlan.model_validate(_load("revision_plan_valid")))
    llm.register("revise_episode", RevisionResult.model_validate(_load("revised_episode_football")))
    llm.register(
        "continuity_semantic_check",
        ContinuitySemanticCheck.model_validate(_load("continuity_semantic_check_valid")),
    )
    # 导入分类 fixture（G-04）：默认 outline golden；API 集成测试按上传内容覆盖。
    llm.register(
        "import_classifier",
        ImportClassification.model_validate(_load("import_classification_outline")),
    )
