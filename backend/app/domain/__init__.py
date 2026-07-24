"""DramaAgent 领域层 — 纯数据模型、枚举与业务规则。

模块边界（见 DEV_PLAN.md §4.1）：
- domain 层只包含 Schema、枚举和纯规则函数；
- 禁止网络、数据库、LLM 调用；
- 所有 Pydantic 模型均设置 extra="forbid"。
"""

from app.domain.continuity import (
    CharacterState,
    ContinuityState,
    EpisodeSummary,
    RelationshipChange,
    StoryLoop,
    TimelineEvent,
)
from app.domain.enums import (
    ArtifactStatus,
    ArtifactType,
    EvaluationDimension,
    ProjectStatus,
)
from app.domain.evaluation import (
    EvaluationIssue,
    EvaluationReport,
    compute_need_revision,
    compute_overall_score,
)
from app.domain.outline import EpisodeOutline, EpisodeOutlineSet, OutlineInput
from app.domain.requirement import NeedsUserInput, NormalizedRequirement, RequirementInput
from app.domain.revision import RevisionOperation, RevisionPlan
from app.domain.script import DialogueLine, EpisodeWriterInput, Scene, ScriptDraft
from app.domain.story_bible import CharacterProfile, StoryBible, StoryBibleInput

__all__ = [
    # Enums
    "ProjectStatus",
    "ArtifactType",
    "ArtifactStatus",
    "EvaluationDimension",
    # Requirement
    "NormalizedRequirement",
    "NeedsUserInput",
    "RequirementInput",
    # StoryBible
    "CharacterProfile",
    "StoryBible",
    "StoryBibleInput",
    # Outline
    "EpisodeOutline",
    "EpisodeOutlineSet",
    "OutlineInput",
    # Script
    "DialogueLine",
    "Scene",
    "ScriptDraft",
    "EpisodeWriterInput",
    # Evaluation
    "EvaluationIssue",
    "EvaluationReport",
    "compute_overall_score",
    "compute_need_revision",
    # Revision
    "RevisionOperation",
    "RevisionPlan",
    # Continuity
    "EpisodeSummary",
    "StoryLoop",
    "CharacterState",
    "RelationshipChange",
    "TimelineEvent",
    "ContinuityState",
]
