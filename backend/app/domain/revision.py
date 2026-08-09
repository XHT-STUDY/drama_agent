"""修订计划模型 — RevisionOperation 与 RevisionPlan（§5.9, F-01）。

RevisionPlan 由评估报告驱动，指定要修改的场景、保留项和变化上限。
F-01 新增：确定性选集 select_revision_candidate、issue→operation 生成、
有据可依过滤，以及 RevisionPlanSkill 的输入模型 RevisionPlanInput。
"""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.domain.continuity import ContinuityState
from app.domain.evaluation import EvaluationIssue, EvaluationReport
from app.domain.script import ScriptDraft


class RevisionOperation(BaseModel):
    """单个修订操作。

    每个操作绑定到具体的评估问题，包含修改指令和必须保留的内容。
    """

    model_config = {"extra": "forbid"}

    operation_id: str = Field(..., description="操作唯一标识", min_length=1)
    target_scene_number: int | None = Field(
        default=None, description="目标场景编号，null 表示跨场景修改", ge=1
    )
    issue_ids: list[str] = Field(
        default_factory=list, description="此操作应对的 issue ID 列表"
    )
    instruction: str = Field(..., description="修订指令", min_length=1)
    preserve: list[str] = Field(
        default_factory=list, description="必须保留的内容（不可修改）"
    )
    expected_effect: str = Field(
        default="", description="预期效果描述"
    )


class RevisionPlan(BaseModel):
    """修订计划。

    指定要修订哪一集、修订操作列表、锁定事实和最大修改比例。
    MVP 中 max_change_ratio 默认 0.35。
    """

    model_config = {"extra": "forbid"}

    episode_number: int = Field(..., description="待修订的集号", ge=1)
    source_script_artifact_id: UUID = Field(
        ..., description="原稿 Artifact ID"
    )
    source_evaluation_artifact_id: UUID = Field(
        ..., description="评估报告 Artifact ID"
    )
    operations: list[RevisionOperation] = Field(
        ..., description="修订操作列表"
    )
    locked_facts: list[str] = Field(
        default_factory=list, description="本次修订必须遵守的锁定事实"
    )
    max_change_ratio: float = Field(
        default=0.35,
        description="允许的最大文本变化比例",
        ge=0.0,
        le=1.0,
    )
    user_instruction: str | None = Field(
        default=None, description="用户补充要求（不可违反锁定事实）"
    )

    @model_validator(mode="after")
    def _check_operations_non_empty(self) -> "RevisionPlan":
        """至少需要一个修订操作。"""
        if len(self.operations) < 1:
            raise ValueError("修订计划至少需要一个 operation")
        return self


class RevisionPlanInput(BaseModel):
    """RevisionPlan Skill 的输入模型 (F-01).

    封装选中的评估报告、原稿、锁定事实与来源 Artifact ID，
    供 RevisionPlanSkill 生成有据可依的修订计划。
    """

    model_config = {"extra": "forbid"}

    episode_number: int = Field(..., description="待修订的集号", ge=1)
    source_script_artifact_id: UUID = Field(
        ..., description="原稿 ScriptDraft Artifact ID"
    )
    source_evaluation_artifact_id: UUID = Field(
        ..., description="评估报告 Artifact ID（selected 集）"
    )
    script_draft: ScriptDraft = Field(..., description="待修订的原稿剧本")
    evaluation_report: EvaluationReport = Field(
        ..., description="选中集的评估报告（提供 issue 依据）"
    )
    locked_facts: list[str] = Field(
        default_factory=list, description="本次修订必须遵守的锁定事实"
    )
    max_change_ratio: float = Field(
        default=0.35,
        description="允许的最大文本变化比例",
        ge=0.0,
        le=1.0,
    )
    user_instruction: str | None = Field(
        default=None, description="用户补充要求（不可违反锁定事实）"
    )


class OperationExecution(BaseModel):
    """单个修订操作的执行情况记录 (F-02).

    记录每个操作是否被应用及其说明——用于"每个 operation 有执行结果或未执行说明"。
    status 取值:
    - applied: 已按计划执行;
    - partial: 部分执行（受 preserve/锁定事实约束而未完全落实）;
    - skipped: 未执行（有明确原因）。
    """

    model_config = {"extra": "forbid"}

    operation_id: str = Field(..., description="对应的计划 operation_id", min_length=1)
    status: Literal["applied", "partial", "skipped"] = Field(
        ..., description="执行结果: applied / partial / skipped"
    )
    note: str = Field(..., description="执行说明或未执行原因", min_length=1)


