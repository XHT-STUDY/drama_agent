"""大纲修订领域契约 — OutlineRevisionInput 与服务端不变量（J-07）。

输入包含旧大纲、Story Bible、用户约束与 source outline ID；
输出必须是完整 EpisodeOutlineSet（由输出 Schema 保证，不接受 patch——
只输出增量补丁的结构无法通过 EpisodeOutlineSet 构造校验）。

服务端不变量（collect_invariant_errors）:
- 集数不变（修订不增删集）;
- episode_number 唯一且连续 1..N（模型校验器兜底，此处输出可诊断错误）;
- required_characters 可追溯到 StoryBible;
- Story Bible locked_facts 未被反转（确定性"否定插入"启发式，
  见 check_locked_facts 的实现说明——语义级反转由连续性检查兜底）。
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.outline import EpisodeOutlineSet

# 否定标记：窗口命中锁定事实子句且包含这些词时判定为反转
_NEGATION_MARKERS: tuple[str, ...] = (
    "不再", "并非", "并未", "没有", "从未", "绝不会", "不是", "已取消", "已删除",
    "已推翻", "不", "未", "非",
)

# 子句滑动窗口相似度阈值（字符 bigram Dice 系数）
_SIMILARITY_THRESHOLD = 0.75


class OutlineRevisionInput(BaseModel):
    """大纲修订 Skill 的输入模型。"""

    model_config = {"extra": "forbid"}

    old_outline: EpisodeOutlineSet = Field(..., description="修订前的完整旧大纲")
    story_bible: dict[str, Any] = Field(
        ..., description="StoryBible dict 表示（含 locked_facts 与角色表）"
    )
    user_constraints: list[str] = Field(
        default_factory=list, description="用户修订约束", max_length=20
    )
    source_outline_artifact_id: UUID = Field(
        ..., description="旧大纲 Artifact UUID（溯源与幂等输入）"
    )


def normalize_text(text: str) -> str:
    """空白规范化：所有空白字符移除后比较，空白差异不算变化。"""
    return "".join(text.split())


def _episode_texts(outline: EpisodeOutlineSet) -> list[str]:
    """收集大纲全部可读文本（弧线 + 各集字段），供锁定事实扫描。"""
    texts = [normalize_text(outline.arc_summary)]
    for ep in outline.episodes:
        parts = [
            ep.title, ep.opening_hook, ep.objective, ep.core_conflict,
            ep.payoff, ep.ending_hook, ep.next_bridge,
            *ep.key_events, *ep.introduced_loops, *ep.resolved_loops,
        ]
        texts.append(normalize_text("".join(parts)))
    return texts


def _bigrams(text: str) -> set[str]:
    """字符 bigram 集合（长度 <2 时回退为单字符集合）。"""
    if len(text) < 2:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _similarity(left: str, right: str) -> float:
    """字符 bigram Dice 相似度（0..1，确定性）。"""
    left_grams, right_grams = _bigrams(left), _bigrams(right)
    if not left_grams or not right_grams:
        return 0.0
    overlap = len(left_grams & right_grams)
    return 2.0 * overlap / (len(left_grams) + len(right_grams))


def _clauses(fact: str) -> list[str]:
    """把锁定事实按标点切成子句（空白规范化，过滤过短子句）。"""
    parts = [normalize_text(p) for p in re.split(r"[，。；、！？,;!?]", fact)]
    return [p for p in parts if len(p) >= 4]


def _find_reversal(text: str, clause: str) -> str | None:
    """在文本中查找子句的"否定改写"窗口，命中时返回该窗口文本。

    窗口为长度 len(clause)-2 .. len(clause)+4 的滑动切片；
    判定条件：与子句 bigram 相似度达标，且窗口包含子句中没有的否定标记。
    子句原文逐字出现视为重述而非反转，直接跳过。
    """
    if not text or clause in text:
        return None
    clause_negations = {m for m in _NEGATION_MARKERS if m in clause}
    for window_len in range(max(2, len(clause) - 2), len(clause) + 5):
        for start in range(0, len(text) - window_len + 1):
            window = text[start : start + window_len]
            if _similarity(clause, window) < _SIMILARITY_THRESHOLD:
                continue
            window_negations = {m for m in _NEGATION_MARKERS if m in window}
            if window_negations - clause_negations:
                return window
    return None


def check_locked_facts(
    outline: EpisodeOutlineSet, locked_facts: list[str]
) -> list[str]:
    """检测新大纲是否把锁定事实改写为否定句（确定性启发式）。

    逐子句滑动窗口匹配：相似度达标的窗口内出现子句本身不包含的
    否定标记即判定反转。只能拦截"原文 + 否定词"的显式改写；
    换一种说法陈述相反内容（语义级反转）由连续性检查兜底。
    """
    texts = _episode_texts(outline)
    errors: list[str] = []
    for fact in locked_facts:
        for clause in _clauses(fact):
            for text in texts:
                window = _find_reversal(text, clause)
                if window is not None:
                    errors.append(
                        f"锁定事实疑似被反转：'{fact}'（新大纲出现否定表述 '{window}'）"
                    )
                    break
    return errors


def collect_invariant_errors(
    *,
    old_outline: EpisodeOutlineSet,
    new_outline: EpisodeOutlineSet,
    story_bible: dict[str, Any],
) -> list[str]:
    """收集大纲修订的全部服务端不变量错误（空列表 = 全部通过）。"""
    errors: list[str] = []

    # 1. 集数不变
    old_count = len(old_outline.episodes)
    new_count = len(new_outline.episodes)
    if new_count != old_count:
        errors.append(f"集数必须保持 {old_count} 集（修订不增删集），实际输出 {new_count} 集")

    # 2. 集号唯一且连续（构造校验兜底，此处给出可诊断错误）
    numbers = sorted(ep.episode_number for ep in new_outline.episodes)
    expected = list(range(1, new_count + 1))
    if numbers != expected:
        errors.append(f"集号必须为 1..{new_count} 连续不重复，实际为 {numbers}")

    # 3. required_characters 可追溯
    errors.extend(new_outline.validate_characters(story_bible))

    # 4. locked_facts 未被反转
    locked = story_bible.get("locked_facts") or []
    if isinstance(locked, list):
        errors.extend(check_locked_facts(new_outline, [str(f) for f in locked]))

    return errors
