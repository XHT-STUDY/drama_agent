"""上下文策略与 token 估算模型 (G-02)。

ContextBuilder 依赖本模块：
- ContextSection：上下文分段的枚举（预算分配的最小单元）；
- TaskKind：任务类型——决定采用哪套组装策略；
- TaskContextPolicy：分任务策略（各分段权重 + 必需段落）；
- ContextTooLargeError：超限异常——当前稿件无法保留时抛出，而非静默截断；
- TokenEstimator：token 估算适配器（默认字符比 1.5）。

模块边界：纯领域类型，不访问数据库、不调用 LLM。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.errors import AppError


class TaskKind(StrEnum):
    """任务类型——决定上下文组装策略 (G-02)。"""

    REQUIREMENT = "requirement"
    STORY_BIBLE = "story_bible"
    OUTLINE = "outline"
    WRITER = "writer"
    EVALUATOR = "evaluator"
    REVISER = "reviser"


class ContextSection(StrEnum):
    """上下文分段——组装与预算分配的最小单元 (G-02)。"""

    SYSTEM_RULES = "system_rules"
    USER_REQUEST = "user_request"
    STORY_BIBLE_OUTLINE = "story_bible_outline"
    PREVIOUS_SUMMARY_CONTINUITY = "previous_summary_continuity"
    RAG_FRAGMENTS = "rag_fragments"
    CURRENT_TARGET = "current_target"


class TaskContextPolicy(BaseModel):
    """分任务上下文策略 (G-02)。

    ratios 记录除 current_target 外各分段权重（相对权重即可，组装时按
    "实际非空段落"归一化）；current_target 作为输出缓冲使用剩余预算，
    且永不截断（放不下即抛 ContextTooLargeError）。
    required_sections：该任务必须提供的段落（缺失只记警告，不阻断）。
    """

    model_config = {"extra": "forbid"}

    task: TaskKind = Field(..., description="任务类型")
    ratios: dict[ContextSection, float] = Field(
        ..., description="各分段权重（不含 current_target，可不必和为 1）"
    )
    required_sections: list[ContextSection] = Field(
        default_factory=list, description="该任务必须提供的段落"
    )

    def allocation(self, budget_tokens: int) -> dict[ContextSection, int]:
        """按权重分配预算；current_target 取剩余作为输出缓冲。"""
        allocated: dict[ContextSection, int] = {
            section: int(budget_tokens * ratio)
            for section, ratio in self.ratios.items()
        }
        used = sum(allocated.values())
        allocated[ContextSection.CURRENT_TARGET] = max(0, budget_tokens - used)
        return allocated


# ---- 分任务策略表（G-02）----
# 权重反映"该任务最依赖哪些上下文"：
# - requirement 重用户请求（用户说了什么最关键）；
# - story_bible 重故事设定；
# - outline 重设定 + 用户请求；
# - writer 重设定 / 连续性 / RAG（多轮会话的摘要也进连续性段）；
# - evaluator / reviser 重连续性 + 设定（评估与修订需全局视野）。

_POLICIES: dict[TaskKind, TaskContextPolicy] = {
    TaskKind.REQUIREMENT: TaskContextPolicy(
        task=TaskKind.REQUIREMENT,
        ratios={
            ContextSection.SYSTEM_RULES: 0.10,
            ContextSection.USER_REQUEST: 0.35,
            ContextSection.STORY_BIBLE_OUTLINE: 0.10,
            ContextSection.PREVIOUS_SUMMARY_CONTINUITY: 0.20,
            ContextSection.RAG_FRAGMENTS: 0.15,
        },
        required_sections=[ContextSection.USER_REQUEST, ContextSection.CURRENT_TARGET],
    ),
    TaskKind.STORY_BIBLE: TaskContextPolicy(
        task=TaskKind.STORY_BIBLE,
        ratios={
            ContextSection.SYSTEM_RULES: 0.10,
            ContextSection.USER_REQUEST: 0.15,
            ContextSection.STORY_BIBLE_OUTLINE: 0.40,
            ContextSection.PREVIOUS_SUMMARY_CONTINUITY: 0.10,
            ContextSection.RAG_FRAGMENTS: 0.15,
        },
        required_sections=[ContextSection.USER_REQUEST],
    ),
    TaskKind.OUTLINE: TaskContextPolicy(
        task=TaskKind.OUTLINE,
        ratios={
            ContextSection.SYSTEM_RULES: 0.10,
            ContextSection.USER_REQUEST: 0.20,
            ContextSection.STORY_BIBLE_OUTLINE: 0.30,
            ContextSection.PREVIOUS_SUMMARY_CONTINUITY: 0.10,
            ContextSection.RAG_FRAGMENTS: 0.15,
        },
        required_sections=[ContextSection.USER_REQUEST, ContextSection.STORY_BIBLE_OUTLINE],
    ),
    TaskKind.WRITER: TaskContextPolicy(
        task=TaskKind.WRITER,
        ratios={
            ContextSection.SYSTEM_RULES: 0.10,
            ContextSection.USER_REQUEST: 0.08,
            ContextSection.STORY_BIBLE_OUTLINE: 0.25,
            ContextSection.PREVIOUS_SUMMARY_CONTINUITY: 0.22,
            ContextSection.RAG_FRAGMENTS: 0.15,
        },
        required_sections=[ContextSection.CURRENT_TARGET],
    ),
    TaskKind.EVALUATOR: TaskContextPolicy(
        task=TaskKind.EVALUATOR,
        ratios={
            ContextSection.SYSTEM_RULES: 0.10,
            ContextSection.USER_REQUEST: 0.10,
            ContextSection.STORY_BIBLE_OUTLINE: 0.25,
            ContextSection.PREVIOUS_SUMMARY_CONTINUITY: 0.20,
            ContextSection.RAG_FRAGMENTS: 0.10,
        },
        required_sections=[ContextSection.CURRENT_TARGET],
    ),
    TaskKind.REVISER: TaskContextPolicy(
        task=TaskKind.REVISER,
        ratios={
            ContextSection.SYSTEM_RULES: 0.10,
            ContextSection.USER_REQUEST: 0.10,
            ContextSection.STORY_BIBLE_OUTLINE: 0.20,
            ContextSection.PREVIOUS_SUMMARY_CONTINUITY: 0.25,
            ContextSection.RAG_FRAGMENTS: 0.10,
        },
        required_sections=[ContextSection.CURRENT_TARGET],
    ),
}


def get_policy(task: str | TaskKind) -> TaskContextPolicy:
    """按任务类型取策略；未知任务回退 writer 策略（防御）。"""
    if isinstance(task, str):
        try:
            task = TaskKind(task)
        except ValueError:
            task = TaskKind.WRITER
    return _POLICIES[task]


class ContextTooLargeError(AppError):
    """上下文超限——当前稿件无法在预算内完整保留 (G-02)。

    按 G-02 验收"当前稿件不能无提示截断"：build_for 宁可抛此异常，
    也不静默截断 current_target。调用方应收缩输入（更短的摘要 /
    更少 RAG / 更大预算）后重试。
    """

    status_code = 413
    code = "CONTEXT_TOO_LARGE"


class TokenEstimator(ABC):
    """token 估算适配器 (G-02)——屏蔽不同计费/窗口口径。"""

    @abstractmethod
    def estimate(self, text: str) -> int:
        """估算给定文本的 token 数。"""

    @property
    @abstractmethod
    def chars_per_token(self) -> float:
        """每 token 对应的字符数（用于把 token 预算换算为字符上限）。"""


class CharacterRatioEstimator(TokenEstimator):
    """默认估算器：1 token ≈ 1.5 中文字符（保守口径）。

    与 C-06 的估算一致（1 token ≈ 1.5 字符）。
    """

    def __init__(self, chars_per_token: float = 1.5) -> None:
        if chars_per_token <= 0:
            raise ValueError("chars_per_token 必须为正数")
        self._chars_per_token = chars_per_token

    def estimate(self, text: str) -> int:
        if not text:
            return 0
        return int(len(text) / self._chars_per_token)

    @property
    def chars_per_token(self) -> float:
        return self._chars_per_token
