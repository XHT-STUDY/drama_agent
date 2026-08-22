"""数据库驱动的 Workflow 调度器。

进程内 Task 只负责唤醒；可执行 Run、租约和恢复次数全部以 PostgreSQL
为准。领取使用 FOR UPDATE SKIP LOCKED，支持多实例竞争与进程重启恢复。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AppError
from app.db.models.workflow_run import WorkflowRun
from app.events.publisher import EventPublisher
from app.llm.budget import enter_run, exit_run, get_budget
from app.workflows.checkpoint import (
    RunCancelledError,
    classify_error_code,
    clear_cancel,
    save_checkpoint,
)
from app.workflows.persistence import open_workflow_checkpointer

logger = logging.getLogger(__name__)

WorkflowExecutor = Callable[[uuid.UUID, str, dict[str, Any], str], Awaitable[None]]


class WorkflowDispatcher:
    """用数据库租约领取、续租并执行 WorkflowRun。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        owner: str | None = None,
        executor: WorkflowExecutor,
        lease_seconds: float = 30.0,
        max_attempts: int = 3,
    ) -> None:
        self._session_factory = session_factory
        self.owner = owner or f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}"
        self._executor = executor
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    async def claim_next(self) -> WorkflowRun | None:
        """原子领取一个 queued 或租约已过期的 running Run。"""
        now = datetime.now(UTC)
        expired = or_(
            WorkflowRun.lease_expires_at.is_(None),
            WorkflowRun.lease_expires_at <= now,
        )
        eligible = or_(
            WorkflowRun.status == "queued",
            (WorkflowRun.status == "running") & expired,
        )

        async with self._session_factory() as db, db.begin():
            exhausted_result = await db.execute(
                select(WorkflowRun)
                .where(eligible, WorkflowRun.attempt_count >= self._max_attempts)
                .order_by(WorkflowRun.created_at)
                .with_for_update(skip_locked=True)
            )
            for exhausted in exhausted_result.scalars().all():
                exhausted.status = "failed"
                exhausted.error_code = "WORKFLOW_RECOVERY_EXHAUSTED"
                exhausted.error_detail = f"Workflow 恢复次数已达到上限 {self._max_attempts}"
                exhausted.lease_owner = None
                exhausted.lease_expires_at = None
                await EventPublisher().publish(
                    db,
                    run_id=exhausted.id,
                    event_type="run.failed",
                    payload={"error_code": exhausted.error_code},
                )

            result = await db.execute(
                select(WorkflowRun)
                .where(eligible, WorkflowRun.attempt_count < self._max_attempts)
                .order_by(WorkflowRun.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            run = result.scalar_one_or_none()
            if run is None:
                return None

            was_queued = run.status == "queued"
            run.status = "running"
            run.lease_owner = self.owner
            run.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            run.attempt_count += 1
            if was_queued:
                await EventPublisher().publish(
                    db,
                    run_id=run.id,
                    event_type="run.running",
                )
            await db.flush()
            return run

    async def renew_lease(self, run_id: uuid.UUID) -> bool:
        """仅当前持有者可续租。"""
        async with self._session_factory() as db, db.begin():
            result = await db.execute(
                select(WorkflowRun)
                .where(
                    WorkflowRun.id == run_id,
                    WorkflowRun.status == "running",
                    WorkflowRun.lease_owner == self.owner,
                )
                .with_for_update()
            )
            run = result.scalar_one_or_none()
            if run is None:
                return False
            run.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self._lease_seconds)
            return True

    async def _heartbeat(self, run_id: uuid.UUID) -> None:
        interval = max(0.1, self._lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            if not await self.renew_lease(run_id):
                return

    async def run_once(self) -> bool:
        """领取并执行一个 Run；没有可领取项时返回 False。"""
        run = await self.claim_next()
        if run is None:
            return False

        heartbeat = asyncio.create_task(self._heartbeat(run.id))
        try:
            await self._executor(
                run.id,
                run.action,
                run.config_snapshot or {},
                self.owner,
            )
        except Exception as exc:
            logger.exception("Workflow executor 未处理异常: run=%s", run.id)
            await self._mark_failed(run.id, "WORKFLOW_EXECUTOR_ERROR", str(exc))
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        return True

    async def _mark_failed(self, run_id: uuid.UUID, code: str, detail: str) -> None:
        async with self._session_factory() as db, db.begin():
            result = await db.execute(
                select(WorkflowRun)
                .where(
                    WorkflowRun.id == run_id,
                    WorkflowRun.lease_owner == self.owner,
                )
                .with_for_update()
            )
            run = result.scalar_one_or_none()
            if run is None or run.status != "running":
                return
            run.status = "failed"
            run.error_code = code
            run.error_detail = detail[:2000]
            run.lease_owner = None
            run.lease_expires_at = None
            await EventPublisher().publish(
                db,
                run_id=run.id,
                event_type="run.failed",
                payload={"error_code": code},
            )

    async def _drain(self) -> None:
        await asyncio.sleep(0.1)
        while not self._closed and await self.run_once():
            pass

    def wake(self) -> None:
        """Best-effort 唤醒；Run 是否可执行仍由数据库决定。"""
        if self._closed:
            return
        task = asyncio.create_task(self._drain())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def startup(self) -> None:
        """启动扫描：领取遗留 queued 和过期租约。"""
        self.wake()

    async def close(self) -> None:
        self._closed = True
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()


# ---- 全局调度器（API 仅唤醒，数据库才是执行事实源） ----

_dispatcher: WorkflowDispatcher | None = None
_dispatcher_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_dispatcher() -> WorkflowDispatcher:
    """获取与当前 DB session factory 绑定的进程级 Dispatcher。"""
    global _dispatcher, _dispatcher_session_factory
    import app.db.session as db_session

    factory = db_session._async_session_factory
    if factory is None:
        raise RuntimeError("数据库未初始化，无法启动 WorkflowDispatcher")
    if _dispatcher is None or _dispatcher_session_factory is not factory:
        _dispatcher = WorkflowDispatcher(factory, executor=_execute_workflow)
        _dispatcher_session_factory = factory
    return _dispatcher


def schedule_worker(
    run_id: uuid.UUID,
    action: str,
    config_snapshot: dict[str, Any],
) -> None:
    """兼容 API 调用的 best-effort 唤醒；参数不作为执行事实源。"""
    del run_id, action, config_snapshot
    get_dispatcher().wake()


async def startup_dispatcher() -> None:
    await get_dispatcher().startup()


async def shutdown_dispatcher() -> None:
    global _dispatcher, _dispatcher_session_factory
    if _dispatcher is not None:
        await _dispatcher.close()
    _dispatcher = None
    _dispatcher_session_factory = None


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
    parsed = await FileParserTool(upload_max_bytes=settings.upload_max_bytes).execute(
        filename=upload.original_name, data=data
    )
    return parsed.text


async def _execute_workflow(
    run_id: uuid.UUID,
    action: str,
    config_snapshot: dict[str, Any],
    lease_owner: str,
) -> None:
    """在后台执行 Creation Workflow。

    从数据库加载 Run，创建 Workflow 运行时上下文，
    通过 LangGraph 执行完整创作流程。
    """

    from app.llm.protocol import LLMClient

    settings = Settings()
    llm_client: LLMClient
    if settings.app_env == "test":
        from app.llm.fake import FakeLLM

        llm_client = FakeLLM(seed=42)
        _register_fake_fixtures(llm_client)
    else:
        from app.llm.openai_compatible import OpenAICompatibleLLM

        llm_client = OpenAICompatibleLLM(settings)

    checkpointer_context = open_workflow_checkpointer(settings)
    checkpointer = await checkpointer_context.__aenter__()

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
            if run.status != "running" or run.lease_owner != lease_owner:
                return
            action = run.action
            config_snapshot = run.config_snapshot or {}

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
                await run_svc.transition_status(db, run_id, "completed", lease_owner=lease_owner)
                await publisher.publish(
                    db,
                    run_id=run_id,
                    event_type="run.completed",
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

            if action == "platform_smoke":
                await run_svc.transition_status(db, run_id, "completed", lease_owner=lease_owner)
                await publisher.publish(
                    db,
                    run_id=run_id,
                    event_type="run.completed",
                    payload={"message": "平台冒烟完成", "progress": 1.0},
                    autocommit=True,
                )
                return
            if action not in ("create_script", "evaluate", "revise", "import"):
                raise AppError(
                    detail=f"不支持的 Workflow action: {action}",
                    status_code=400,
                    code="UNSUPPORTED_ACTION",
                )

            options: dict[str, Any] = config_snapshot.get("options", {})
            user_input = options.get("user_input", "")
            # G-06 导入路径：config.upload_id 提供时优先用上传文件内容作为创作输入
            # （"上传 Outline → 创作"：导入分类 route=create 后，客户端带 upload_id
            #  重跑 create_script，Worker 解析上传文本注入创作管线）。
            upload_id_cfg = config_snapshot.get("upload_id")
            if upload_id_cfg:
                upload_text = await _resolve_upload_text(db, run.project_id, upload_id_cfg)
                if upload_text:
                    user_input = upload_text
            if not user_input:
                user_input = "一个被青训队抛弃的足球少年逆袭故事"

            progress_log: list[dict[str, Any]] = []

            def progress_callback(node: str, event: str, progress: float) -> None:
                progress_log.append({"node": node, "event": event, "progress": progress})

            workflow_config: dict[str, Any] = {
                "configurable": {
                    "thread_id": str(run_id),
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
            # （CreationState / ImportState 等），此处用 Any 避免 TypedDict 强约束。
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
                workflow = build_creation_workflow(checkpointer=checkpointer)
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
                    "script_artifact_ids": {str(ep): sid for ep, sid in latest_per_episode.items()},
                    "evaluation_artifact_ids": {},
                    "needs_revision_decision": False,
                    "status": "running",
                    "completed_nodes": [],
                    "input_hashes": {},
                    "prompt_versions": {},
                }
                workflow = build_evaluation_workflow(checkpointer=checkpointer)
            elif action == "import":
                # action=import → 单节点导入分类工作流（G-04）：
                # parse（FileStore + Parser）→ classify（规则+LLM）→ 持久化
                # import_classification Artifact → route。
                workflow = build_import_workflow(checkpointer=checkpointer)
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
                sb_artifact = await store.get_latest(db, run.project_id, "story_bible", 1)
                outline_artifact = await store.get_latest(db, run.project_id, "episode_outline_set", 1)
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
                script_artifact_ids = {str(ep): sid for ep, sid in latest_scripts.items()}
                evaluation_artifact_ids: dict[str, str] = {}
                for ep, sid in latest_scripts.items():
                    bound = await repo.find_evaluation_for_script(run.project_id, uuid.UUID(sid))
                    if bound is not None:
                        evaluation_artifact_ids[str(ep)] = str(bound.id)

                # 用户指定剧本覆盖（防御性重校验：type/status/project + 绑定评估）
                revision_candidate_episode: int | None = options.get("episode_number")
                script_id_opt = options.get("script_artifact_id")
                if script_id_opt:
                    user_script = await store.get_version(db, uuid.UUID(script_id_opt))
                    if (
                        user_script.type != "script_draft"
                        or user_script.status != "valid"
                        or user_script.project_id != run.project_id
                    ):
                        raise ValueError("指定的剧本版本不合法或不属于当前项目")
                    ep = user_script.episode_number
                    bound_eval = await repo.find_evaluation_for_script(run.project_id, user_script.id)
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
                workflow = build_revision_workflow(checkpointer=checkpointer)

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
                    db,
                    run_id=run_id,
                    event_type="run.warning",
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
                await run_svc.transition_status(db, run_id, "failed", lease_owner=lease_owner)
                await _persist_run_error(
                    db,
                    run_id,
                    final_state.get("error_code"),
                    final_state.get("error_detail"),
                )
                await publisher.publish(
                    db,
                    run_id=run_id,
                    event_type="run.failed",
                    payload={
                        "error_code": final_state.get("error_code"),
                        "error_node": final_state.get("error_node"),
                        "error_detail": final_state.get("error_detail"),
                    },
                    autocommit=True,
                )
            elif final_state.get("needs_user_input"):
                await run_svc.transition_status(db, run_id, "needs_review", lease_owner=lease_owner)
                await publisher.publish(
                    db,
                    run_id=run_id,
                    event_type="run.needs_review",
                    payload={"reason": "用户输入不完整，需要补充信息"},
                    autocommit=True,
                )
            elif final_state.get("needs_manual_review"):
                # 连续性失败或修订后显著下降 → 转人工复核（候选稿保留为诊断版本）
                await run_svc.transition_status(db, run_id, "needs_review", lease_owner=lease_owner)
                await publisher.publish(
                    db,
                    run_id=run_id,
                    event_type="run.needs_manual_review",
                    payload={
                        "reason": final_state.get("needs_manual_review_reason"),
                        "message": "自动修订受限，需要人工复核",
                    },
                    autocommit=True,
                )
            elif final_state.get("needs_revision_decision"):
                # 修订轮次已用满仍存在需修订的集 → 暂停在人工复核点
                await run_svc.transition_status(db, run_id, "needs_review", lease_owner=lease_owner)
                await publisher.publish(
                    db,
                    run_id=run_id,
                    event_type="run.needs_revision_decision",
                    payload={
                        "message": "自动修订轮次已用满，仍有需修订的集，等待人工决策",
                        "evaluation_artifact_ids": final_state.get("evaluation_artifact_ids", {}),
                    },
                    autocommit=True,
                )
            elif action == "import":
                # action=import 且无拦截标志 → 导入分类完成。
                # route=needs_user_input（unknown）已在上方 needs_user_input 分支拦截。
                await run_svc.transition_status(db, run_id, "completed", lease_owner=lease_owner)
                await publisher.publish(
                    db,
                    run_id=run_id,
                    event_type="run.completed",
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
                await run_svc.transition_status(db, run_id, "completed", lease_owner=lease_owner)
                await publisher.publish(
                    db,
                    run_id=run_id,
                    event_type="run.completed",
                    payload={
                        "message": "评估完成",
                        "evaluation_count": len(final_state.get("evaluation_artifact_ids", {})),
                    },
                    autocommit=True,
                )
            elif action == "revise":
                # action=revise 且无任何拦截标志 → 修订闭环完成
                await run_svc.transition_status(db, run_id, "completed", lease_owner=lease_owner)
                await publisher.publish(
                    db,
                    run_id=run_id,
                    event_type="run.completed",
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
                await run_svc.transition_status(db, run_id, "cancelled", lease_owner=lease_owner)
                await publisher.publish(
                    db,
                    run_id=run_id,
                    event_type="run.cancelled",
                    payload={"message": "Run 已取消"},
                    autocommit=True,
                )
            except Exception:
                pass
        except Exception as e:
            logger.exception("Workflow 执行失败: run=%s", run_id)
            try:
                await run_svc.transition_status(db, run_id, "failed", lease_owner=lease_owner)
                error_code = e.code if isinstance(e, AppError) else classify_error_code(e)
                await _persist_run_error(db, run_id, error_code, str(e))
                await publisher.publish(
                    db,
                    run_id=run_id,
                    event_type="run.failed",
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
                        db,
                        run_id=run_id,
                        event_type="run.llm_stats",
                        payload={
                            "calls": _budget.calls,
                            "prompt_tokens": _budget.prompt_tokens,
                            "completion_tokens": _budget.completion_tokens,
                        },
                        autocommit=True,
                    )
            exit_run(str(run_id))
            clear_cancel(str(run_id))
            with contextlib.suppress(Exception):
                await checkpointer_context.__aexit__(None, None, None)
            if hasattr(llm_client, "close"):
                await llm_client.close()


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

    # workflow_dispatcher.py → application/ → app/ → backend/
    _golden_dir = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))),
        "tests",
        "golden",
    )

    def _load(name: str) -> dict[str, Any]:
        path = _os.path.join(_golden_dir, f"{name}.json")
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
        if isinstance(data, dict) and "expected_output" in data:
            return cast(dict[str, Any], data["expected_output"])
        return cast(dict[str, Any], data)

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