class RevisionResult(BaseModel):
    """修订结果 (F-02): 完整新稿 + 每个 operation 的执行情况。

    - script_draft 是**完整**的新 ScriptDraft，不是原地 patch（验收项）；
    - operation_executions 覆盖计划中的每一个 operation；
    - source_* 三个来源 Artifact ID 由服务端权威填充，
      保证"新稿 source 包含原稿、评估、计划"（持久化时写入 source_artifact_ids）。
    """

    model_config = {"extra": "forbid"}

    script_draft: ScriptDraft = Field(..., description="修改后的完整剧本草稿")
    operation_executions: list[OperationExecution] = Field(
        default_factory=list, description="每个 operation 的执行情况"
    )
    source_script_artifact_id: UUID = Field(
        ..., description="原稿 ScriptDraft Artifact ID"
    )
    source_evaluation_artifact_id: UUID = Field(
        ..., description="评估报告 Artifact ID"
    )
    source_revision_plan_artifact_id: UUID = Field(
        ..., description="修订计划 Artifact ID"
    )


class RevisionTaskInput(BaseModel):
    """Reviser Skill 的输入模型 (F-02).

    封装修订任务的全部上下文：原稿、计划、StoryBible、当前集大纲与连续性状态。
    source_revision_plan_artifact_id 由调用方（Service）传入，
    供修订结果绑定计划 Artifact（原稿/评估 ID 已含在 RevisionPlan 中）。
    """

    model_config = {"extra": "forbid"}

    episode_number: int = Field(..., description="待修订的集号", ge=1)
    script_draft: ScriptDraft = Field(..., description="原稿剧本")
    revision_plan: RevisionPlan = Field(..., description="修订计划")
    story_bible: dict[str, Any] = Field(
        ..., description="完整 StoryBible (dict 表示)"
    )
    episode_outline: dict[str, Any] = Field(
        ..., description="当前集大纲 (EpisodeOutline 的 dict 表示)"
    )
    continuity_state: str = Field(
        default="", description="当前连续性状态文本快照"
    )
    source_revision_plan_artifact_id: UUID = Field(
        ..., description="修订计划 Artifact ID"
    )


# ========== 确定性选集（F-01，纯函数） ==========


def select_revision_candidate(
    reports: list[EvaluationReport],
) -> EvaluationReport | None:
    """确定性选出待修订集（F-01 验收：选择逻辑不调用 LLM）。

    规则：
    1. 只从 need_revision=true 的评估报告中选择；
    2. 选择 overall_score 最低者；
    3. 同分时选择 episode_number 最小者。

    Args:
        reports: 各集评估报告列表（可无序）。

    Returns:
        选中的 EvaluationReport；无 need_revision 集时返回 None。
    """
    candidates = [r for r in reports if r.need_revision]
    if not candidates:
        return None
    return min(candidates, key=lambda r: (r.overall_score, r.episode_number))


# ========== issue → RevisionOperation（F-01，纯函数） ==========


def operations_from_issues(
    issues: list[EvaluationIssue],
    *,
    locked_facts: list[str] | None = None,
) -> list[RevisionOperation]:
    """从评估问题确定性生成修订操作。

    每个 issue 映射为一个 operation：
    - target_scene_number = issue.scene_number；
    - instruction = issue.suggestion（可执行建议）；
    - issue_ids = [issue.issue_id]（有据可依）；
    - preserve = locked_facts（不可修改内容）；
    - expected_effect = 针对该维度的预期提升。

    用于 LLM 计划缺失或全部失实时的确定性兜底，
    保证修订计划始终有据可依（F-01 验收：不允许无来源 issue 的空泛任务）。

    Args:
        issues: 评估报告中的问题列表。
        locked_facts: 必须保留的锁定事实（写入 preserve）。

    Returns:
        RevisionOperation 列表（与 issues 一一对应）。
    """
    preserve = list(locked_facts or [])
    operations: list[RevisionOperation] = []
    for idx, issue in enumerate(issues, start=1):
        operations.append(
            RevisionOperation(
                operation_id=f"op_{idx:03d}",
                target_scene_number=issue.scene_number,
                issue_ids=[issue.issue_id],
                instruction=issue.suggestion,
                preserve=preserve,
                expected_effect=f"提升 {issue.dimension.value} 维度评分",
            )
        )
    return operations


