"""RunService — WorkflowRun 状态机与生命周期管理。

负责：
- Run 创建（含 Idempotency-Key 去重）
- 状态机转换（queued→running→completed/failed/cancelled）
- 事件发布
- cancel/retry
- 确认门续跑（W1-01：行锁串行 + 持久收据 + 阶段世代校验）
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    AppError,
    IdempotencyKeyReusedError,
    NotFoundError,
    RunAlreadyActiveError,
    RunNotRetryableError,
    RunStageStaleError,
)
from app.db.models.project import Project
from app.db.models.workflow_event import WorkflowEvent
from app.db.models.workflow_run import WorkflowRun
from app.events.publisher import EventPublisher
from app.observability.metrics import workflow_runs_total
from app.workflows.checkpoint import request_cancel

# 合法状态转换（I-01：running → cancelled 允许协作式取消）
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancelled"},
    "running": {"completed", "failed", "needs_review", "cancelled"},
    "completed": set(),
    "failed": {"queued"},  # retry
    "cancelled": set(),
    "needs_review": {"queued"},  # 人工审查后可重试
}

# 门续跑后需要重新执行的批次节点（W1-01：completed_nodes 不清理则第二批
# 因早退守卫直接跳过写作与评估，Run 空转 completed）
_BATCH_NODES_TO_RERUN = ("write_episodes", "evaluate_episodes")


@dataclass
class ContinueResult:
    """continue_gated_run 的结果（W1-01）。

    replayed=True 表示同幂等键同请求命中已接受的收据——不新增阶段，
    accepted_view 是接受时的 RunResponse 快照，调用方原样重放。
    """

    run: WorkflowRun | None
    replayed: bool = False
    accepted_view: dict[str, Any] = field(default_factory=dict)


class RunService:
    """WorkflowRun 应用服务。"""

    def __init__(self) -> None:
        self._publisher = EventPublisher()

    @staticmethod
    def request_hash(action: str, config: dict[str, Any] | None) -> str:
        """生成稳定的请求指纹，键顺序不影响结果。"""
        payload = {"action": action, "config": config or {}}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # ---- 创建 ----

    async def create_run(
        self,
        db: AsyncSession,
        *,
        project_id: uuid.UUID,
        action: str,
        config: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> WorkflowRun:
        """创建新的 WorkflowRun。

        1. 幂等检查：相同 idempotency_key 返回已有 run
        2. 校验 project 存在
        3. INSERT workflow_run (status=queued)
        4. 发布 run.created 事件

        Raises:
            NotFoundError: 项目不存在
        """
        request_hash = self.request_hash(action, config)

        if idempotency_key:
            result = await db.execute(
                select(WorkflowRun).where(
                    WorkflowRun.project_id == project_id,
                    WorkflowRun.action == action,
                    WorkflowRun.idempotency_key == idempotency_key,
                )
            )
            existing = result.scalar_one_or_none()
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise IdempotencyKeyReusedError(detail="幂等键已用于不同请求")
                return existing

        # 校验项目存在
        proj_result = await db.execute(select(Project).where(Project.id == project_id))
        project = proj_result.scalar_one_or_none()
        if project is None or project.deleted_at is not None:
            raise NotFoundError(detail=f"项目不存在: {project_id}", code="PROJECT_NOT_FOUND")

        active_result = await db.execute(
            select(WorkflowRun).where(
                WorkflowRun.project_id == project_id,
                WorkflowRun.status.in_(("queued", "running")),
            )
        )
        active = active_result.scalar_one_or_none()
        if active is not None:
            raise RunAlreadyActiveError(detail=f"项目已有活跃 Run: {active.id}")

        # 创建 Run
        run = WorkflowRun(
            project_id=project_id,
            action=action,
            status="queued",
            config_snapshot=config,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        try:
            async with db.begin_nested():
                db.add(run)
                await db.flush()
        except IntegrityError:
            if idempotency_key is not None:
                result = await db.execute(
                    select(WorkflowRun).where(
                        WorkflowRun.project_id == project_id,
                        WorkflowRun.action == action,
                        WorkflowRun.idempotency_key == idempotency_key,
                    )
                )
                existing = result.scalar_one_or_none()
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise IdempotencyKeyReusedError(detail="幂等键已用于不同请求") from None
                    return existing
            raise RunAlreadyActiveError(detail="项目已有活跃 Run") from None

        # I-02：Run 创建计数（status=queued）
        workflow_runs_total.inc(action=action, status="queued")

        # 发布 run.created 事件
        await self._publisher.publish(
            db,
            run_id=run.id,
            event_type="run.created",
            payload={"action": action, "project_id": str(project_id)},
        )

        return run

    # ---- 状态机 ----

    async def transition_status(
        self,
        db: AsyncSession,
        run_id: uuid.UUID,
        new_status: str,
        *,
        lease_owner: str | None = None,
    ) -> WorkflowRun:
        """执行状态转换。

        校验合法性；如转换到终态则发布对应事件。
        """
        stmt = select(WorkflowRun).where(WorkflowRun.id == run_id)
        if lease_owner is not None:
            stmt = stmt.where(WorkflowRun.lease_owner == lease_owner)
        result = await db.execute(stmt)
        run = result.scalar_one_or_none()
        if run is None:
            if lease_owner is not None:
                raise AppError(
                    detail="Workflow 租约已丢失，拒绝旧 Worker 写入终态",
                    status_code=409,
                    code="WORKFLOW_LEASE_LOST",
                )
            raise NotFoundError(detail=f"Run 不存在: {run_id}", code="RUN_NOT_FOUND")

        current = run.status
        allowed = _VALID_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise AppError(
                detail=f"不允许从 {current} 转换到 {new_status}",
                status_code=409,
                code="INVALID_TRANSITION",
            )

        run.status = new_status
        if new_status not in ("queued", "running"):
            run.lease_owner = None
            run.lease_expires_at = None
        await db.flush()

        # I-02：状态变更计数（action 低基数；run_id 不入标签）
        workflow_runs_total.inc(action=run.action, status=new_status)

        # 发布状态变更事件
        event_type = f"run.{new_status}"
        await self._publisher.publish(db, run_id=run_id, event_type=event_type)

        return run

    async def cancel_run(self, db: AsyncSession, run_id: uuid.UUID) -> WorkflowRun:
        """取消 Run（I-01 协作式）。

        queued → 立即转为 cancelled；running → 置内存取消标记，工作流各节点
        在安全点检查后退出，由 worker 统一把 Run 转为 cancelled（保证 cancel
        后不创建新 Artifact）。已完成/失败/人工审查态不可取消。
        """
        result = await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
        run = result.scalar_one_or_none()
        if run is None:
            raise NotFoundError(detail=f"Run 不存在: {run_id}", code="RUN_NOT_FOUND")

        if run.status == "queued":
            return await self.transition_status(db, run_id, "cancelled")
        if run.status == "running":
            request_cancel(str(run_id))
            return run
        raise AppError(
            detail=f"Run 当前状态 {run.status} 不可取消",
            status_code=409,
            code="INVALID_TRANSITION",
        )

    async def get_run(self, db: AsyncSession, run_id: uuid.UUID) -> WorkflowRun:
        """查询 Run 详情。"""
        result = await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
        run = result.scalar_one_or_none()
        if run is None:
            raise NotFoundError(detail=f"Run 不存在: {run_id}", code="RUN_NOT_FOUND")
        return run

    async def continue_gated_run(
        self,
        db: AsyncSession,
        run_id: uuid.UUID,
        *,
        batch_size: int | None = None,
        expected_stage_generation: int,
        idempotency_key: str,
    ) -> ContinueResult:
        """确认门续跑（L-3/L-4，W1-01 幂等收据版）：分段创作停在 stage_gate 后继续执行。

        续跑事实源只此一份，REST 端点、对话短路（确认/继续短语）与
        continue 意图确认共用本方法。W1-01 语义：
        - Run 行锁串行确认；同 (run_id, idempotency_key) 收据重放返回原
          接受快照、不新增阶段；同键不同参数 409 IDEMPOTENCY_KEY_REUSED；
          expected_stage_generation 与当前世代不符 409 RUN_STAGE_STALE
          （旧请求重放 / 并发确认只有一个胜者）；
        - 每次合法接受：stage_generation + 1、attempt_count 置零（批次
          推进不耗故障重试预算）；收据（run.continue_accepted 事件）与
          Run/Action 变更同事务提交，无 LLM 调用；
        - 剥离 stage_gate / stop_after / needs_manual_review 标记与上一批
          的 write/evaluate 完成标记（不清理则第二批空转）；
        - outline 门：大纲刷新为项目最新 valid（暂停期间聊天改版生效）；
        - batch_size 提供时进入批模式：options.script_count = 已有集数 +
          batch（不超过目标），本批完成后再次停在 scripts 门；缺省写剩余
          全部并把 script_count 恢复为项目目标（不沿用上一批终点）；
        - needs_review → queued，Worker 从 checkpoint 恢复——已完成剧本
          引用保留，不重生已写集。

        Raises:
            RunAlreadyActiveError: Run 正在执行，不可重复续跑
            RunNotRetryableError: 非 needs_review 或不在 stage_gate 上
            IdempotencyKeyReusedError: 同幂等键用于不同请求参数
            RunStageStaleError: expected_stage_generation 与当前世代不符
        """
        # 行锁串行确认：与 Dispatcher 领取（FOR UPDATE SKIP LOCKED）及
        # 并发 continue（按钮/聊天）互斥，只有一个胜者能推进世代
        result = await db.execute(
            select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update()
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise NotFoundError(detail=f"Run 不存在: {run_id}", code="RUN_NOT_FOUND")

        # 收据重放：同幂等键同请求 → 返回原接受快照，不新增阶段。
        # 必须先于活跃检查——接受后的 Run 已回到 queued/running，重放
        # 此时仍应拿到收据而非 RUN_ALREADY_ACTIVE
        request_hash = self.continue_request_hash(batch_size, expected_stage_generation)
        receipt = await self._find_continue_receipt(db, run_id, idempotency_key)
        if receipt is not None:
            stored = receipt.payload or {}
            if stored.get("request_hash") != request_hash:
                raise IdempotencyKeyReusedError(
                    detail="续跑幂等键已用于不同请求参数",
                )
            accepted = dict(stored.get("accepted_response") or {})
            return ContinueResult(run=None, replayed=True, accepted_view=accepted)

        if run.status in ("queued", "running"):
            raise RunAlreadyActiveError(detail=f"Run 正在执行（{run.status}），不可重复续跑")
        summary = run.state_summary or {}
        gate = summary.get("stage_gate")
        if run.status != "needs_review" or gate not in ("outline", "scripts"):
            raise RunNotRetryableError(
                detail=(
                    f"Run 不可续跑（状态 {run.status}，"
                    f"stage_gate={gate or '无'}）：仅分段创作/剧本分批的确认门可继续"
                )
            )

        # 世代校验：防止旧请求/旧计划重放多写一批
        if expected_stage_generation != run.stage_generation:
            raise RunStageStaleError(
                detail=(
                    f"续跑请求基于旧进度（expected_stage_generation="
                    f"{expected_stage_generation}，当前 {run.stage_generation}）；"
                    "任务已被其他确认推进，请刷新后基于当前门重新操作"
                ),
                current_stage_generation=run.stage_generation,
            )

        # ---- 接受：世代推进 + 批次重置 + 门字段剥离（同一事务） ----
        run.stage_generation += 1
        run.attempt_count = 0

        # 剥离分段门字段；清掉复核标记；清掉上一批的完成标记与修订决策
        resumed = {
            k: v
            for k, v in summary.items()
            if k
            not in (
                "stage_gate",
                "stop_after",
                "needs_manual_review",
                "needs_manual_review_reason",
                "needs_revision_decision",
            )
        }
        resumed["completed_nodes"] = [
            n for n in (resumed.get("completed_nodes") or []) if n not in _BATCH_NODES_TO_RERUN
        ]
        # 暂停期间大纲可能被 revise_outline 更新 → 刷新为最新 valid
        from app.artifacts.store import ArtifactStore

        latest_outline = await ArtifactStore().get_latest(
            db, run.project_id, "episode_outline_set", 1
        )
        if gate == "outline" and latest_outline is not None:
            resumed["outline_set_artifact_id"] = str(latest_outline.id)
        run.state_summary = resumed

        config = dict(run.config_snapshot or {})
        options = dict(config.get("options") or {})
        options.pop("stop_after", None)
        existing = len(resumed.get("script_artifact_ids") or {})
        target = int(options.get("outline_count") or existing)
        if target <= existing:
            # 项目目标缺失时回查 Project 行（W1-01：恢复"项目目标"而非沿用上批终点）
            proj = await db.get(Project, run.project_id)
            if proj is not None:
                target = max(proj.target_episode_count, existing)
        # L-4 批模式：本批终点 = 已有集数 + batch（不超过目标集数）
        if batch_size is not None:
            end = min(existing + batch_size, target)
            if end > existing:
                options["script_count"] = end
                options["stop_after"] = "scripts"
                resumed_stop = dict(resumed)
                resumed_stop["stop_after"] = "scripts"
                run.state_summary = resumed_stop
        else:
            # 缺省写剩余全部：script_count 恢复为项目目标。上一批留下的
            # 终点若不清除，write_episodes 只会写到旧终点——"写完剩余
            # 全部"实际一个字都不写；options 原无该字段时也必须补上，
            # 否则 Worker 端默认值会截断项目目标
            options["script_count"] = target
        config["options"] = options
        run.config_snapshot = config

        run.error_code = None
        run.error_detail = None
        await db.flush()
        await self.transition_status(db, run_id, "queued")

        # 持久收据：与 Run 变更同事务提交（不 autocommit——由调用方提交，
        # 保证收据与 Action 归属切换原子落地）；SSE 通知由事件层在提交后发出
        accepted_view = self._accepted_run_view(run)
        await self._publisher.publish(
            db,
            run_id=run_id,
            event_type="run.continue_accepted",
            payload={
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "expected_stage_generation": expected_stage_generation,
                "accepted_stage_generation": run.stage_generation,
                "batch_size": batch_size,
                "stage_gate_cleared": str(gate),
                "accepted_response": accepted_view,
            },
        )
        return ContinueResult(run=run, replayed=False, accepted_view=accepted_view)

    @staticmethod
    def continue_request_hash(
        batch_size: int | None, expected_stage_generation: int | None
    ) -> str:
        """continue 请求参数的规范化指纹（收据重放比对用）。"""
        payload = {
            "batch_size": batch_size,
            "expected_stage_generation": expected_stage_generation,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _accepted_run_view(run: WorkflowRun) -> dict[str, Any]:
        """接受时刻的 RunResponse 等价快照（重放时原样返回）。

        字段形状与 api/v1/runs.py 的 RunResponse.from_orm 保持一致——
        该 dict 会作为收据 payload 持久化，形状即契约；RunResponse 增删
        字段时此处必须同步（application 层不 import API 层，故手写映射）。
        """
        config = run.config_snapshot or {}
        return {
            "run_id": str(run.id),
            "project_id": str(run.project_id),
            "action": run.action,
            "status": run.status,
            "config_snapshot": run.config_snapshot,
            "error_code": run.error_code,
            "error_detail": run.error_detail,
            "agent_action_id": config.get("agent_action_id"),
            "stage_gate": (run.state_summary or {}).get("stage_gate"),
            "stage_generation": run.stage_generation,
            "created_at": run.created_at.isoformat() if run.created_at else "",
            "updated_at": run.updated_at.isoformat() if run.updated_at else "",
        }

    @staticmethod
    async def _find_continue_receipt(
        db: AsyncSession, run_id: uuid.UUID, idempotency_key: str
    ) -> WorkflowEvent | None:
        """查找同幂等键的已接受续跑收据（最新一条）。"""
        result = await db.execute(
            select(WorkflowEvent)
            .where(
                WorkflowEvent.run_id == run_id,
                WorkflowEvent.type == "run.continue_accepted",
                WorkflowEvent.payload["idempotency_key"].astext == idempotency_key,
            )
            .order_by(WorkflowEvent.sequence.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_runs_by_project(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> list[WorkflowRun]:
        """按项目分页查询 Run 列表。"""
        stmt = (
            select(WorkflowRun)
            .where(WorkflowRun.project_id == project_id)
            .order_by(WorkflowRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())
