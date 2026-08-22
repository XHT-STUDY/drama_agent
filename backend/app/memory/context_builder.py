"""ContextBuilder — 按预算组装 Skill 上下文 (C-06 / G-02).

职责（见 DEV_PLAN §9.3、C-06 任务卡、G-02 任务卡）：
- 按任务策略（TaskContextPolicy）分配 token 预算并组装上下文；
- 超预算时按优先级裁剪辅助段落，**当前稿件（current_target）永不静默截断**——
  放不下即抛 ContextTooLargeError（G-02 验收）；
- 记录 ContextManifest（使用/裁剪/裁剪原因/各段 token 估算/RAG chunk IDs）。

模块边界：
- 纯组装逻辑，不调用 LLM、不访问数据库；
- 不管理具体内容获取（由调用方传入）；
- 策略与超限异常定义在 app/domain/context.py。
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.domain.context import (
    CharacterRatioEstimator,
    ContextTooLargeError,
    TaskContextPolicy,
    TaskKind,
    TokenEstimator,
    get_policy,
)

logger = logging.getLogger(__name__)


# 最少保留 token 数（G-02：作为文档化常量，当前稿件按"永不截断"保证）
_MIN_CURRENT_TARGET_TOKENS = 2000  # 当前目标场景保底 token（输出缓冲口径）


# ========================================================================
# ContextManifest
# ========================================================================


class ContextManifest(BaseModel):
    """上下文组装清单 — 记录使用了哪些内容以及裁剪决策。

    可用于调试和审计上下文预算使用情况。
    """

    model_config = {"extra": "forbid"}

    task: str = Field(
        default="", description="G-02：本次组装所采用的任务类型"
    )
    sections_used: list[str] = Field(
        default_factory=list, description="最终使用的上下文分段列表"
    )
    sections_cut: list[str] = Field(
        default_factory=list, description="被完全移除的分段列表"
    )
    sections_truncated: list[str] = Field(
        default_factory=list, description="被部分截断的分段列表"
    )
    truncation_reasons: list[str] = Field(
        default_factory=list, description="G-02：各段裁剪/移除的原因"
    )
    section_estimates: dict[str, int] = Field(
        default_factory=dict, description="G-02：各段最终文本的估算 token 数"
    )
    estimated_tokens: int = Field(
        default=0, description="估算最终上下文 token 数", ge=0
    )
    budget_total: int = Field(
        default=0, description="总预算 token 数", ge=0
    )
    budget_remaining: int = Field(
        default=0, description="剩余未使用 token 数", ge=0
    )
    warnings: list[str] = Field(
        default_factory=list, description="组装过程中的告警信息"
    )
    rag_chunk_ids: list[str] = Field(
        default_factory=list,
        description="本次上下文引用的 RAG chunk ID 列表（D-05 记录，G-02 完整接入组装）",
    )

    def has_current_target_cut(self) -> bool:
        """检查当前目标场景是否被截断（验收项：不能静默截断）。

        G-02 起 current_target 永不截断（放不下即抛 ContextTooLargeError），
        因此正常构建结果此方法恒为 False——保留方法以兼容既有调用方。
        """
        return "current_target" in self.sections_truncated


# ========================================================================
# ContextBuilder
# ========================================================================


class ContextBuilder:
    """上下文组装器（§9.3 预算管理 + G-02 分任务策略）。

    按任务策略分配 token 预算；current_target 作为输出缓冲优先保留，
    超预算时先裁剪辅助段落，仍放不下则抛 ContextTooLargeError。

    使用方法:
        builder = ContextBuilder(budget_tokens=16000)
        context_text, manifest = builder.build_for(
            "writer",
            system_rules="...",
            user_request="...",
            story_bible_outline="...",
            previous_summary_continuity="...",
            rag_fragments="...",
            rag_chunk_ids=[...],
            current_target="...",
        )
    """

    def __init__(
        self,
        budget_tokens: int = 16000,
        *,
        token_estimator: TokenEstimator | None = None,
    ) -> None:
        """初始化 ContextBuilder。

        Args:
            budget_tokens: 上下文总 token 预算（默认 16000）
            token_estimator: token 估算器；缺省用 CharacterRatioEstimator(1.5)
        """
        self.budget_tokens = budget_tokens
        self._estimator = token_estimator or CharacterRatioEstimator()

    # ---- 公开 API ----

    def build(
        self,
        system_rules: str = "",
        user_request: str = "",
        story_bible_outline: str = "",
        previous_summary_continuity: str = "",
        rag_fragments: str = "",
        current_target: str = "",
    ) -> tuple[str, ContextManifest]:
        """兼容 C-06 的 build —— 以 writer 策略组装（G-02 保留入口）。

        等价于 build_for(TaskKind.WRITER, ...)。
        """
        return self.build_for(
            TaskKind.WRITER,
            system_rules=system_rules,
            user_request=user_request,
            story_bible_outline=story_bible_outline,
            previous_summary_continuity=previous_summary_continuity,
            rag_fragments=rag_fragments,
            current_target=current_target,
        )

    def build_for(
        self,
        task: str | TaskKind,
        *,
        system_rules: str = "",
        user_request: str = "",
        story_bible_outline: str = "",
        previous_summary_continuity: str = "",
        rag_fragments: str = "",
        rag_chunk_ids: list[str] | None = None,
        current_target: str = "",
        protected_sections: set[str] | None = None,
    ) -> tuple[str, ContextManifest]:
        """按任务策略组装上下文（G-02）。

        Args:
            task: 任务类型（writer/evaluator/...）
            system_rules: 系统规则与输出 Schema 文本
            user_request: 当前用户请求
            story_bible_outline: StoryBible 与当前大纲
            previous_summary_continuity: 前集摘要与连续性状态（含会话摘要）
            rag_fragments: RAG 检索片段
            rag_chunk_ids: 本次上下文引用的 RAG chunk UUID（回填 manifest）
            current_target: 当前稿件/目标场景——永不静默截断

        Returns:
            (assembled_context_text, ContextManifest)

        Raises:
            ContextTooLargeError: current_target 无法在预算内完整保留时
        """
        policy = get_policy(task)
        sections: dict[str, str] = {
            "system_rules": system_rules,
            "user_request": user_request,
            "story_bible_outline": story_bible_outline,
            "previous_summary_continuity": previous_summary_continuity,
            "rag_fragments": rag_fragments,
            "current_target": current_target,
        }

        manifest = ContextManifest()
        manifest.budget_total = self.budget_tokens
        manifest.task = policy.task.value

        for section in policy.required_sections:
            if not sections.get(section.value, ""):
                logger.warning(
                    "任务 %s 缺少必需段落 %s（继续组装，结果可能不完整）",
                    policy.task.value, section.value,
                )

        # ---- 输出缓冲：current_target 优先，永不截断 ----
        protected = set(protected_sections or {"current_target"})
        protected.add("current_target")
        allocated = self._allocate_with_output_buffer(sections, policy, protected)

        # ---- 逐段落到分配上限 + 记录裁剪原因 ----
        final_sections, manifest = self._fit_sections(
            sections, allocated, manifest, protected
        )

        # ---- 组装最终文本 ----
        assembled = self._assemble(final_sections)

        # ---- 统计 ----
        manifest.estimated_tokens = self._estimator.estimate(assembled)
        manifest.budget_remaining = max(0, self.budget_tokens - manifest.estimated_tokens)
        manifest.rag_chunk_ids = list(rag_chunk_ids or [])

        return assembled, manifest

    # ---- 内部方法 ----

    def _allocate_with_output_buffer(
        self,
        sections: dict[str, str],
        policy: TaskContextPolicy,
        protected_sections: set[str],
    ) -> dict[str, int]:
        """分配预算：current_target 完整保留，其余段分享剩余。

        若 current_target 单独就超过总预算 → 抛 ContextTooLargeError（G-02）。
        其余段按策略权重归一化到"实际非空段落"上分配。
        """
        ratios: dict[str, float] = {
            section.value: ratio for section, ratio in policy.ratios.items()
        }

        protected_tokens: dict[str, int] = {
            key: self._chars_to_tokens(len(sections.get(key, "") or ""))
            for key in protected_sections
            if sections.get(key, "")
        }
        protected_total = sum(protected_tokens.values())

        if protected_total > self.budget_tokens:
            largest = max(protected_tokens, key=lambda name: protected_tokens[name])
            raise ContextTooLargeError(
                f"受保护段落 {largest} 需要约 {protected_tokens[largest]} tokens，"
                f"受保护内容合计 {protected_total} tokens，已超过总预算 "
                f"{self.budget_tokens} tokens。请缩小当前请求/活动目标或调大预算。"
            )

        remaining = self.budget_tokens - protected_total

        # 其余段：只在非空段落之间按权重归一化
        active_others = [
            key for key, text in sections.items()
            if key not in protected_sections and text
        ]
        weight_sum = sum(ratios.get(key, 0.0) for key in active_others)

        allocated: dict[str, int] = dict(protected_tokens)
        if weight_sum > 0:
            for key in active_others:
                allocated[key] = int(remaining * ratios.get(key, 0.0) / weight_sum)
        return allocated

    def _fit_sections(
        self,
        sections: dict[str, str],
        allocated: dict[str, int],
        manifest: ContextManifest,
        protected_sections: set[str],
    ) -> tuple[dict[str, str], ContextManifest]:
        """把各段文本裁到分配上限；current_target 保证完整。"""
        final_sections: dict[str, str] = {}

        for key, text in sections.items():
            if not text:
                continue

            max_tokens = allocated.get(key, 0)
            if max_tokens <= 0:
                manifest.sections_cut.append(key)
                reason = f"{key}: 预算耗尽，被完全移除"
                manifest.truncation_reasons.append(reason)
                manifest.warnings.append(reason)
                continue

            max_chars = self._tokens_to_chars(max_tokens)
            if len(text) > max_chars:
                if key in protected_sections:
                    raise ContextTooLargeError(
                        f"受保护段落 {key} 无法在预算内完整保留；"
                        "请缩小当前请求/活动目标或调大预算。"
                    )
                final_sections[key] = self._truncate_text(text, max_chars)
                manifest.sections_truncated.append(key)
                reason = (
                    f"{key}: {len(text)} 字符 > 分配 {max_tokens} tokens"
                    f"（约 {max_chars} 字符），已按预算截断"
                )
                manifest.truncation_reasons.append(reason)
                manifest.warnings.append(reason)
            else:
                final_sections[key] = text

            manifest.sections_used.append(key)
            manifest.section_estimates[key] = self._estimator.estimate(final_sections[key])

        return final_sections, manifest

    def _assemble(self, final_sections: dict[str, str]) -> str:
        """按固定顺序组装上下文文本。"""
        header_mapping = {
            "system_rules": "## 系统规则与输出要求",
            "user_request": "## 当前用户请求",
            "story_bible_outline": "## 故事设定与大纲",
            "previous_summary_continuity": "## 连续性状态",
            "rag_fragments": "## 参考资料",
            "current_target": "## 当前任务目标",
        }
        order = [
            "system_rules", "user_request", "story_bible_outline",
            "previous_summary_continuity", "rag_fragments", "current_target",
        ]

        context_parts: list[str] = []
        for key in order:
            text = final_sections.get(key, "")
            if text:
                context_parts.append(header_mapping.get(key, f"## {key}"))
                context_parts.append(text)
                context_parts.append("")

        return "\n".join(context_parts)

    # ---- 单位换算 ----

    def _chars_to_tokens(self, chars: int) -> int:
        """字符数 → 估算 token 数（向上取整，避免低估）。"""
        if chars <= 0:
            return 0
        import math

        return int(math.ceil(chars / self._estimator.chars_per_token))

    def _tokens_to_chars(self, tokens: int) -> int:
        """token 数 → 字符上限。"""
        return int(tokens * self._estimator.chars_per_token)

    # ---- 辅助方法 ----

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        """截断文本到指定字符数（保留完整句）。"""
        if len(text) <= max_chars or max_chars <= 0:
            return text

        truncated = text[:max_chars]
        # 尝试在最后一个句号处断开
        for sep in ["。", "\n", "；", "，"]:
            last = truncated.rfind(sep)
            if last > max_chars * 0.7:
                truncated = truncated[:last + 1]
                break

        return truncated + "\n\n[因预算限制，此处内容已截断]"