def filter_grounded_operations(
    operations: list[RevisionOperation],
    report: EvaluationReport,
) -> list[RevisionOperation]:
    """过滤掉不源自评估问题的空泛修订任务（F-01 验收）。

    判定规则：
    - 空 issue_ids 的 operation 视为无来源，剔除；
    - issue_ids 中存在 report 之外的未知 issue_id 视为臆造，剔除；
    - 只有全部 issue_ids 都来自评估报告的 operation 才被保留。

    Args:
        operations: LLM 生成的修订操作列表。
        report: 选中集的评估报告（提供合法 issue_id 集合）。

    Returns:
        仅保留有据可依的 operations。
    """
    valid_issue_ids = {i.issue_id for i in report.issues}
    grounded: list[RevisionOperation] = []
    for op in operations:
        op_issue_ids = set(op.issue_ids)
        if not op_issue_ids:
            continue
        if not op_issue_ids.issubset(valid_issue_ids):
            continue
        grounded.append(op)
    return grounded


# ========== operation 执行记录规范化（F-02，纯函数） ==========


def normalize_executions(
    plan_operations: list[RevisionOperation],
    llm_executions: list[OperationExecution],
) -> list[OperationExecution]:
    """规范化 LLM 输出的 operation 执行记录（F-02 验收：每个 operation 有执行结果或未执行说明）。

    规则：
    1. 只保留引用计划中真实 operation_id 的记录（剔除 LLM 臆造的执行记录）；
    2. 同一 operation_id 去重（保留第一条）；
    3. 计划中缺失执行记录的 operation 补确定性 "skipped" 说明（视为未执行）；
    4. 输出顺序与计划 operation 顺序一致（稳定有序，便于审计）。

    Args:
        plan_operations: 修订计划中的 operation 列表（权威顺序）。
        llm_executions: LLM 输出的执行记录（可能缺项 / 多造 / 乱序）。

    Returns:
        与 plan_operations 一一对应、有序、全覆盖的执行记录列表。
    """
    valid_ids = {op.operation_id for op in plan_operations}
    by_op: dict[str, OperationExecution] = {}
    for llm_record in llm_executions:
        if llm_record.operation_id not in valid_ids:
            continue
        if llm_record.operation_id not in by_op:
            by_op[llm_record.operation_id] = llm_record

    normalized: list[OperationExecution] = []
    for op in plan_operations:
        op_record = by_op.get(op.operation_id)
        if op_record is None:
            normalized.append(
                OperationExecution(
                    operation_id=op.operation_id,
                    status="skipped",
                    note="未提供执行说明，视为未执行",
                )
            )
        else:
            normalized.append(op_record)
    return normalized


# ========== 连续性检查（F-03） ==========


# 连续性违规类型
# - 规则检查（确定性）发现: 缺失 / 未体现类问题;
# - 语义检查（独立 Skill）发现: 反转 / 状态 / 伏笔类问题。
ContinuityViolationKind = Literal[
    "locked_fact_missing",        # 锁定事实被移除（原稿有而新稿无，规则发现）
    "locked_fact_reversed",       # 锁定事实被语义反转/矛盾（语义发现）
    "required_event_missing",     # 大纲关键事件未体现（规则发现）
    "required_character_missing", # 大纲必需角色未出场（规则发现）
    "loop_inconsistent",          # 伏笔状态不一致（语义发现）
    "character_state_change",     # 关键人物状态变化矛盾（语义发现）
    "semantic_inconsistency",     # 其他语义不一致（语义发现）
]


class ContinuityViolation(BaseModel):
    """连续性违规——阻断性，导致检查失败。

    kind 表明违规类型; source 区分发现途径（规则检查 / 语义检查）。
    规则检查确定性发现缺失类问题; 语义检查发现反转 / 状态 / 伏笔类问题。
    """

    model_config = {"extra": "forbid"}

    kind: ContinuityViolationKind = Field(..., description="违规类型")
    target: str = Field(..., description="目标对象（事实原文/事件/角色/伏笔描述）", min_length=1)
    expected: str = Field(..., description="期望的一致状态")
    actual: str = Field(..., description="修订稿中的实际情况")
    evidence: str = Field(..., description="证据（场景/台词/文本片段）", min_length=1)
    source: Literal["rule", "semantic"] = Field(
        ..., description="发现途径: rule（规则检查）/ semantic（语义检查）"
    )


