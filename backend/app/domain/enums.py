"""DramaAgent 领域枚举与 Literal 类型别名。

集中管理所有共享枚举，避免跨模块散布魔法字符串。
所有字符串枚举使用 StrEnum（Python 3.11+），保证 JSON 序列化输出字符串值。
"""

from enum import StrEnum
from typing import Literal

# ========== 项目状态 ==========


class ProjectStatus(StrEnum):
    """项目生命周期状态。"""

    DRAFT = "draft"
    PLANNING = "planning"
    WRITING = "writing"
    EVALUATING = "evaluating"
    REVISING = "revising"
    COMPLETED = "completed"
    ARCHIVED = "archived"


# ========== Artifact 类型与状态 ==========


class ArtifactType(StrEnum):
    """Artifact 类别 — 每一种对应一个 Pydantic content schema。"""

    NORMALIZED_REQUIREMENT = "normalized_requirement"
    STORY_BIBLE = "story_bible"
    EPISODE_OUTLINE_SET = "episode_outline_set"
    SCRIPT_DRAFT = "script_draft"
    EVALUATION_REPORT = "evaluation_report"
    REVISION_PLAN = "revision_plan"
    CONTINUITY_CHECK = "continuity_check"
    CONTINUITY_STATE = "continuity_state"
    CONVERSATION_SUMMARY = "conversation_summary"
    IMPORT_CLASSIFICATION = "import_classification"
    EXPORT_FILE = "export_file"
    RETRIEVAL_TRACE = "retrieval_trace"


class ArtifactStatus(StrEnum):
    """Artifact 内容校验状态。

    状态只允许 draft → valid 或 draft → invalid；
    content 一旦写入即不可原地修改。
    """

    DRAFT = "draft"
    VALID = "valid"
    INVALID = "invalid"


# ========== 评估相关 ==========


class EvaluationDimension(StrEnum):
    """剧本评估的九个维度（§5.8）。"""

    OPENING_HOOK = "opening_hook"
    MAIN_CLARITY = "main_clarity"
    CHARACTER_APPEAL = "character_appeal"
    CONFLICT_INTENSITY = "conflict_intensity"
    PAYOFF_DENSITY = "payoff_density"
    ENDING_HOOK = "ending_hook"
    PACING = "pacing"
    VISUALIZABILITY = "visualizability"
    COMPLIANCE_SAFETY = "compliance_safety"


# ========== Literal 类型别名（用于 Schema 字段注解） ==========

# 问题严重程度
Severity = Literal["low", "medium", "high"]

# 需求来源类型
SourceType = Literal["idea", "outline", "txt", "docx"]

# 伏笔状态
LoopStatus = Literal["open", "resolved"]

# 导入内容分类
ContentType = Literal["idea_or_notes", "outline", "full_script", "reference", "unknown"]

# 导出格式
ExportFormat = Literal["markdown", "docx"]

# 评估维度 → 默认权重 映射
DEFAULT_EVALUATION_WEIGHTS: dict[EvaluationDimension, float] = {
    EvaluationDimension.OPENING_HOOK: 0.15,
    EvaluationDimension.MAIN_CLARITY: 0.10,
    EvaluationDimension.CHARACTER_APPEAL: 0.10,
    EvaluationDimension.CONFLICT_INTENSITY: 0.15,
    EvaluationDimension.PAYOFF_DENSITY: 0.15,
    EvaluationDimension.ENDING_HOOK: 0.15,
    EvaluationDimension.PACING: 0.10,
    EvaluationDimension.VISUALIZABILITY: 0.05,
    EvaluationDimension.COMPLIANCE_SAFETY: 0.05,
}
"""默认评估权重（§5.8 表格），总和必须为 1.0。"""
