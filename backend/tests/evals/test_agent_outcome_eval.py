"""AgentOutcome 评测 harness（J-12）。

两种模式：
- CI（默认）：确定性规则子集——failed→blocked、needs_review→partial、
  revise_outline 依赖剧本→partial+revise_script 建议、深度/非法建议约束，
  直接对 AgentOutcomeService 的纯规则函数断言；
- 真实模型（pytest -m eval_real，EVAL_LLM_ENABLED=1）：语义约束用例调用
  AgentOutcomeEvaluatorSkill，按与 Service 相同的合并语义
  （achieved + 任一约束未满足 → partially_achieved）计算 goal_status 一致率，
  结果写入 tests/evals/results/。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

from app.agents.base import BaseAgent
from app.application.agent_outcome_service import (
    AgentEvidence,
    AgentOutcomeService,
)

EVAL_DIR = Path(__file__).parent
DATASET = EVAL_DIR / "agent_outcomes.json"
RESULTS_DIR = EVAL_DIR / "results"


def load_cases() -> list[dict[str, Any]]:
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    return cast(list[dict[str, Any]], data["cases"])


def evidence_of(case: dict[str, Any]) -> AgentEvidence:
    state = case.get("final_state") or {}
    impact = state.get("outline_impact") or {}
    return AgentEvidence(
        run_status=case["run_status"],
        intent=case["intent"],
        goal="评测目标",
        user_constraints=list(case.get("user_constraints") or []),
        remaining_constraints=[],
        follow_ups=[str(f) for f in impact.get("follow_ups", [])],
        changed_episodes=[int(ep) for ep in impact.get("changed_episodes", [])],
        dependent_script_ids=[str(s) for s in impact.get("dependent_script_ids", [])],
        needs_manual_review_reason=state.get("needs_manual_review_reason"),
        error_code=state.get("error_code"),
        error_detail=state.get("error_detail"),
    )


# ========================================================================
# 数据集契约（CI）
# ========================================================================


@pytest.mark.contract
class TestOutcomeDatasetContract:
    def test_dataset_has_at_least_30_cases(self) -> None:
        assert len(load_cases()) >= 30

    def test_covers_all_goal_statuses_and_depths(self) -> None:
        cases = load_cases()
        statuses = {c["expected"]["goal_status"] for c in cases}
        assert statuses == {"achieved", "partially_achieved", "blocked"}
        assert {c["replan_depth"] for c in cases} == {0, 1}
        assert any(c["semantic_constraints"] for c in cases)

    def test_covers_partial_with_next_intent_and_no_next(self) -> None:
        cases = load_cases()
        assert any("next_intent" in c["expected"] for c in cases)
        assert any(c["expected"].get("no_next_intent") for c in cases)
        assert any(c["expected"].get("no_child_at_depth") == 1 for c in cases)


# ========================================================================
# 确定性规则评测（CI）
# ============================================================================


@pytest.mark.unit
class TestDeterministicOutcomeEval:
    def test_all_deterministic_cases_match_rules(self) -> None:
        cases = [c for c in load_cases() if c["deterministic"]]
        assert len(cases) >= 20
        failures: list[str] = []
        for case in cases:
            evidence = evidence_of(case)
            goal_status, remaining = AgentOutcomeService._deterministic_status(evidence)
            expected = case["expected"]

            if goal_status != expected["goal_status"]:
                failures.append(
                    f"{case['id']}: {case['intent']}/{case['run_status']} "
                    f"期望 {expected['goal_status']} 实际 {goal_status}"
                )
                continue

            fragment = expected.get("remaining_contains")
            if fragment and not any(fragment in r for r in remaining):
                failures.append(f"{case['id']}: 剩余约束缺少 '{fragment}'：{remaining}")

            next_intent = expected.get("next_intent")
            no_next = expected.get("no_next_intent")
            if next_intent or no_next:
                recommendation = AgentOutcomeService._recommended_next_action(evidence)
                if next_intent:
                    if recommendation is None or recommendation.intent != next_intent:
                        actual = recommendation.intent if recommendation else None
                        failures.append(f"{case['id']}: 建议意图期望 {next_intent} 实际 {actual}")
                    elif expected.get("next_episode") and (
                        recommendation.target.episode_number != expected["next_episode"]
                    ):
                        failures.append(f"{case['id']}: 建议集数不符")
                if no_next and recommendation is not None:
                    failures.append(
                        f"{case['id']}: 期望无建议但得到 {recommendation.intent}"
                    )

            # replan_depth 透传（深度上限由 lifecycle 层强制，J-09 集成测试覆盖）
            depth = case["replan_depth"]
            if "no_child_at_depth" in expected and expected["no_child_at_depth"] != depth:
                failures.append(f"{case['id']}: 深度字段不一致")

        assert not failures, "\n".join(failures)

    def test_illegal_next_intent_never_recommended(self) -> None:
        """白名单外意图（explain）不可能出现在确定性建议中。"""
        for case in load_cases():
            evidence = evidence_of(case)
            recommendation = AgentOutcomeService._recommended_next_action(evidence)
            if recommendation is not None:
                assert recommendation.intent in {
                    "create_script", "evaluate", "revise_script", "revise_outline",
                }, case["id"]


# ========================================================================
# 真实模型评测（pytest -m eval_real）
# ============================================================================

REAL_EVAL_ENABLED = os.environ.get("EVAL_LLM_ENABLED") == "1"


@pytest.mark.eval_real
@pytest.mark.skipif(not REAL_EVAL_ENABLED, reason="需要 EVAL_LLM_ENABLED=1 与真实模型 Key")
class TestRealModelOutcomeEval:
    def test_semantic_constraint_goal_status_agreement(self) -> None:
        """语义约束用例：模型判断 + Service 合并语义 → goal_status 一致率。"""
        from pathlib import Path

        from dotenv import dotenv_values

        from app.core.config import Settings
        from app.domain.agent_command import AgentOutcomeEvaluatorInput
        from app.llm.openai_compatible import OpenAICompatibleLLM
        from app.prompts.loader import PromptLoader
        from app.skills.agent_outcome_evaluator import AgentOutcomeEvaluatorSkill

        # pytest 全局设 APP_ENV=test → Settings 跳过 .env 源（防泄漏设计），
        # 真实评测必须以 init kwargs 显式注入真实配置
        repo_root = Path(__file__).resolve().parents[3]
        real = {
            key.lower(): value
            for key, value in dotenv_values(repo_root / ".env").items()
            if value and key.startswith(("LLM_", "RUN_"))
        }
        settings = Settings(app_env="local", **real)
        agent = BaseAgent(name="planner", llm=OpenAICompatibleLLM(settings))
        skill = AgentOutcomeEvaluatorSkill()
        loader = PromptLoader()

        cases = [c for c in load_cases() if c["semantic_constraints"]]
        matches = 0
        mismatches: list[dict[str, str]] = []
        for case in cases:
            deterministic, _ = AgentOutcomeService._deterministic_status(evidence_of(case))
            goal_status = deterministic
            if goal_status != "blocked":
                import asyncio

                output = asyncio.run(
                    skill.execute(
                        {
                            "input": AgentOutcomeEvaluatorInput(
                                goal="评测目标",
                                intent=case["intent"],
                                run_status=case["run_status"],
                                deterministic_goal_status=deterministic,
                                user_constraints=case.get("user_constraints") or [],
                                evidence_summary="（评测桩：证据摘要略）",
                            ),
                            "agent": agent,
                            "prompt_loader": loader,
                        }
                    )
                )
                for judgment in output.constraint_judgments:
                    if not judgment.satisfied and goal_status == "achieved":
                        goal_status = "partially_achieved"
            if goal_status == case["expected"]["goal_status"]:
                matches += 1
            else:
                mismatches.append(
                    {
                        "id": case["id"],
                        "expected": case["expected"]["goal_status"],
                        "actual": goal_status,
                    }
                )

        agreement = matches / len(cases) if cases else None
        report = {
            "provider": settings.llm_provider,
            "model": settings.llm_planner_model,
            "prompt_version": loader.get("agent_outcome_evaluator").version,
            "semantic_cases": len(cases),
            "goal_status_agreement": agreement,
            "mismatches": mismatches,
        }
        RESULTS_DIR.mkdir(exist_ok=True)
        (RESULTS_DIR / "agent_outcomes_results.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        assert agreement is not None and agreement >= 0.6, mismatches[:5]