class ContinuityWarning(BaseModel):
    """连续性警告——非阻断，仅提示风险。

    与 violations 分开存储: 有 warnings 不影响 pass/fail 结论。
    """

    model_config = {"extra": "forbid"}

    kind: ContinuityViolationKind = Field(..., description="警告类型")
    target: str = Field(..., description="目标对象", min_length=1)
    message: str = Field(..., description="警告说明", min_length=1)
    source: Literal["rule", "semantic"] = Field(
        ..., description="发现途径: rule / semantic"
    )


class ContinuitySemanticCheck(BaseModel):
    """语义连续性检查输出（独立 Skill 的结构化输出，F-03）。

    规则检查通过后，由独立 Skill 复核修订稿是否存在语义层面的
    一致性风险: 锁定事实被反转、关键人物状态变化矛盾、伏笔状态不一致。
    violations 为阻断性问题; warnings 为非阻断提示。
    """

    model_config = {"extra": "forbid"}

    violations: list[ContinuityViolation] = Field(
        default_factory=list, description="语义层面发现的阻断性问题"
    )
    warnings: list[ContinuityWarning] = Field(
        default_factory=list, description="语义层面的非阻断提示"
    )


class ContinuityCheckInput(BaseModel):
    """连续性检查输入（F-03）。

    输入修订后的新稿、原稿、本集大纲、StoryBible、修订前连续性状态
    与锁定事实，供规则检查与必要的语义检查验证修订稿是否破坏连续性。

    original_script_draft 用于"回归"判定: 仅当某锁定事实在原稿中出现时，
    规则检查才要求其在新稿中仍然保留（防止修订误删既有事实）;
    语义检查对所有锁定事实做反转/矛盾复核。
    """

    model_config = {"extra": "forbid"}

    episode_number: int = Field(..., description="被检查的集号", ge=1)
    script_draft: ScriptDraft = Field(..., description="修订后的新稿")
    original_script_draft: ScriptDraft | None = Field(
        default=None, description="修订前的原稿（缺失时跳过锁定事实回归检查）"
    )
    episode_outline: dict[str, Any] = Field(
        ..., description="本集大纲（key_events/required_characters/introduced_loops/resolved_loops）"
    )
    story_bible: dict[str, Any] = Field(
        ..., description="StoryBible（角色 ID → 名称映射）"
    )
    continuity_state: ContinuityState = Field(
        ..., description="修订前的连续性状态（角色状态与伏笔基线）"
    )
    locked_facts: list[str] = Field(
        ..., description="本次修订必须遵守的锁定事实"
    )


class ContinuityCheckResult(BaseModel):
    """连续性检查结果（F-03）。

    - status: pass / fail（存在 violations 即 fail，失败转 needs_manual_review，
      不自动无限改写）;
    - violations: 阻断性违规（规则 + 语义合并）;
    - warnings: 非阻断提示，与 violations 分开;
    - rule_checks_run / semantic_checks_run: 记录实际执行的检查清单，供诊断与审计。

    失败稿保存为 invalid/candidate 版本时，此结果作为诊断随稿存储。
    """

    model_config = {"extra": "forbid"}

    status: Literal["pass", "fail"] = Field(..., description="连续性检查结论")
    checked_episode_number: int = Field(..., description="被检查的集号", ge=1)
    violations: list[ContinuityViolation] = Field(
        default_factory=list, description="阻断性违规"
    )
    warnings: list[ContinuityWarning] = Field(
        default_factory=list, description="非阻断提示"
    )
    rule_checks_run: list[str] = Field(
        default_factory=list, description="已执行的规则检查"
    )
    semantic_checks_run: list[str] = Field(
        default_factory=list, description="已执行的语义检查"
    )

    @model_validator(mode="after")
    def _status_matches_violations(self) -> "ContinuityCheckResult":
        """status 与 violations 一致性: fail ⟺ 存在 violations。"""
        has_violations = bool(self.violations)
        if self.status == "pass" and has_violations:
            raise ValueError("status=pass 但存在 violations，结果自相矛盾")
        if self.status == "fail" and not has_violations:
            raise ValueError("status=fail 但没有 violations，结果自相矛盾")
        return self
