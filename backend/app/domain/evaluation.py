"""剧本评估模型 — EvaluationIssue 与 EvaluationReport（§5.8, Phase E）。

评估服务端负责：
- 计算 weighted overall_score（不采用 LLM 自报总分）；
- 按确定性规则判断 need_revision；
- 记录 rubric_version 以支持评估标准演进。
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.domain.enums import (
    DEFAULT_EVALUATION_WEIGHTS,
    EvaluationDimension,
    Severity,
)
from app.domain.script import ScriptDraft


class EvaluationInput(BaseModel):
    """Evaluation Skill 的输入模型 (E-02)。

    封装单集剧本、本集大纲、StoryBible 与可选客观特征。
    评估器不注入其他集的评估结论。
    """

    model_config = {"extra": "forbid"}

    episode_number: int = Field(..., description="被评估的集号", ge=1)
    script_draft: ScriptDraft = Field(..., description="待评估的单集剧本")
    episode_outline: dict[str, Any] = Field(
        default_factory=dict, description="本集大纲（EpisodeOutline 的 dict 表示）"
    )
    story_bible: dict[str, Any] = Field(
        default_factory=dict, description="StoryBible 的 dict 表示（必要设定）"
    )
    script_features: dict[str, Any] = Field(
        default_factory=dict,
        description="客观辅助特征（场景数/对白占比/钩子等），可预先计算传入",
    )


class EvaluationIssue(BaseModel):
    """评估中发现的问题。

    每个问题必须绑定到具体维度、提供证据和诊断。
    """

    model_config = {"extra": "forbid"}

    issue_id: str = Field(..., description="问题唯一标识", min_length=1)
    dimension: EvaluationDimension = Field(..., description="对应评估维度")
    severity: Severity = Field(..., description="严重程度")
    scene_number: int | None = Field(
        default=None, description="问题所在场次，null 表示全局问题", ge=1
    )
    evidence: str = Field(
        ..., description="来自剧本原文的证据", min_length=1
    )
    diagnosis: str = Field(
        ..., description="问题诊断与分析", min_length=1
    )
    suggestion: str = Field(
        ..., description="改进建议", min_length=1
    )


class EvaluationReport(BaseModel):
    """单集评估报告。

    overall_score 由服务端按默认权重计算；
    need_revision 由确定性规则判断（overall < 75 或存在 high 严重问题或 compliance < 60）。
    """

    model_config = {"extra": "forbid"}

    episode_number: int = Field(..., description="集号", ge=1)
    script_artifact_id: UUID = Field(..., description="被评估的 Script Artifact ID")
    rubric_version: str = Field(..., description="使用的评分标准版本", min_length=1)
    dimension_scores: dict[EvaluationDimension, int] = Field(
        ..., description="各维度原始评分（0–100）"
    )
    overall_score: float = Field(
        default=0.0,
        description="加权总分（0–100），由服务端计算",
        ge=0.0,
        le=100.0,
    )
    strengths: list[str] = Field(default_factory=list, description="亮点")
    issues: list[EvaluationIssue] = Field(
        default_factory=list, description="发现的问题列表"
    )
    revision_suggestions: list[str] = Field(
        default_factory=list, description="修订建议"
    )
    need_revision: bool = Field(
        default=False,
        description="是否需要修订，由服务端规则确定",
    )
    risk_flags: list[str] = Field(
        default_factory=list, description="风险标记（合规、内容安全等）"
    )

    @model_validator(mode="after")
    def _check_dimension_coverage(self) -> "EvaluationReport":
        """必须覆盖全部九个评估维度。"""
        missing = set(EvaluationDimension) - set(self.dimension_scores.keys())
        if missing:
            raise ValueError(
                f"dimension_scores 缺少以下维度: "
                f"{', '.join(d.value for d in sorted(missing, key=lambda d: d.value))}"
            )
        return self

    @model_validator(mode="after")
    def _check_scores_in_range(self) -> "EvaluationReport":
        """每个维度评分必须在 0–100 范围内。"""
        for dim, score in self.dimension_scores.items():
            if not (0 <= score <= 100):
                raise ValueError(
                    f"维度 {dim.value} 评分为 {score}，超出 [0, 100] 范围"
                )
        return self


# ========== 确定性计算函数 ==========


def compute_overall_score(
    dimension_scores: dict[EvaluationDimension, int],
    weights: dict[EvaluationDimension, float] | None = None,
) -> float:
    """按权重计算加权总分。

    Args:
        dimension_scores: 各维度原始评分（0–100）。
        weights: 维度权重映射，默认使用 DEFAULT_EVALUATION_WEIGHTS。

    Returns:
        加权总分，保留一位小数。
    """
    weights = weights if weights is not None else DEFAULT_EVALUATION_WEIGHTS
    total = sum(
        dimension_scores.get(dim, 0) * weight
        for dim, weight in weights.items()
    )
    return round(total, 1)


def compute_need_revision(
    overall_score: float,
    issues: list[EvaluationIssue],
    dimension_scores: dict[EvaluationDimension, int] | None = None,
) -> bool:
    """按确定性规则判断是否需要修订。

    规则：
    1. overall_score < 75；
    2. 存在 severity="high" 的问题；
    3. compliance_safety 评分 < 60。

    Args:
        overall_score: 加权总分。
        issues: 问题列表。
        dimension_scores: 维度评分（用于判断 compliance_safety）。

    Returns:
        是否需要修订。
    """
    if overall_score < 75:
        return True
    if any(issue.severity == "high" for issue in issues):
        return True
    if dimension_scores is not None:
        compliance = dimension_scores.get(EvaluationDimension.COMPLIANCE_SAFETY)
        if compliance is not None and compliance < 60:
            return True
    return False
