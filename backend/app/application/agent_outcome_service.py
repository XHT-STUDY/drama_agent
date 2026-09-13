"""AgentOutcomeService — 目标达成判断（J-09，W1-04 诚实性版）。

评估顺序（确定性证据优先，且当前是唯一来源）:
1. Run 终态 + workflow final state → 确定性 goal_status / evidence /
   score_delta / remaining_constraints（评分、连续性、Artifact 引用、
   大纲影响、错误信息全部来自服务端事实）;
2. 逐条创作要求生成 constraint_checks：阶段一没有"读正文比对要求"的
   可复核检查（W1-03 才引入原文证据链），自然语言要求一律如实标
   unverified 交作者判断——执行完成、评分上涨、Schema 合法都不自动
   视为满足（W1-04 停用无正文的语义 Outcome Evaluator 调用；Skill
   文件保留供历史契约与后续阶段）。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.artifact_service import ArtifactService
from app.db.models.agent_action import AgentAction
from app.db.models.workflow_run import WorkflowRun
from app.domain.agent_command import (
    ActionTarget,
    AgentGoalStatus,
    AgentOutcome,
    ConstraintCheck,
    OutcomeEvidenceRef,
    RecommendedNextAction,
)

logger = logging.getLogger(__name__)

_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "needs_review", "cancelled"})

# state 键 → 证据角色（W1-04：证据锚点指向本轮实际产出/依据）
_EVIDENCE_REF_KEYS: tuple[tuple[str, str], ...] = (
    ("source_script_artifact_id", "source_script"),
    ("source_outline_artifact_id", "source_outline"),
    ("story_bible_artifact_id", "story_bible"),
    ("outline_set_artifact_id", "outline"),
    ("revision_plan_artifact_id", "revision_plan"),
    ("continuity_check_artifact_id", "continuity"),
)


@dataclass
class AgentEvidence:
    """从 workflow final state 收集的确定性证据（轻量，仅 ID 与摘要）。"""

    run_status: str
    intent: str
    goal: str
    user_constraints: list[str] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    evidence_refs: list[OutcomeEvidenceRef] = field(default_factory=list)
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
    """确定性证据优先的目标达成判断（W1-04：零模型调用）。"""

    def __init__(self) -> None:
        self._artifact_svc = ArtifactService()

    async def evaluate(
        self,
        db: AsyncSession,
        *,
        action: AgentAction,
        run: WorkflowRun,
        final_state: dict[str, Any] | None = None,
    ) -> AgentOutcome:
        """生成 AgentOutcome（含可选的一次后续建议）。

        纯确定性评估（W1-04 起零模型调用——无正文证据的语义判断只会
        产生不可核实的结论）。

        Args:
            final_state: workflow 终态快照；None 时从 run.state_summary 读取。
        """
        state: dict[str, Any] = final_state or run.state_summary or {}
        evidence = await self._collect_evidence(db, action=action, run=run, state=state)

        goal_status: AgentGoalStatus
        goal_status, remaining = self._deterministic_status(evidence)

        # 确认门（stage_gate=outline/scripts）是设计内的阶段暂停而非异常：
        # 不产生核验项与后续动作建议（用户只需在门上确认续跑），也不把
        # "等待确认"列为未完成约束。
        is_stage_gate = (
            evidence.run_status == "needs_review"
            and state.get("stage_gate") in ("outline", "scripts")
        )

        # 逐条创作要求的核验结果（W1-04）：阶段一没有可复核的正文检查，
        # 已知确定性失败（blocked）以外的语义要求一律 unverified 待作者
        # 判断；blocked 时要求随任务一起失败，不再单列核验项；
        # 确认门是设计内暂停，不产生核验项也不留"需要人工复核"尾巴
        checks: list[ConstraintCheck] = []
        if is_stage_gate:
            remaining = []
        elif goal_status != "blocked":
            checks = [
                ConstraintCheck(
                    constraint=c,
                    status="unverified",
                    reason="缺少可核验的正文证据检查，需要你阅读本轮稿件后判断",
                    evidence_refs=[
                        r.artifact_id for r in evidence.evidence_refs if r.role == "script"
                    ][:5],
                )
                for c in evidence.user_constraints
                if c and c not in remaining
            ]
        has_unverified = any(c.status == "unverified" for c in checks)
        verification_status: Any = "unverified" if has_unverified else "verified"

        # 语义未验证不得自称完全达成（兼容旧三态：映射 partially_achieved）。
        # unverified 不进 remaining_constraints——"未完成"是已知失败，
        # "待判断"是未知，混在一起会把未验证伪装成已失败。
        if goal_status == "achieved" and has_unverified:
            goal_status = "partially_achieved"

        next_action = (
            None
            if is_stage_gate
            else self._recommended_next_action(evidence)
        )

        return AgentOutcome(
            goal_status=goal_status,
            verification_status=verification_status,
            constraint_checks=checks,
            evidence_artifact_ids=_unique_uuids(evidence.artifact_ids),
            evidence_refs=evidence.evidence_refs,
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

        # Artifact ID 证据 + 角色化证据锚点（W1-04）
        for key, role in _EVIDENCE_REF_KEYS:
            value = state.get(key)
            if value:
                evidence.artifact_ids.append(str(value))
                evidence.evidence_refs.append(
                    OutcomeEvidenceRef(artifact_id=uuid.UUID(str(value)), role=role)  # type: ignore[arg-type]
                )
        for mapping_key, role in (
            ("script_artifact_ids", "script"),
            ("evaluation_artifact_ids", "evaluation"),
        ):
            for value in (state.get(mapping_key) or {}).values():
                evidence.artifact_ids.append(str(value))
                evidence.evidence_refs.append(
                    OutcomeEvidenceRef(artifact_id=uuid.UUID(str(value)), role=role)  # type: ignore[arg-type]
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
    ) -> RecommendedNextAction | None:
        """确定性建议（大纲影响 → 修订受影响剧本）。

        W1-04：仅有 unverified 待判断项不构成后续计划——缺证据不触发
        无限改稿，作者判断后手动发起。只输出意图与目标描述；执行目标
        （Artifact ID）由 lifecycle 用当前最新 Artifact 重新解析。
        """
        if evidence.dependent_script_ids and evidence.intent == "revise_outline":
            episode = evidence.changed_episodes[0] if evidence.changed_episodes else None
            return RecommendedNextAction(
                intent="revise_script",
                target=ActionTarget(target_type="script", episode_number=episode),
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
