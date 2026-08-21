"""AgentAction 专用 Repository。

Action 的状态变更必须在行锁内通过白名单状态机，避免重复确认覆盖 run_id。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AgentStateTransitionError
from app.db.models.agent_action import AgentAction
from app.db.repositories.base import BaseRepository
from app.domain.agent_command import ACTION_TRANSITIONS, AgentActionStatus


class AgentActionRepository(BaseRepository):
    """AgentAction 的锁定读取与状态迁移接口。"""

    _MUTABLE_FIELDS = frozenset({"run_id", "result", "requires_confirmation"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AgentAction)

    async def get_for_update(self, action_id: uuid.UUID) -> AgentAction | None:
        """锁定并读取 Action，供确认和终态回写。"""
        stmt = (
            select(AgentAction)
            .where(AgentAction.id == action_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_turn_id(self, turn_id: uuid.UUID) -> AgentAction | None:
        """读取指定 Turn 产生的唯一 Action。"""
        stmt = select(AgentAction).where(AgentAction.agent_turn_id == turn_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def transition(
        self,
        action_id: uuid.UUID,
        target_status: AgentActionStatus,
        *,
        expected_statuses: set[AgentActionStatus] | None = None,
        **values: Any,
    ) -> AgentAction:
        """在行锁内执行受白名单约束的 Action 状态迁移。"""
        action = await self.get_for_update(action_id)
        if action is None:
            raise AgentStateTransitionError(
                detail=f"AgentAction 不存在: {action_id}",
                entity="AGENT_ACTION",
            )
        if expected_statuses is not None and action.status not in expected_statuses:
            raise AgentStateTransitionError(
                detail=(f"AgentAction 当前状态 {action.status} 不在预期状态 {sorted(expected_statuses)} 中"),
                entity="AGENT_ACTION",
            )
        if target_status not in ACTION_TRANSITIONS.get(action.status, frozenset()):
            raise AgentStateTransitionError(
                detail=f"AgentAction 不允许从 {action.status} 迁移到 {target_status}",
                entity="AGENT_ACTION",
            )
        requested_run_id = values.get("run_id")
        if action.run_id is not None and requested_run_id is not None and requested_run_id != action.run_id:
            raise AgentStateTransitionError(
                detail="AgentAction 已关联其他 WorkflowRun，不能替换",
                entity="AGENT_ACTION",
            )
        if target_status == "queued" and action.run_id is None and requested_run_id is None:
            raise AgentStateTransitionError(
                detail="AgentAction 进入 queued 前必须关联 WorkflowRun",
                entity="AGENT_ACTION",
            )
        unknown_fields = set(values) - self._MUTABLE_FIELDS
        if unknown_fields:
            raise ValueError(f"unsupported AgentAction transition fields: {unknown_fields}")

        action.status = target_status
        for field, value in values.items():
            setattr(action, field, value)
        await self.session.flush()
        return action
