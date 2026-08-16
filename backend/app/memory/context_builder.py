"""ContextBuilder — 按预算组装 Skill 上下文 (C-06).

职责（见 DEV_PLAN §9.3、C-06 任务卡）：
- 按比例分配 token 预算并组装上下文
- 超预算时按约定顺序裁剪（不静默截断当前场景）
- 输出 ContextManifest 记录使用/裁剪的资产

模块边界：
- 纯组装逻辑，不调用 LLM、不访问数据库
- 不管理具体内容获取（由调用方传入）
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 预算分配比例（见 DEV_PLAN §9.3 表格）
_BUDGET_ALLOCATION: dict[str, float] = {
    "system_rules": 0.15,
    "user_request": 0.10,
    "story_bible_outline": 0.25,
    "previous_summary_continuity": 0.20,
    "rag_fragments": 0.15,
    "current_target": 0.00,  # 使用剩余预算
}

# 裁剪顺序（§9.3 规则）
_TRUNCATION_ORDER = [
    "rag_fragments",        # 1. 删除低分 RAG 片段
    "previous_summary_continuity",  # 2. 只保留与当前集相关的连续性
    "user_request",         # 3. 将较早会话换成摘要
    "story_bible_outline",  # 4. 缩短非目标集大纲
    "current_target",       # 5. 当前稿件按场景分段（标记但不静默截断）
]

# 最少保留 token 数
_MIN_CURRENT_TARGET_TOKENS = 2000  # 当前目标场景保底 token
_MIN_SYSTEM_RULES_TOKENS = 500    # 系统规则保底 token


# ========================================================================
# ContextManifest
# ========================================================================


class ContextManifest(BaseModel):
    """上下文组装清单 — 记录使用了哪些内容以及裁剪决策。

    可用于调试和审计上下文预算使用情况。
    """

    model_config = {"extra": "forbid"}

    sections_used: list[str] = Field(
        default_factory=list, description="最终使用的上下文分段列表"
    )
    sections_cut: list[str] = Field(
        default_factory=list, description="被裁剪的分段列表"
    )
    sections_truncated: list[str] = Field(
        default_factory=list, description="被部分截断的分段列表"
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
        """检查当前目标场景是否被截断（验收项：不能静默截断）。"""
        return "current_target" in self.sections_truncated


# ========================================================================
# ContextBuilder
# ========================================================================


class ContextBuilder:
    """上下文组装器（§9.3 预算管理）。

    按约定比例分配 token 预算，超预算时按规则裁剪。

    使用方法:
        builder = ContextBuilder(budget_tokens=16000)
        context_text, manifest = builder.build(
            system_rules="...",
            user_request="...",
            story_bible_outline="...",
            previous_summary_continuity="...",
            rag_fragments="...",
            current_target="...",
        )
    """

    def __init__(self, budget_tokens: int = 16000) -> None:
        """初始化 ContextBuilder。

        Args:
            budget_tokens: 上下文总 token 预算（默认 16000）
        """
        self.budget_tokens = budget_tokens
        # 估算系数：1 token ≈ 1.5 中文字符（保守估算）
        self._chars_per_token = 1.5

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
        """按预算分配组装上下文。

        Args:
            system_rules: 系统规则与输出 Schema 文本
            user_request: 当前用户请求
            story_bible_outline: StoryBible 与当前大纲
            previous_summary_continuity: 前集摘要与连续性状态
            rag_fragments: RAG 检索片段
            current_target: 当前稿件或目标场景

        Returns:
            (assembled_context_text, ContextManifest)
        """
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

        # 第一阶段：按比例分配
        allocated = self._allocate_budget(sections)

        # 第二阶段：检查是否超预算
        total_allocated = sum(allocated.values())
        if total_allocated > self.budget_tokens:
            allocated, truncation_warnings = self._apply_truncation(
                sections, allocated, total_allocated
            )
            for w in truncation_warnings:
                manifest.warnings.append(w)
                logger.warning("ContextBuilder: %s", w)

        # 第三阶段：截断到分配预算
        final_sections: dict[str, str] = {}
        for key, text in sections.items():
            max_chars = int(allocated.get(key, 0) * self._chars_per_token)
            if max_chars <= 0 and text:
                manifest.sections_cut.append(key)
                continue
            if len(text) > max_chars > 0:
                final_sections[key] = self._truncate_text(text, max_chars)
                manifest.sections_truncated.append(key)
            else:
                final_sections[key] = text
            if final_sections[key]:
                manifest.sections_used.append(key)

        # 组装最终文本
        context_parts: list[str] = []
        header_mapping = {
            "system_rules": "## 系统规则与输出要求",
            "user_request": "## 当前用户请求",
            "story_bible_outline": "## 故事设定与大纲",
            "previous_summary_continuity": "## 连续性状态",
            "rag_fragments": "## 参考资料",
            "current_target": "## 当前任务目标",
        }

        for key in ["system_rules", "user_request", "story_bible_outline",
                     "previous_summary_continuity", "rag_fragments", "current_target"]:
            text = final_sections.get(key, "")
            if text:
                context_parts.append(header_mapping.get(key, f"## {key}"))
                context_parts.append(text)
                context_parts.append("")

        assembled = "\n".join(context_parts)

        # 计算估算 token
        estimated = self._estimate_tokens(assembled)
        manifest.estimated_tokens = estimated
        manifest.budget_remaining = max(0, self.budget_tokens - estimated)

        # 最终检查：当前目标是否被静默截断
        if "current_target" in manifest.sections_truncated:
            warning = (
                "当前目标场景超出预算，已被截断——不应静默发生！"
                "请减少前集摘要或 RAG 片段以腾出空间。"
            )
            manifest.warnings.append(warning)
            logger.warning("ContextBuilder: %s", warning)

        return assembled, manifest

    # ---- 内部方法 ----

    def _allocate_budget(self, sections: dict[str, str]) -> dict[str, int]:
        """按 §9.3 比例分配预算。

        current_target 不占固定比例，使用分配后剩余的全部预算。
        """
        allocated: dict[str, int] = {}

        for key, ratio in _BUDGET_ALLOCATION.items():
            if key == "current_target":
                continue
            allocated[key] = int(self.budget_tokens * ratio)

        # current_target 使用剩余预算
        other_total = sum(allocated.values())
        allocated["current_target"] = self.budget_tokens - other_total

        return allocated

    def _apply_truncation(
        self,
        sections: dict[str, str],
        allocated: dict[str, int],
        total_allocated: int,
    ) -> tuple[dict[str, int], list[str]]:
        """按 §9.3 裁剪顺序缩减预算。

        Returns:
            (adjusted_allocations, warnings)
        """
        warnings: list[str] = []
        overflow = total_allocated - self.budget_tokens

        for key in _TRUNCATION_ORDER:
            if overflow <= 0:
                break

            current = allocated.get(key, 0)
            if current <= 0:
                continue

            if key == "current_target":
                # 当前场景不能降到保底以下
                min_tokens = _MIN_CURRENT_TARGET_TOKENS
                if current > min_tokens:
                    reduction = min(overflow, current - min_tokens)
                    allocated[key] = current - reduction
                    overflow -= reduction
                    if reduction > 0:
                        warnings.append(
                            f"裁剪 '{key}': {current} → {allocated[key]} tokens"
                        )
            else:
                # 非关键区段可裁剪更多
                reduction = min(overflow, current)
                allocated[key] = current - reduction
                overflow -= reduction
                if reduction > 0:
                    warnings.append(
                        f"裁剪 '{key}': {current} → {allocated[key]} tokens"
                    )

        if overflow > 0:
            # 溢出未解决，记录但不再继续裁剪
            warnings.append(
                f"预算超支 {overflow} tokens 无法通过正常裁剪消除，"
                f"current_target 保持 {allocated.get('current_target', 0)} tokens"
            )

        return allocated, warnings

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

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """粗略估算中文文本的 token 数。

        保守估算：1 token ≈ 1.5 中文字符。
        """
        if not text:
            return 0
        return int(len(text) / 1.5)
