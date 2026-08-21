"""AgentTurn 专用 Repository。

提供数据库级幂等 get-or-create、行锁读取、planning lease 原子领取和状态迁移。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AgentStateTransitionError, IdempotencyKeyReusedError
from app.db.models.agent_turn import AgentTurn
from app.db.repositories.base import BaseRepository
from app.domain.agent_command import TURN_TRANSITIONS, AgentTurnStatus

_FINAL_TURN_TYPES = {
    "needs_input": "clarification",
    "answered": "answer",
    "action_proposed": "plan",
}


class AgentTurnRepository(BaseRepository):
    """AgentTurn 的并发安全持久化接口。"""

    _MUTABLE_FIELDS = frozenset(
        {
            "turn_type",
            "planner_output",
            "response_message_id",
            "planning_lease_owner",
            "planning_lease_expires_at",
            "error_code",
            "error_detail",
        }
    )

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AgentTurn)

    async def get_by_idempotency_key(
        self,
        project_id: uuid.UUID,
        idempotency_key: str,
    ) -> AgentTurn | None:
        """按项目和幂等键读取 Turn。"""
        stmt = select(AgentTurn).where(
            AgentTurn.project_id == project_id,
            AgentTurn.idempotency_key == idempotency_key,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        *,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
        user_message_id: uuid.UUID,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[AgentTurn, bool]:
        """原子创建 Turn；重复键返回既有记录并比较 request_hash。"""
        stmt = (
            insert(AgentTurn)
            .values(
                id=uuid.uuid4(),
                project_id=project_id,
                conversation_id=conversation_id,
                user_message_id=user_message_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                status="received",
                planning_attempt_count=0,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    AgentTurn.project_id,
                    AgentTurn.idempotency_key,
                ]
            )
            .returning(AgentTurn)
        )
        result = await self.session.execute(stmt)
        created = result.scalar_one_or_none()
        if created is not None:
            return created, True

        existing = await self.get_by_idempotency_key(project_id, idempotency_key)
        if existing is None:
            raise RuntimeError("AgentTurn idempotency conflict without existing row")
        if existing.request_hash != request_hash:
            raise IdempotencyKeyReusedError(detail="同一 idempotency_key 已用于不同的请求载荷")
        return existing, False

    async def get_for_update(self, turn_id: uuid.UUID) -> AgentTurn | None:
        """锁定并读取 Turn，供短事务完成状态迁移。"""
        stmt = (
            select(AgentTurn)
            .where(AgentTurn.id == turn_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def claim_planning_lease(
        self,
        turn_id: uuid.UUID,
        *,
        lease_owner: str,
        lease_expires_at: datetime,
        now: datetime | None = None,
    ) -> AgentTurn | None:
        """原子领取 received 或租约已过期的 planning Turn。"""
        current_time = now or datetime.now(UTC)
        stmt = (
            update(AgentTurn)
            .where(
                AgentTurn.id == turn_id,
                or_(
                    AgentTurn.status == "received",
                    (
                        (AgentTurn.status == "planning")
                        & or_(
                            AgentTurn.planning_lease_expires_at.is_(None),
                            AgentTurn.planning_lease_expires_at <= current_time,
                        )
                    ),
                ),
            )
            .values(
                status="planning",
                planning_lease_owner=lease_owner,
                planning_lease_expires_at=lease_expires_at,
                planning_attempt_count=AgentTurn.planning_attempt_count + 1,
            )
            .returning(AgentTurn)
        )
        result = await self.session.execute(stmt)
        claimed = result.scalar_one_or_none()
        if claimed is not None:
            await self.session.refresh(claimed)
        return claimed

    async def transition(
        self,
        turn_id: uuid.UUID,
        target_status: AgentTurnStatus,
        *,
        expected_statuses: set[AgentTurnStatus] | None = None,
        lease_owner: str | None = None,
        now: datetime | None = None,
        **values: Any,
    ) -> AgentTurn:
        """在行锁内执行受白名单约束的 Turn 状态迁移。"""
        turn = await self.get_for_update(turn_id)
        if turn is None:
            raise AgentStateTransitionError(
                detail=f"AgentTurn 不存在: {turn_id}",
                entity="AGENT_TURN",
            )
        if expected_statuses is not None and turn.status not in expected_statuses:
            raise AgentStateTransitionError(
                detail=(f"AgentTurn 当前状态 {turn.status} 不在预期状态 {sorted(expected_statuses)} 中"),
                entity="AGENT_TURN",
            )
        if target_status not in TURN_TRANSITIONS.get(turn.status, frozenset()):
            raise AgentStateTransitionError(
                detail=f"AgentTurn 不允许从 {turn.status} 迁移到 {target_status}",
                entity="AGENT_TURN",
            )
        if turn.status == "planning":
            current_time = now or datetime.now(UTC)
            lease_is_valid = (
                lease_owner is not None
                and turn.planning_lease_owner == lease_owner
                and turn.planning_lease_expires_at is not None
                and turn.planning_lease_expires_at > current_time
            )
            if not lease_is_valid:
                raise AgentStateTransitionError(
                    detail="只有有效 planning lease 的持有者可以写入最终响应",
                    entity="AGENT_TURN",
                )
        protected_fields = {"planning_lease_owner", "planning_lease_expires_at"} & set(values)
        if protected_fields:
            raise ValueError(f"planning lease fields are managed by repository: {protected_fields}")
        expected_turn_type = _FINAL_TURN_TYPES.get(target_status)
        supplied_turn_type = values.get("turn_type")
        if expected_turn_type is not None and supplied_turn_type not in (None, expected_turn_type):
            raise AgentStateTransitionError(
                detail=f"{target_status} 必须使用 turn_type={expected_turn_type}",
                entity="AGENT_TURN",
            )
        if expected_turn_type is not None:
            values["turn_type"] = expected_turn_type
        if target_status != "planning":
            values.setdefault("planning_lease_owner", None)
            values.setdefault("planning_lease_expires_at", None)
        unknown_fields = set(values) - self._MUTABLE_FIELDS
        if unknown_fields:
            raise ValueError(f"unsupported AgentTurn transition fields: {unknown_fields}")

        turn.status = target_status
        for field, value in values.items():
            setattr(turn, field, value)
        await self.session.flush()
        return turn
