"""ContinuityCheckTool — 连续性规则检查工具 (F-03).

对修订后的剧本执行确定性规则检查（锁定事实回归 / 大纲关键事件 / 大纲必需角色），
输出规则层面的违规与警告。纯 Python 实现，不调用 LLM——
语义层面的检查（反转 / 状态 / 伏笔）由独立 Skill（continuity_semantic_check）负责。
"""

from __future__ import annotations

import logging
from typing import Any

from app.memory.continuity import ContinuityManager
from app.tools.protocol import Tool, ToolMetadata

logger = logging.getLogger(__name__)


class ContinuityCheckTool(Tool):
    """连续性规则检查工具。

    输入修订稿 / 原稿 / 本集大纲 / StoryBible / 锁定事实，
    输出规则检查发现的违规与警告（source=rule）。
    """

    metadata = ToolMetadata(
        name="run_continuity_rule_check",
        version="1.0",
        description=(
            "对修订稿执行确定性连续性规则检查"
            "（锁定事实回归 / 大纲关键事件 / 大纲必需角色）——不产生语义判断"
        ),
    )

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """执行规则检查。

        Args:
            episode_number: int — 被检查的集号
            script_draft: ScriptDraft — 修订后的新稿
            original_script_draft: ScriptDraft | None — 修订前的原稿
            episode_outline: dict — 本集大纲
            story_bible: dict — StoryBible（角色 ID→名称）
            locked_facts: list[str] — 锁定事实

        Returns:
            {
                "violations": list[ContinuityViolation],  # 序列化为 dict 列表
                "warnings": list[ContinuityWarning],
                "checks_run": list[str],
            }
        """
        script_draft = kwargs["script_draft"]
        violations, warnings, checks_run = ContinuityManager.run_rule_checks(
            episode_number=int(kwargs["episode_number"]),
            script_draft=script_draft,
            original_script_draft=kwargs.get("original_script_draft"),
            episode_outline=kwargs.get("episode_outline", {}),
            story_bible=kwargs.get("story_bible", {}),
            locked_facts=list(kwargs.get("locked_facts", [])),
        )

        if violations:
            logger.warning(
                "第 %d 集连续性规则检查失败: %d 个违规（checks=%s）",
                int(kwargs["episode_number"]),
                len(violations),
                checks_run,
            )
        return {
            "violations": [v.model_dump() for v in violations],
            "warnings": [w.model_dump() for w in warnings],
            "checks_run": checks_run,
        }
