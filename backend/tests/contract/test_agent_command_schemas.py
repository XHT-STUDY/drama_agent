"""J-01 Agent 命令领域 Schema 契约测试。"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.domain.agent_command import (
    ActionStep,
    ActionTarget,
    AgentActionPlan,
    AgentOutcome,
    ExplainCommand,
    RecommendedNextAction,
    compute_request_hash,
)


@pytest.mark.contract
class TestAgentCommandSchemas:
    """Agent 命令、计划和结果必须保持严格结构化。"""

    def test_action_plan_accepts_discriminated_command(self) -> None:
        """计划中的 command 必须按 intent 判别并与计划意图一致。"""
        target = ActionTarget(target_type="project")
        plan = AgentActionPlan(
            goal="解释当前项目状态",
            intent="explain",
            command=ExplainCommand(target=target),
            target=target,
            constraints=[],
            steps=[
                ActionStep(
                    step_id="read-context",
                    title="读取项目上下文",
                    description="汇总当前有效资产并回答问题",
                )
            ],
            expected_impact=["只读，不创建 Run 或 Artifact"],
        )

        dumped = plan.model_dump(mode="json")
        assert dumped["command"]["intent"] == "explain"
        assert dumped["target"]["target_type"] == "project"

    def test_action_plan_rejects_intent_mismatch(self) -> None:
        """顶层 intent 与 command.intent 不一致时拒绝持久化。"""
        target = ActionTarget(target_type="project")
        with pytest.raises(ValidationError, match="intent"):
            AgentActionPlan.model_validate(
                {
                    "goal": "错误计划",
                    "intent": "evaluate",
                    "command": {
                        "intent": "explain",
                        "target": target.model_dump(mode="json"),
                    },
                    "target": target.model_dump(mode="json"),
                    "constraints": [],
                    "steps": [
                        {
                            "step_id": "read",
                            "title": "读取",
                            "description": "读取上下文",
                        }
                    ],
                    "expected_impact": [],
                }
            )

    def test_schema_forbids_unknown_fields(self) -> None:
        """公共 Agent Schema 禁止静默吞掉未知字段。"""
        with pytest.raises(ValidationError):
            ActionTarget.model_validate({"target_type": "project", "unexpected": "not allowed"})

    def test_outcome_replan_depth_is_bounded(self) -> None:
        """结果建议只能描述后续动作，递归深度由 0/1 约束。"""
        outcome = AgentOutcome(
            goal_status="partially_achieved",
            evidence_artifact_ids=[uuid.uuid4()],
            score_delta=2.5,
            remaining_constraints=["第三集结尾仍缺少反转"],
            recommended_next_action=RecommendedNextAction(
                intent="revise_script",
                target=ActionTarget(target_type="script", episode_number=3),
                constraints=["加强结尾反转"],
            ),
            replan_depth=1,
        )
        assert outcome.replan_depth == 1

        with pytest.raises(ValidationError):
            AgentOutcome.model_validate(
                {
                    **outcome.model_dump(mode="json"),
                    "replan_depth": 2,
                }
            )


@pytest.mark.unit
class TestRequestHash:
    """请求哈希必须规范化，供数据库幂等判断使用。"""

    def test_mapping_key_order_does_not_change_hash(self) -> None:
        first = {"message": "解释项目", "context": {"episode": 3, "kind": "script"}}
        second = {"context": {"kind": "script", "episode": 3}, "message": "解释项目"}
        assert compute_request_hash(first) == compute_request_hash(second)

    def test_semantic_change_changes_hash(self) -> None:
        first = {"message": "解释项目", "context": {"episode": 3}}
        second = {"message": "解释项目", "context": {"episode": 4}}
        assert compute_request_hash(first) != compute_request_hash(second)
