"""对话命令评测 harness（J-12）。

两种模式：
- CI（默认）：数据集契约 + 确定性 preflight 子集——歧义/越界/冲突请求
  在调用模型之前就应澄清，用"调用即失败"的 LLM 桩证明零模型调用；
- 真实模型（pytest -m eval_real，需 EVAL_LLM_ENABLED=1 + 真实 Key）：
  全量用例跑 AgentCommandPlannerSkill，产出 precision/recall/F1 与
  澄清召回率，结果写入 tests/evals/results/（不写模拟数字）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from app.agents.base import BaseAgent
from app.domain.agent_planner import AgentPlannerInput, AgentPlannerOutput
from app.llm.fake import FakeLLM
from app.llm.models import LLMCallResult
from app.prompts.loader import PromptLoader
from app.skills.agent_command_planner import AgentCommandPlannerSkill

EVAL_DIR = Path(__file__).parent
DATASET = EVAL_DIR / "agent_commands.json"
RESULTS_DIR = EVAL_DIR / "results"

ALL_INTENTS = ("create_script", "explain", "evaluate", "revise_script", "revise_outline")


def load_cases() -> list[dict[str, Any]]:
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    return cast(list[dict[str, Any]], data["cases"])


class ExplodeOnCallLLM(FakeLLM):
    """任何 generate_structured 调用直接失败——证明 preflight 零模型调用。"""

    async def generate_structured(
        self, schema: Any, messages: list[dict[str, str]], **kwargs: Any
    ) -> LLMCallResult:
        raise AssertionError("preflight 确定性用例不应调用 LLM")


def planner_input(case: dict[str, Any]) -> AgentPlannerInput:
    return AgentPlannerInput(
        user_request=case["user_request"],
        project_title="评测项目",
        target_episode_count=case.get("target_episode_count", 10),
        available_intents=case.get("available_intents") or list(ALL_INTENTS),
        active_context=case.get("active_context"),
        project_context="（评测桩：项目上下文略）",
        unresolved_turn_count=0,
    )


# ========================================================================
# 数据集契约（CI）
# ========================================================================


@pytest.mark.contract
class TestCommandDatasetContract:
    def test_dataset_has_at_least_50_cases(self) -> None:
        cases = load_cases()
        assert len(cases) >= 50

    def test_covers_all_five_intents(self) -> None:
        cases = load_cases()
        intents = {
            c["expected"]["intent"] for c in cases if c["expected"]["turn_type"] == "plan"
        }
        assert set(ALL_INTENTS) <= intents

    def test_covers_clarification_and_preflight_subset(self) -> None:
        cases = load_cases()
        clarification = [c for c in cases if c["expected"]["turn_type"] == "clarification"]
        preflight = [c for c in clarification if c["preflight_deterministic"]]
        assert len(preflight) >= 10  # 歧义指代/越界/冲突/白名单未开放

    def test_covers_active_context_and_conflict_inputs(self) -> None:
        cases = load_cases()
        assert any(c.get("active_context") for c in cases)
        assert any("既" in c["user_request"] or "同时" in c["user_request"] for c in cases)


# ========================================================================
# 确定性 preflight 评测（CI，零模型调用）
# ========================================================================


@pytest.mark.unit
class TestPreflightEval:
    def test_preflight_clarification_recall_is_100_percent(self) -> None:
        """全部 preflight 确定性用例都在调用模型前澄清（explode 桩证明）。"""
        skill = AgentCommandPlannerSkill()
        agent = BaseAgent(name="planner", llm=ExplodeOnCallLLM(seed=42))
        loader = PromptLoader()

        hits = 0
        total = 0
        failures: list[str] = []
        for case in load_cases():
            if not case["preflight_deterministic"]:
                continue
            total += 1
            output = cast(
                AgentPlannerOutput,
                asyncio_run(
                    skill.execute(
                        {
                            "input": planner_input(case),
                            "agent": agent,
                            "prompt_loader": loader,
                        }
                    )
                ),
            )
            if output.turn_type == "clarification":
                hits += 1
            else:
                failures.append(f"{case['id']}: {case['user_request'][:30]} → {output.turn_type}")

        assert total >= 10
        recall = hits / total
        assert recall == 1.0, f"preflight 澄清召回率 {recall:.2%}，失败: {failures}"


def asyncio_run(coro: Any) -> Any:
    """同步测试内执行协程（评测逐条串行，无需事件循环复用）。"""
    import asyncio

    return asyncio.run(coro)


# ========================================================================
# 真实模型评测（pytest -m eval_real；EVAL_LLM_ENABLED=1 才执行）
# ========================================================================

REAL_EVAL_ENABLED = __import__("os").environ.get("EVAL_LLM_ENABLED") == "1"


@pytest.mark.eval_real
@pytest.mark.skipif(not REAL_EVAL_ENABLED, reason="需要 EVAL_LLM_ENABLED=1 与真实模型 Key")
class TestRealModelCommandEval:
    def test_run_full_dataset_and_report_metrics(self) -> None:
        """全量数据集 → 各 intent P/R/F1 + 澄清召回，结果落盘 results/。"""
        from app.core.config import Settings
        from app.llm.openai_compatible import OpenAICompatibleLLM

        settings = Settings()
        agent = BaseAgent(name="planner", llm=OpenAICompatibleLLM(settings))
        skill = AgentCommandPlannerSkill()
        loader = PromptLoader()

        per_intent: dict[str, list[int]] = {}  # intent -> [tp, fp, fn]
        clarification_expected = clarification_correct = 0
        failures: list[dict[str, str]] = []

        for case in load_cases():
            try:
                output = cast(
                    AgentPlannerOutput,
                    asyncio_run(
                        skill.execute(
                            {"input": planner_input(case), "agent": agent, "prompt_loader": loader}
                        )
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - 失败分类计入报告
                failures.append({"id": case["id"], "error": str(exc)[:200]})
                continue

            expected = case["expected"]
            if expected["turn_type"] == "clarification":
                clarification_expected += 1
                if output.turn_type == "clarification":
                    clarification_correct += 1
                else:
                    bucket = per_intent.setdefault(output.intent or "unknown", [0, 0, 0])
                    bucket[1] += 1  # FP：应澄清却出了计划
                continue

            intent = expected["intent"]
            bucket = per_intent.setdefault(intent, [0, 0, 0])
            if output.turn_type == "plan" and output.intent == intent:
                bucket[0] += 1
            elif output.turn_type == "plan":
                bucket[2] += 1  # FN：期望 intent 未命中
                per_intent.setdefault(str(output.intent), [0, 0, 0])[1] += 1
            else:
                bucket[2] += 1  # 应出计划却澄清

        report: dict[str, Any] = {
            "provider": settings.llm_provider,
            "model": settings.llm_planner_model,
            "prompt_version": loader.get("agent_command_planner").version,
            "total": len(load_cases()),
            "failures": failures,
            "clarification_recall": (
                clarification_correct / clarification_expected if clarification_expected else None
            ),
            "intents": {},
        }
        for intent, (tp, fp, fn) in per_intent.items():
            precision = tp / (tp + fp) if tp + fp else None
            recall = tp / (tp + fn) if tp + fn else None
            f1 = (
                2 * precision * recall / (precision + recall)
                if precision and recall
                else None
            )
            report["intents"][intent] = {
                "tp": tp, "fp": fp, "fn": fn,
                "precision": precision, "recall": recall, "f1": f1,
            }

        RESULTS_DIR.mkdir(exist_ok=True)
        (RESULTS_DIR / "agent_commands_results.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 门禁：真实评测不允许全军覆没（模型不可用应直接报错而非全 0）
        assert not failures or len(failures) < len(load_cases()) * 0.2, failures[:3]
