"""P0 分段创作多幕次终态回写测试：门上暂停后，续跑的结局必须对用户可见。

回归背景（2026-09-07 真实事故）：staged 创作停大纲门 → 确认续跑 → 写剧本
失败——门上结果消息占用了 Action 唯一的结果位，失败被幂等守卫吞掉，
用户看到的最后一句仍是"等待确认后继续创作剧本"，输入「继续」被 Planner
反问澄清。修复后按幕次键（last_synced_phase）幂等：每个 Run 幕次各有一条
结果消息，失败如实呈现并给出重试入口。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.agent_action_lifecycle import AgentActionLifecycle
from app.db.models.agent_action import AgentAction
from app.db.models.agent_turn import AgentTurn
from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.db.models.project import Project
from app.db.models.workflow_run import WorkflowRun
from app.domain.agent_command import (
    ActionStep,
    ActionTarget,
    AgentActionPlan,
    CreateScriptCommand,
)


async def _seed_action_with_run(
    db_session: AsyncSession, *, status: str = "running"
) -> tuple[AgentAction, WorkflowRun]:
    """种下 running 状态的 Action 与关联 Run（绕过确认 API，直测 lifecycle）。"""
    project = Project(title="多幕次项目", target_episode_count=2)
    db_session.add(project)
    await db_session.flush()
    conversation = Conversation(project_id=project.id, title="多幕次会话")
    db_session.add(conversation)
    await db_session.flush()
    message = Message(
        conversation_id=conversation.id, role="user", content="seed",
        kind="text", message_metadata={}, sequence=1,
    )
    db_session.add(message)
    await db_session.flush()
    turn = AgentTurn(
        project_id=project.id, conversation_id=conversation.id,
        user_message_id=message.id, idempotency_key=f"seed-{uuid.uuid4().hex}",
        request_hash="0" * 64, status="action_proposed", turn_type="plan",
    )
    db_session.add(turn)
    await db_session.flush()
    plan = AgentActionPlan(
        goal="创建足球少年逆袭短剧", intent="create_script",
        command=CreateScriptCommand(user_input="足球少年逆袭", outline_count=2, script_count=2),
        target=ActionTarget(target_type="project"),
        steps=[ActionStep(step_id="run", title="创建剧本", description="执行创作工作流")],
    )
    action = AgentAction(
        project_id=project.id, conversation_id=conversation.id,
        agent_turn_id=turn.id, replan_depth=0, intent="create_script",
        status=status, requires_confirmation=True,
        plan=plan.model_dump(mode="json"), source_artifact_ids=[],
    )
    run = WorkflowRun(
        project_id=project.id, action="create_script", status="running",
        attempt_count=1,  # 已被 Dispatcher 领取过一次
        config_snapshot={"options": {"user_input": "足球少年逆袭"}},
    )
    db_session.add_all([action, run])
    await db_session.flush()
    action.run_id = run.id
    await db_session.commit()
    return action, run


def _gate_summary() -> dict[str, Any]:
    """大纲门暂停时的 state_summary（Artifact ID 用假 UUID，评估只收集 ID）。"""
    return {
        "stage_gate": "outline",
        "story_bible_artifact_id": str(uuid.uuid4()),
        "outline_set_artifact_id": str(uuid.uuid4()),
        "completed_nodes": ["normalize", "story_bible", "outline"],
    }


async def _result_messages(
    db_session: AsyncSession, action: AgentAction
) -> list[Message]:
    result = await db_session.execute(
        select(Message)
        .where(
            Message.conversation_id == action.conversation_id,
            Message.kind == "action_result",
            Message.message_metadata["agent_action_id"].astext == str(action.id),
        )
        .order_by(Message.created_at)
    )
    return list(result.scalars())


@pytest.mark.integration
@pytest.mark.asyncio
async def test_failure_after_gate_pause_is_reported(db_session: AsyncSession) -> None:
    """门上暂停后续跑失败：Action 迁移 failed + 追加失败结果消息（不被吞）。"""
    action, run = await _seed_action_with_run(db_session)
    lifecycle = AgentActionLifecycle()

    # 第一幕：大纲门暂停 → 门上结果消息
    run.status = "needs_review"
    run.state_summary = _gate_summary()
    await db_session.commit()
    finalized = await lifecycle.finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert finalized is not None and finalized.status == "needs_review"
    assert finalized.last_synced_phase == "v2:needs_review:outline:g0:a1"
    messages = await _result_messages(db_session, action)
    assert len(messages) == 1
    assert "等待确认后继续创作剧本" in messages[0].content
    assert messages[0].message_metadata["run_status"] == "needs_review"

    # 第二幕：确认续跑后写剧本失败（state_summary 已被 continue 剥离门字段）
    run.status = "failed"
    run.error_code = "LLM_OUTPUT_TRUNCATED"
    run.error_detail = "Episode Writer LLM 调用失败: output_truncated"
    run.state_summary = {"completed_nodes": ["normalize", "story_bible", "outline"]}
    await db_session.commit()
    finalized = await lifecycle.finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert finalized is not None and finalized.status == "failed"
    assert finalized.last_synced_phase == "v2:failed:g0:a1"
    assert finalized.result is not None
    assert finalized.result["goal_status"] == "blocked"

    messages = await _result_messages(db_session, action)
    assert len(messages) == 2, "门上消息保留，失败消息必须追加"
    failure_msg = messages[-1]
    assert "失败" in failure_msg.content
    assert "重试" in failure_msg.content, "失败消息必须给出重试入口"
    assert failure_msg.message_metadata["run_status"] == "failed"

    # 同幕次重入（重复 reconciliation）不追加第三条
    again = await lifecycle.finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert again is not None and again.status == "failed"
    assert len(await _result_messages(db_session, action)) == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_completion_after_gate_pause_finalizes_action(
    db_session: AsyncSession,
) -> None:
    """门上暂停后续跑完成：Action 迁移 completed + 追加最终结果消息。"""
    action, run = await _seed_action_with_run(db_session)
    lifecycle = AgentActionLifecycle()

    run.status = "needs_review"
    run.state_summary = _gate_summary()
    await db_session.commit()
    await lifecycle.finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )

    run.status = "completed"
    run.state_summary = {
        "story_bible_artifact_id": str(uuid.uuid4()),
        "outline_set_artifact_id": str(uuid.uuid4()),
        "script_artifact_ids": {str(ep): str(uuid.uuid4()) for ep in (1, 2)},
        "evaluation_artifact_ids": {str(ep): str(uuid.uuid4()) for ep in (1, 2)},
        "completed_nodes": ["normalize", "story_bible", "outline", "write_episodes"],
    }
    await db_session.commit()
    finalized = await lifecycle.finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert finalized is not None and finalized.status == "completed"
    assert finalized.last_synced_phase == "v2:completed:g0:a1"

    messages = await _result_messages(db_session, action)
    assert len(messages) == 2
    assert messages[-1].message_metadata["run_status"] == "completed"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_legacy_row_without_phase_stays_frozen(db_session: AsyncSession) -> None:
    """存量行（last_synced_phase=NULL + 已有 result）保持冻结语义，不重复回写。"""
    action, run = await _seed_action_with_run(db_session, status="needs_review")
    action.result = {"goal_status": "partially_achieved", "remaining_constraints": []}
    action.last_synced_phase = None
    await db_session.commit()

    run.status = "needs_review"
    run.state_summary = _gate_summary()
    await db_session.commit()
    finalized = await AgentActionLifecycle().finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert finalized is not None
    assert finalized.last_synced_phase is None, "存量行不回填幕次键"
    assert len(await _result_messages(db_session, action)) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_needs_review_after_continue_reports_new_phase(
    db_session: AsyncSession,
) -> None:
    """续跑后再次 needs_review（修订轮次用尽/下一批门）必须再次回写。

    E2E 红的直接根因：needs_review→needs_review 不是状态机合法迁移，
    transition 抛错后被"并发胜者已写入"分支静默吞掉——用户看到的
    最后一句永远是门上那句"等待确认"（L-4 批次门两连停同样命中）。
    """
    action, run = await _seed_action_with_run(db_session)
    lifecycle = AgentActionLifecycle()

    # 第一幕（attempt=1）：大纲门暂停
    run.status = "needs_review"
    run.attempt_count = 1
    run.state_summary = _gate_summary()
    await db_session.commit()
    await lifecycle.finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert len(await _result_messages(db_session, action)) == 1

    # 第二幕（generation=1, attempt=2）：确认续跑 → 全部写完+评估 →
    # 修订轮次用尽，Run 仍是 needs_review 但门字段已被 continue 剥离
    run.stage_generation = 1
    run.attempt_count = 2
    run.state_summary = {
        "completed_nodes": ["normalize", "story_bible", "outline", "write_episodes"],
        "needs_revision_decision": True,
        "script_artifact_ids": {str(ep): str(uuid.uuid4()) for ep in (1, 2)},
        "evaluation_artifact_ids": {str(ep): str(uuid.uuid4()) for ep in (1, 2)},
    }
    await db_session.commit()
    finalized = await lifecycle.finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert finalized is not None and finalized.status == "needs_review"
    assert finalized.last_synced_phase == "v2:needs_review:paused:g1:a2"
    assert finalized.result is not None

    messages = await _result_messages(db_session, action)
    assert len(messages) == 2, "续跑后的结局必须追加，不能停在门上那句"
    assert "等待确认" not in messages[-1].content
    assert messages[-1].message_metadata["phase"] == "v2:needs_review:paused:g1:a2"

    # 同幕次重入（重复 reconciliation）不追加第三条
    again = await lifecycle.finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert again is not None and again.status == "needs_review"
    assert len(await _result_messages(db_session, action)) == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retry_failure_after_failure_reports_again(
    db_session: AsyncSession,
) -> None:
    """失败 → 用户「重试」→ 再次失败：第二次结局必须再次汇报。

    真实环境实测：失败消息承诺"输入「重试」…完成后会在这里汇报"，
    重试后再次失败却被同状态幂等守卫吞掉——会话沉默。幕次键必须带
    attempt 维度，重试是新的幕次。
    """
    action, run = await _seed_action_with_run(db_session)
    lifecycle = AgentActionLifecycle()

    # 第一幕（attempt=1）：失败
    run.status = "failed"
    run.attempt_count = 1
    run.error_code = "LLM_INVALID_OUTPUT"
    run.error_detail = "Episode Writer LLM 调用失败: invalid_output"
    run.state_summary = {"completed_nodes": ["normalize", "story_bible", "outline"]}
    await db_session.commit()
    finalized = await lifecycle.finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert finalized is not None and finalized.status == "failed"
    assert finalized.last_synced_phase == "v2:failed:g0:a1"
    assert len(await _result_messages(db_session, action)) == 1

    # 第二幕（attempt=2）：断点重试后再次失败
    run.attempt_count = 2
    await db_session.commit()
    finalized = await lifecycle.finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert finalized is not None and finalized.status == "failed"
    assert finalized.last_synced_phase == "v2:failed:g0:a2"
    messages = await _result_messages(db_session, action)
    assert len(messages) == 2, "重试后的再次失败必须再次汇报（承诺过）"
    assert "失败" in messages[-1].content
    assert messages[-1].message_metadata["phase"] == "v2:failed:g0:a2"

    # 同幕次重入不追加第三条
    await lifecycle.finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert len(await _result_messages(db_session, action)) == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_legacy_phase_format_stays_frozen(db_session: AsyncSession) -> None:
    """旧格式幕次键（无 v2 前缀，0010 迁移期写入的行）冻结，不重复回写。"""
    action, run = await _seed_action_with_run(db_session, status="needs_review")
    action.result = {"goal_status": "partially_achieved", "remaining_constraints": []}
    action.last_synced_phase = "needs_review:outline"  # 旧格式
    await db_session.commit()

    run.status = "needs_review"
    run.attempt_count = 2
    run.state_summary = {"completed_nodes": ["write_episodes"]}
    await db_session.commit()
    finalized = await AgentActionLifecycle().finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert finalized is not None
    assert finalized.last_synced_phase == "needs_review:outline", "旧格式行保持冻结"
    assert len(await _result_messages(db_session, action)) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_consecutive_scripts_gates_each_reported(
    db_session: AsyncSession,
) -> None:
    """W1-01：同一 Action 连续两个 scripts 门各有一条结果消息。

    门续跑不再累加 attempt（置零后领取回到 1）——若幕次键只看 attempt，
    第二个 scripts 门（g1:a1）会与第一个（g0:a1）撞键被吞。世代维度
    是区分连续批门的关键。
    """
    action, run = await _seed_action_with_run(db_session)
    lifecycle = AgentActionLifecycle()

    def _scripts_summary(written: int) -> dict[str, Any]:
        return {
            "stage_gate": "scripts",
            "target_episode_count": 10,
            "script_artifact_ids": {
                str(ep): str(uuid.uuid4()) for ep in range(1, written + 1)
            },
            "completed_nodes": ["normalize", "story_bible", "outline"],
        }

    # 第一批（g0）：写 3 集停在 scripts 门
    run.status = "needs_review"
    run.stage_generation = 0
    run.attempt_count = 1
    run.state_summary = _scripts_summary(3)
    await db_session.commit()
    await lifecycle.finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert action.last_synced_phase == "v2:needs_review:scripts:g0:a1"

    # 第二批（g1，attempt 置零后被领取回到 1）：再写 5 集停在 scripts 门
    run.stage_generation = 1
    run.attempt_count = 1
    run.state_summary = _scripts_summary(8)
    await db_session.commit()
    finalized = await lifecycle.finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert finalized is not None
    assert finalized.last_synced_phase == "v2:needs_review:scripts:g1:a1"

    messages = await _result_messages(db_session, action)
    assert len(messages) == 2, "连续两个 scripts 门各一条结果，第二个不能被吞"
    assert "3/10" in messages[0].content
    assert "8/10" in messages[-1].content

    # 两个幕次各自的重入（重复 reconciliation）都不追加
    for _ in range(2):
        await lifecycle.finalize(
            db_session, action_id=action.id, run=run, final_state=run.state_summary
        )
    assert len(await _result_messages(db_session, action)) == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_old_v2_phase_format_normalized_not_duplicated(
    db_session: AsyncSession,
) -> None:
    """W1-01 之前写入的旧 v2 幕次键（无 g 段）按世代 0 归一比较。

    同幕次（g0）不重复回写；新世代（g1）正常放行——旧格式行升级后
    既不产生重复门消息，也不会错过续跑后的新结局。
    """
    action, run = await _seed_action_with_run(db_session, status="needs_review")
    action.result = {"goal_status": "partially_achieved", "remaining_constraints": []}
    action.last_synced_phase = "v2:needs_review:scripts:a1"  # bda1302 格式
    await db_session.commit()

    # 同幕次（g0, a1, scripts 门）：归一后相等 → 冻结，不追加
    run.status = "needs_review"
    run.stage_generation = 0
    run.attempt_count = 1
    run.state_summary = {
        "stage_gate": "scripts",
        "script_artifact_ids": {"1": str(uuid.uuid4())},
        "completed_nodes": ["normalize"],
    }
    await db_session.commit()
    finalized = await AgentActionLifecycle().finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert finalized is not None
    assert finalized.last_synced_phase == "v2:needs_review:scripts:a1"
    assert len(await _result_messages(db_session, action)) == 0

    # 新世代（g1）：放行回写
    run.stage_generation = 1
    await db_session.commit()
    finalized = await AgentActionLifecycle().finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert finalized is not None
    assert finalized.last_synced_phase == "v2:needs_review:scripts:g1:a1"
    assert len(await _result_messages(db_session, action)) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_owner_swap_freezes_old_action_result(
    db_session: AsyncSession,
) -> None:
    """W1-01 所有者守卫：continue Action 接管归属后，旧创建 Action 的
    已存结果不被当前 Run 终态覆盖（GET 旧 Action 的 reconcile 短路）。"""
    action, run = await _seed_action_with_run(db_session, status="needs_review")
    lifecycle = AgentActionLifecycle()

    # 第一幕：创建 Action 停门 + 回写结果
    run.status = "needs_review"
    run.state_summary = _gate_summary()
    await db_session.commit()
    await lifecycle.finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert action.status == "needs_review"
    assert action.result is not None
    frozen_result = dict(action.result)
    frozen_phase = action.last_synced_phase

    # continue Action 接管归属；Run 走完批次后 completed
    new_owner_id = uuid.uuid4()
    run.config_snapshot = {
        **(run.config_snapshot or {}),
        "agent_action_id": str(new_owner_id),
    }
    run.status = "completed"
    run.stage_generation = 1
    run.attempt_count = 1
    run.state_summary = {
        "script_artifact_ids": {"1": str(uuid.uuid4())},
        "evaluation_artifact_ids": {"1": str(uuid.uuid4())},
        "completed_nodes": ["finalize"],
    }
    await db_session.commit()

    finalized = await lifecycle.finalize(
        db_session, action_id=action.id, run=run, final_state=run.state_summary
    )
    assert finalized is not None
    assert finalized.status == "needs_review", "旧 Action 状态不被覆盖"
    assert finalized.result == frozen_result, "旧 Action 结果不被覆盖"
    assert finalized.last_synced_phase == frozen_phase
    # 旧 Action 也没有新消息
    assert len(await _result_messages(db_session, action)) == 1
