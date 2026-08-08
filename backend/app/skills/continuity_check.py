"""连续性检查技能 (F-03)。

规则优先原则（DEV_PLAN F-03）:
- 规则检查（确定性）先行: 锁定事实回归 / 大纲关键事件 / 大纲必需角色;
- 规则失败立即判定 fail，不再调用 LLM（节省调用并保证确定性）;
- 规则通过后，由独立 Skill（continuity_semantic_check）执行必要语义检查
  （锁定事实反转 / 关键人物状态变化 / 伏笔一致性），结构化输出;
- 语义检查输出 source 一律由服务端权威置为 "semantic"（不信任 LLM 自报）;
- 违规阻断（fail → needs_manual_review），警告仅提示，二者分开。

模块边界:
- Skill 只负责组装 Prompt、调用 LLM / Tool、后校验与合并
- 不直接访问 ORM、不操作前端
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any, Literal, cast

from app.agents.base import BaseAgent
from app.domain.revision import (
    ContinuityCheckInput,
    ContinuityCheckResult,
    ContinuitySemanticCheck,
    ContinuityViolation,
    ContinuityWarning,
)
from app.prompts.loader import PromptLoader
from app.skills.protocol import Skill, SkillMetadata
from app.tools.continuity_check import ContinuityCheckTool

logger = logging.getLogger(__name__)

# 语义检查清单（规则通过后由独立 Skill 执行的维度）
_SEMANTIC_CHECKS = [
    "locked_fact_reversal",    # 锁定事实反转 / 矛盾复核
    "character_state_change",  # 关键人物状态变化
    "loop_consistency",        # 伏笔状态一致性
]


class ContinuitySemanticCheckSkill(Skill):
    """语义连续性检查 Skill（独立、结构化输出，F-03）。

    规则检查通过后调用。复核修订稿在语义层面是否反转锁定事实、
    是否与关键人物状态矛盾、是否破坏伏笔一致性。
    输出 ContinuitySemanticCheck（violations + warnings），
    所有条目的 source 由服务端权威置为 "semantic"。
    """

    metadata = SkillMetadata(
        name="continuity_semantic_check",
        version="1.0",
        description=(
            "对修订稿执行语义层连续性复核"
            "（锁定事实反转 / 关键人物状态 / 伏笔一致性）——结构化输出"
        ),
    )

    # ---- 公开 API ----

    async def execute(self, context: dict[str, Any]) -> ContinuitySemanticCheck:
        """执行语义连续性检查。

        context 必需键:
            input: ContinuityCheckInput — 新稿/大纲/StoryBible/连续性状态/锁定事实
            agent: BaseAgent — 用于调用 LLM
            prompt_loader: PromptLoader — 用于加载 Prompt 模板
        context 可选键:
            rule_summary: str — 规则检查结果摘要（引导 LLM 聚焦语义维度）

        Returns:
            source 全部为 "semantic" 的 ContinuitySemanticCheck

        Raises:
            RuntimeError: LLM 调用失败
        """
        check_input: ContinuityCheckInput = context["input"]
        agent: BaseAgent = context["agent"]
        prompt_loader: PromptLoader = context["prompt_loader"]
        rule_summary: str = context.get("rule_summary", "（规则检查已通过）")

        try:
            tpl = prompt_loader.get("continuity_semantic_check")
        except KeyError as e:
            logger.error("Prompt 加载失败: %s", e)
            raise

        rendered = tpl.render(
            episode_number=str(check_input.episode_number),
            script_draft=_json.dumps(
                check_input.script_draft.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
            locked_facts=_json.dumps(
                check_input.locked_facts, ensure_ascii=False, indent=2
            ),
            continuity_state=_json.dumps(
                check_input.continuity_state.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
            episode_outline=_json.dumps(
                check_input.episode_outline, ensure_ascii=False, indent=2
            ),
            rule_summary=rule_summary,
        )

        messages: list[dict[str, str]] = [
            {"role": "user", "content": rendered},
        ]
        result = await agent.generate_structured(
            ContinuitySemanticCheck,
            messages,
            prompt_name="continuity_semantic_check",
            temperature=0.2,
        )

        if result.error_code or result.parsed is None:
            logger.error(
                "语义连续性检查 LLM 调用失败: code=%s detail=%s",
                result.error_code,
                result.error_detail,
            )
            raise RuntimeError(
                f"ContinuitySemanticCheck Skill LLM 调用失败: "
                f"{result.error_code} - {result.error_detail}"
            )

        semantic = cast(ContinuitySemanticCheck, result.parsed)

        # 权威覆盖: source 一律由服务端置为 "semantic"，不信任 LLM 自报
        violations = [
            v.model_copy(update={"source": "semantic"}) for v in semantic.violations
        ]
        warnings = [
            w.model_copy(update={"source": "semantic"}) for w in semantic.warnings
        ]

        logger.info(
            "第 %d 集语义检查完成: violations=%d warnings=%d",
            check_input.episode_number, len(violations), len(warnings),
        )
        return ContinuitySemanticCheck(violations=violations, warnings=warnings)


class ContinuityCheckSkill(Skill):
    """连续性检查 Skill（规则优先 + 必要语义，F-03）。

    1. 规则检查先行（锁定事实回归 / 大纲关键事件 / 大纲必需角色）;
    2. 规则失败 → 直接 fail，跳过语义检查;
    3. 规则通过 → 调用 ContinuitySemanticCheckSkill 复核语义层面;
    4. 合并规则 + 语义违规与警告，输出 ContinuityCheckResult。
    """

    metadata = SkillMetadata(
        name="continuity_check",
        version="1.0",
        description=(
            "对修订稿执行连续性检查：规则检查优先，必要语义检查通过独立 Skill，"
            "输出 pass/fail 与违规/警告"
        ),
    )

    def __init__(self) -> None:
        super().__init__()
        self._rule_tool = ContinuityCheckTool()
        self._semantic_skill = ContinuitySemanticCheckSkill()

    # ---- 公开 API ----

    async def execute(self, context: dict[str, Any]) -> ContinuityCheckResult:
        """执行连续性检查。

        context 必需键:
            input: ContinuityCheckInput — 新稿/原稿/大纲/StoryBible/连续性状态/锁定事实
            agent: BaseAgent — 用于调用 LLM
            prompt_loader: PromptLoader — 用于加载 Prompt 模板

        Returns:
            规则 + 语义合并后的 ContinuityCheckResult

        Raises:
            RuntimeError: 语义检查 LLM 调用失败（仅当规则通过后需要调用时）
        """
        check_input: ContinuityCheckInput = context["input"]
        agent: BaseAgent = context["agent"]
        prompt_loader: PromptLoader = context["prompt_loader"]

        # 1. 规则检查先行（确定性）
        rule_result = await self._rule_tool.execute(
            episode_number=check_input.episode_number,
            script_draft=check_input.script_draft,
            original_script_draft=check_input.original_script_draft,
            episode_outline=check_input.episode_outline,
            story_bible=check_input.story_bible,
            locked_facts=check_input.locked_facts,
        )
        rule_violations = [
            ContinuityViolation.model_validate(v) for v in rule_result["violations"]
        ]
        rule_warnings = [
            ContinuityWarning.model_validate(w) for w in rule_result["warnings"]
        ]
        rule_checks_run = list(rule_result["checks_run"])

        # 2. 规则失败 → 直接 fail，跳过语义检查（规则优先原则）
        if rule_violations:
            logger.warning(
                "第 %d 集规则检查已失败（%d 个违规），跳过语义检查",
                check_input.episode_number, len(rule_violations),
            )
            return self._build_result(
                check_input,
                violations=rule_violations,
                warnings=rule_warnings,
                rule_checks_run=rule_checks_run,
                semantic_checks_run=[],
            )

        # 3. 规则通过 → 必要语义检查（独立 Skill）
        semantic = await self._semantic_skill.execute(
            {
                "input": check_input,
                "agent": agent,
                "prompt_loader": prompt_loader,
                "rule_summary": self._rule_summary(rule_checks_run, rule_warnings),
            }
        )

        # 4. 合并
        return self._build_result(
            check_input,
            violations=rule_violations + semantic.violations,
            warnings=rule_warnings + semantic.warnings,
            rule_checks_run=rule_checks_run,
            semantic_checks_run=list(_SEMANTIC_CHECKS),
        )

    # ---- 辅助 ----

    @staticmethod
    def _build_result(
        check_input: ContinuityCheckInput,
        *,
        violations: list[ContinuityViolation],
        warnings: list[ContinuityWarning],
        rule_checks_run: list[str],
        semantic_checks_run: list[str],
    ) -> ContinuityCheckResult:
        """组装 ContinuityCheckResult，status 由违规决定（fail ⟺ 有违规）。"""
        status: Literal["pass", "fail"] = "fail" if violations else "pass"
        result = ContinuityCheckResult(
            status=status,
            checked_episode_number=check_input.episode_number,
            violations=violations,
            warnings=warnings,
            rule_checks_run=rule_checks_run,
            semantic_checks_run=semantic_checks_run,
        )
        logger.info(
            "第 %d 集连续性检查: status=%s violations=%d warnings=%d"
            " rule_checks=%d semantic_checks=%d",
            check_input.episode_number, status,
            len(violations), len(warnings),
            len(rule_checks_run), len(semantic_checks_run),
        )
        return result

    @staticmethod
    def _rule_summary(
        rule_checks_run: list[str],
        rule_warnings: list[ContinuityWarning],
    ) -> str:
        """生成规则检查结果摘要，供语义检查聚焦语义维度。"""
        lines = [f"已执行规则检查: {'、'.join(rule_checks_run)}"]
        if rule_warnings:
            lines.append("规则警告:")
            lines.extend(f"- {w.message}" for w in rule_warnings)
        return "\n".join(lines)
