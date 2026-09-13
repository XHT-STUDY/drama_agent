"""AgentOutcomeService 单元测试（J-09）。

核心锚点：
- test_deterministic_evidence_is_preferred_over_llm_judgment：
  确定性证据已定论（失败/人工复核）时，不得调用 LLM。
其余覆盖确定性规则（大纲影响→部分达成、语义约束合并、模型越权字段被忽略）。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.base import BaseAgent
from app.application.agent_outcome_service import AgentOutcomeService
from app.db.models.agent_action import AgentAction
from app.db.models.agent_turn import AgentTurn
from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.db.models.project import Project
from app.db.models.workflow_run import WorkflowRun
from app.llm.fake import FakeLLM
from app.llm.models import LLMCallResult


class ExplodeOnCallLLM(FakeLLM):
    """任何 generate_structured 调用都直接失败——用于证明未调用模型。"""

    async def generate_structured(
        self,
        schema: Any,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> LLMCallResult:
        raise AssertionError("确定性证据已定论，不应调用 LLM")


def _plan(intent: str, constraints: list[str] | None = None) -> dict[str, Any]:
    return {
        "goal": "测试目标",
        "intent": intent,
        "constraints": constraints or [],
        "steps": [],
    }


async def _seed(
    session: AsyncSession,
    *,
    intent: str = "revise_outline",
    run_status: str = "completed",
    constraints: list[str] | None = None,
    state_summary: dict[str, Any] | None = None,
) -> tuple[AgentAction, WorkflowRun]:
    project = Project(title="Outcome 测试", target_episode_count=10)
    session.add(project)
    await session.flush()
    conversation = Conversation(project_id=project.id, title="会话")
    session.add(conversation)
    await session.flush()
    message = Message(
        conversation_id=conversation.id, role="user", content="seed",
        kind="text", message_metadata={}, sequence=1,
    )
    session.add(message)
    await session.flush()
    run = WorkflowRun(
        project_id=project.id, action=intent, status=run_status,
        config_snapshot={}, state_summary=state_summary,
    )
    session.add(run)
    await session.flush()
    turn = AgentTurn(
        project_id=project.id, conversation_id=conversation.id,
        user_message_id=message.id, idempotency_key=f"seed-{uuid.uuid4().hex}",
        request_hash="0" * 64, status="action_proposed", turn_type="plan",
    )
    session.add(turn)
    await session.flush()
    action = AgentAction(
        project_id=project.id, conversation_id=conversation.id,
        agent_turn_id=turn.id, intent=intent, status="running",
        plan=_plan(intent, constraints), run_id=run.id,
    )
    session.add(action)
    await session.commit()
    return action, run


@pytest.mark.unit
@pytest.mark.asyncio
class TestAgentOutcomeService:
    async def _evaluate(
        self,
        session: AsyncSession,
        action: AgentAction,
        run: WorkflowRun,
        final_state: dict[str, Any] | None,
        agent: BaseAgent | None = None,
    ) -> Any:
        # W1-04 起 evaluate 不再调用模型；agent 参数仅为兼容旧调用保留
        del agent  # W1-04 起 evaluate 纯确定性，不再接收模型依赖
        return await AgentOutcomeService().evaluate(
            session, action=action, run=run,
            final_state=final_state,
        )

    async def test_deterministic_evidence_is_preferred_over_llm_judgment(
        self, test_engine: Any
    ) -> None:
        """Run failed（确定性 blocked）时，即使有语义约束也不调用 LLM。"""
        factory = async_sessionmaker(test_engine, expire_on_commit=False)
        async with factory() as session:
            action, run = await _seed(
                session, intent="revise_script", run_status="failed",
                constraints=["保持轻松基调"],
            )
            agent = BaseAgent(name="planner", llm=ExplodeOnCallLLM(seed=42))

            outcome = await self._evaluate(
                session, action, run,
                final_state={"error_code": "LLM_TIMEOUT", "error_detail": "模拟超时"},
                agent=agent,
            )

            assert outcome.goal_status == "blocked"
            assert any("Run 未完成" in c for c in outcome.remaining_constraints)
            assert outcome.recommended_next_action is None

    async def test_needs_review_is_partially_achieved_without_llm(
        self, test_engine: Any
    ) -> None:
        """needs_review → partially_achieved（无语义约束时不调模型）。"""
        factory = async_sessionmaker(test_engine, expire_on_commit=False)
        async with factory() as session:
            action, run = await _seed(session, run_status="needs_review")
            agent = BaseAgent(name="planner", llm=ExplodeOnCallLLM(seed=42))

            outcome = await self._evaluate(
                session, action, run,
                final_state={"needs_manual_review_reason": "连续性检查失败"},
                agent=agent,
            )
            assert outcome.goal_status == "partially_achieved"
            assert "连续性检查失败" in outcome.remaining_constraints[0]

    async def test_outline_dependent_scripts_make_outcome_partial(
        self, test_engine: Any
    ) -> None:
        """大纲修订完成但有剧本仍引用旧大纲 → 部分达成 + 建议修订剧本。"""
        factory = async_sessionmaker(test_engine, expire_on_commit=False)
        async with factory() as session:
            action, run = await _seed(session, run_status="completed")
            state = {
                "outline_impact": {
                    "changed_episodes": [3],
                    "dependent_script_ids": ["00000000-0000-0000-0000-000000000003"],
                    "follow_ups": ["第 3 集剧本依赖旧大纲且该集大纲已变化，建议发起剧本修订"],
                },
                "outline_set_artifact_id": "00000000-0000-0000-0000-000000000001",
            }

            outcome = await self._evaluate(session, action, run, state, agent=None)

            assert outcome.goal_status == "partially_achieved"
            assert outcome.recommended_next_action is not None
            assert outcome.recommended_next_action.intent == "revise_script"
            assert outcome.recommended_next_action.target.episode_number == 3
            assert "00000000-0000-0000-0000-000000000003" in [
                str(a) for a in outcome.evidence_artifact_ids
            ]

    async def test_plain_completion_is_achieved_without_llm(
        self, test_engine: Any
    ) -> None:
        """evaluate 完成且无语义约束 → achieved（不调模型）。"""
        factory = async_sessionmaker(test_engine, expire_on_commit=False)
        async with factory() as session:
            action, run = await _seed(session, intent="evaluate", run_status="completed")
            agent = BaseAgent(name="planner", llm=ExplodeOnCallLLM(seed=42))

            outcome = await self._evaluate(
                session, action, run,
                final_state={"evaluation_artifact_ids": {"1": "00000000-0000-0000-0000-000000000009"}},
                agent=agent,
            )
            assert outcome.goal_status == "achieved"
            assert outcome.remaining_constraints == []

    async def test_semantic_constraints_unverified_without_model_call(
        self, test_engine: Any
    ) -> None:
        """W1-04：语义约束不再经 evaluator 判断——无正文检查即 unverified。

        执行完成/评分上涨不自动视为满足；约束进 constraint_checks 待作者
        判断，achieved 降级 partially_achieved；unverified 不混入
        remaining_constraints（未完成是已知失败，待判断是未知）。
        """
        factory = async_sessionmaker(test_engine, expire_on_commit=False)
        async with factory() as session:
            action, run = await _seed(
                session, intent="revise_script", run_status="completed",
                constraints=["第 3 集增加正面冲突", "保持整体轻松基调"],
            )
            outcome = await self._evaluate(
                session, action, run,
                final_state={"script_artifact_ids": {"3": "00000000-0000-0000-0000-000000000010"}},
            )

            assert outcome.goal_status == "partially_achieved"
            assert outcome.verification_status == "unverified"
            assert outcome.remaining_constraints == []
            statuses = {c.constraint: c.status for c in outcome.constraint_checks}
            assert statuses == {
                "第 3 集增加正面冲突": "unverified",
                "保持整体轻松基调": "unverified",
            }
            # 待判断项锚定本轮新稿证据（供前端"查看本轮稿件"入口）
            script_check = outcome.constraint_checks[0]
            assert str(script_check.evidence_refs[0]) == "00000000-0000-0000-0000-000000000010"
            # 单纯 unverified 不触发后续修订计划（缺证据不自动改稿）
            assert outcome.recommended_next_action is None
            # 证据锚点带角色
            roles = {r.role for r in outcome.evidence_refs}
            assert roles == {"script"}

    async def test_failed_run_constraints_reported_once_as_blocked(
        self, test_engine: Any
    ) -> None:
        """blocked 任务：要求随任务失败陈述，不单列 unverified 核验项。"""
        factory = async_sessionmaker(test_engine, expire_on_commit=False)
        async with factory() as session:
            action, run = await _seed(
                session, intent="revise_script", run_status="failed",
                constraints=["保持悬念"],
            )
            run.error_code = "LLM_TIMEOUT"
            run.error_detail = "模型调用超时"
            await session.commit()

            outcome = await self._evaluate(session, action, run, final_state={})
            assert outcome.goal_status == "blocked"
            assert outcome.constraint_checks == []
            assert len(outcome.remaining_constraints) == 1
            assert "Run 未完成" in outcome.remaining_constraints[0]

