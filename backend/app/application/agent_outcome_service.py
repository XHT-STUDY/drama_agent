"""AgentOutcomeService — 目标达成判断（J-09）。

评估顺序（确定性证据优先）:
1. Run 终态 + workflow final state → 确定性 goal_status / evidence /
   score_delta / remaining_constraints（评分、连续性、Artifact 引用、
   大纲影响、错误信息全部来自服务端事实）;
2. 仅当存在"用户语义约束"（现有校验器无法判断的自然语言要求）时，
   调用 AgentOutcomeEvaluatorSkill 判断这些约束——模型只能补充
   remaining_constraints 与后续意图建议，不得改变确定性结论
   （越权字段在合并时被直接丢弃）。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.core.errors import AppError
from app.db.models.agent_action import AgentAction
from app.db.models.workflow_run import WorkflowRun
from app.domain.agent_command import (
    ActionTarget,
    AgentGoalStatus,
    AgentOutcome,
    AgentOutcomeEvaluatorInput,
    OutcomeRecommendation,
    RecommendedNextAction,
)
from app.prompts.loader import PromptLoader
from app.skills.agent_outcome_evaluator import AgentOutcomeEvaluatorSkill

logger = logging.getLogger(__name__)

_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "needs_review", "cancelled"})


@dataclass
class AgentEvidence:
    """从 workflow final state 收集的确定性证据（轻量，仅 ID 与摘要）。"""

    run_status: str
    intent: str
    goal: str
    user_constraints: list[str] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    score_delta: float | None = None
    remaining_constraints: list[str] = field(default_factory=list)
    follow_ups: list[str] = field(default_factory=list)
    changed_episodes: list[int] = field(default_factory=list)
    dependent_script_ids: list[str] = field(default_factory=list)
    needs_manual_review_reason: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    summary_lines: list[str] = field(default_factory=list)


class AgentOutcomeService:
    """确定性证据优先的目标达成判断。"""

    def __init__(self) -> None:
        self._artifact_svc = ArtifactService()
        self._evaluator = AgentOutcomeEvaluatorSkill()

    async def evaluate(
        self,
        db: AsyncSession,
        *,
        action: AgentAction,
        run: WorkflowRun,
        final_state: dict[str, Any] | None = None,
        agent: BaseAgent | None = None,
        prompt_loader: PromptLoader | None = None,
    ) -> AgentOutcome:
        """生成 AgentOutcome（含可选的一次后续建议）。

        Args:
            final_state: workflow 终态快照；None 时从 run.state_summary 读取。
            agent / prompt_loader: 缺省时不调用模型（纯确定性评估）。
        """
        state: dict[str, Any] = final_state or run.state_summary or {}
        evidence = await self._collect_evidence(db, action=action, run=run, state=state)

        goal_status: AgentGoalStatus
        goal_status, remaining = self._deterministic_status(evidence)

        # 语义约束判断：确定性结论已 blocked / 无语义约束 → 不调用模型
        judgments: list[tuple[str, bool, str]] = []
        recommendation: OutcomeRecommendation | None = None
        semantic_constraints = [
            c for c in evidence.user_constraints if c not in remaining
        ]
        if (
            goal_status != "blocked"
            and semantic_constraints
            and agent is not None
            and prompt_loader is not None
        ):
            try:
                output = await self._evaluator.execute(
                    {
                        "input": AgentOutcomeEvaluatorInput(
                            goal=evidence.goal,
                            intent=evidence.intent,  # type: ignore[arg-type]
                            run_status=evidence.run_status,
                            deterministic_goal_status=goal_status,
                            user_constraints=semantic_constraints,
                            evidence_summary="\n".join(evidence.summary_lines)[:6000],
                        ),
                        "agent": agent,
                        "prompt_loader": prompt_loader,
                    }
                )
                judgments = [
                    (j.constraint, j.satisfied, j.reason)
                    for j in output.constraint_judgments
                ]
                recommendation = output.recommended_next_action
            except AppError as exc:
                # 评估失败不阻断终态回写：退化为纯确定性结论
                logger.warning("Outcome Evaluator 失败，退化为确定性结论: %s", exc)

        # 合并语义判断（模型不能改变确定性结论，只能补充剩余约束）
        for constraint, satisfied, _reason in judgments:
            if not satisfied and constraint not in remaining:
                remaining.append(constraint)
                if goal_status == "achieved":
                    goal_status = "partially_achieved"

        next_action = self._recommended_next_action(evidence, recommendation)

        return AgentOutcome(
            goal_status=goal_status,
            evidence_artifact_ids=_unique_uuids(evidence.artifact_ids),
            score_delta=evidence.score_delta,
            remaining_constraints=remaining,
            recommended_next_action=next_action,
            replan_depth=action.replan_depth,
        )

    # ------------------------------------------------------------------
    # 证据收集
    # ------------------------------------------------------------------

    async def _collect_evidence(
        self,
        db: AsyncSession,
        *,
        action: AgentAction,
        run: WorkflowRun,
        state: dict[str, Any],
    ) -> AgentEvidence:
        plan = action.plan or {}
        evidence = AgentEvidence(
            run_status=run.status,
            intent=action.intent,
            goal=str(plan.get("goal", ""))[:2000],
            user_constraints=[
                str(c) for c in (action.plan or {}).get("constraints", [])
            ],
        )
        if evidence.run_status not in _TERMINAL_RUN_STATUSES:
            evidence.run_status = "failed"
            evidence.error_detail = f"Run 处于非终态 {run.status}，按失败处理"

        # Artifact ID 证据
        for key in (
            "requirement_artifact_id", "story_bible_artifact_id",
            "outline_set_artifact_id", "revision_plan_artifact_id",
            "continuity_check_artifact_id", "source_script_artifact_id",
            "source_outline_artifact_id",
        ):
            value = state.get(key)
            if value:
                evidence.artifact_ids.append(str(value))
        for mapping_key in ("script_artifact_ids", "evaluation_artifact_ids"):
            evidence.artifact_ids.extend(
                str(v) for v in (state.get(mapping_key) or {}).values()
            )

        # 大纲影响证据
        impact = state.get("outline_impact") or {}
        evidence.changed_episodes = [int(ep) for ep in impact.get("changed_episodes", [])]
        evidence.dependent_script_ids = [
            str(sid) for sid in impact.get("dependent_script_ids", [])
        ]
        evidence.artifact_ids.extend(evidence.dependent_script_ids)
        evidence.follow_ups = [str(f) for f in impact.get("follow_ups", [])]

        # 人工复核 / 失败证据
        reason = state.get("needs_manual_review_reason")
        if reason:
            evidence.needs_manual_review_reason = str(reason)
        evidence.error_code = run.error_code or state.get("error_code")
        evidence.error_detail = run.error_detail or state.get("error_detail")

        # 评分变化（revise_script：原评估 vs 新评估）
        evidence.score_delta = await self._score_delta(db, state)

        evidence.summary_lines = self._summary_lines(evidence)
        return evidence

    async def _score_delta(
        self, db: AsyncSession, state: dict[str, Any]
    ) -> float | None:
        """从修订计划引用的评估与新评估计算分数变化（确定性）。"""
        plan_id = state.get("revision_plan_artifact_id")
        episode = state.get("revision_candidate_episode")
        new_eval_id = (state.get("evaluation_artifact_ids") or {}).get(str(episode))
        if not plan_id or not new_eval_id:
            return None
        try:
            from app.domain.evaluation import EvaluationReport
            from app.domain.revision import RevisionPlan

            plan = RevisionPlan.model_validate(
                (await self._artifact_svc.get_version(db, uuid.UUID(str(plan_id)))).content
            )
            old = EvaluationReport.model_validate(
                (await self._artifact_svc.get_version(db, plan.source_evaluation_artifact_id)).content
            )
            new = EvaluationReport.model_validate(
                (await self._artifact_svc.get_version(db, uuid.UUID(str(new_eval_id)))).content
            )
            return round(new.overall_score - old.overall_score, 2)
        except Exception:  # noqa: BLE001 - 证据缺失不阻断评估
            logger.info("评分变化证据不可用，score_delta 置空")
            return None

    @staticmethod
    def _summary_lines(evidence: AgentEvidence) -> list[str]:
        lines = [f"Run 终态: {evidence.run_status}", f"意图: {evidence.intent}"]
        if evidence.artifact_ids:
            lines.append(f"产出 Artifact: {len(evidence.artifact_ids)} 个")
        if evidence.score_delta is not None:
            lines.append(f"评分变化: {evidence.score_delta:+.1f}")
        if evidence.needs_manual_review_reason:
            lines.append(f"人工复核原因: {evidence.needs_manual_review_reason}")
        if evidence.changed_episodes:
            lines.append(f"大纲变更集: {evidence.changed_episodes}")
        if evidence.dependent_script_ids:
            lines.append(f"仍引用旧大纲的剧本: {len(evidence.dependent_script_ids)} 个")
        if evidence.error_code:
            lines.append(f"错误: {evidence.error_code} {evidence.error_detail or ''}"[:500])
        return lines

    # ------------------------------------------------------------------
    # 确定性 goal_status
    # ------------------------------------------------------------------

    @staticmethod
    def _deterministic_status(
        evidence: AgentEvidence,
    ) -> tuple[AgentGoalStatus, list[str]]:
        """规则优先的 goal_status 判定（不调用 LLM）。"""
        remaining = list(evidence.remaining_constraints)

        if evidence.run_status in ("failed", "cancelled"):
            reason = evidence.error_detail or evidence.error_code or evidence.run_status
            return "blocked", [f"Run 未完成（{evidence.run_status}）: {reason}"[:500]]

        if evidence.run_status == "needs_review":
            reason = evidence.needs_manual_review_reason or "需要人工复核"
            return "partially_achieved", [reason]

        # completed：按意图做确定性判定
        if evidence.intent == "revise_outline" and evidence.dependent_script_ids:
            # 大纲已更新，但仍有剧本引用旧大纲 → 部分达成
            remaining.extend(
                f for f in evidence.follow_ups if f not in remaining
            )
            return "partially_achieved", remaining

        return "achieved", remaining

    @staticmethod
    def _recommended_next_action(
        evidence: AgentEvidence,
        evaluator_recommendation: OutcomeRecommendation | None,
    ) -> RecommendedNextAction | None:
        """确定性建议优先（大纲影响 → 修订受影响剧本），其次 evaluator 建议。

        只输出意图与目标描述；执行目标（Artifact ID）由 lifecycle 用
        当前最新 Artifact 重新解析——建议本身不携带可执行句柄。
        """
        if evidence.dependent_script_ids and evidence.intent == "revise_outline":
            episode = evidence.changed_episodes[0] if evidence.changed_episodes else None
            return RecommendedNextAction(
                intent="revise_script",
                target=ActionTarget(target_type="script", episode_number=episode),
                constraints=[],
            )
        if evaluator_recommendation is not None:
            rec = evaluator_recommendation
            episode = rec.episode_number
            target_type = {
                "revise_script": "script",
                "revise_outline": "outline",
                "evaluate": "evaluation",
                "create_script": "project",
            }.get(rec.intent, "project")
            return RecommendedNextAction(
                intent=rec.intent,
                target=ActionTarget(
                    target_type=target_type,  # type: ignore[arg-type]
                    episode_number=episode,
                ),
                constraints=[],
            )
        return None


def _unique_uuids(values: list[str]) -> list[uuid.UUID]:
    seen: set[str] = set()
    result: list[uuid.UUID] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        try:
            result.append(uuid.UUID(value))
        except ValueError:
            continue
    return result
