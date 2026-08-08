"""ReviserSkill — 单集剧本修订技能 (F-02).

职责:
- 接收原稿、修订计划、StoryBible、当前集大纲与连续性状态 (RevisionTaskInput)
- 在模型输入中显式列出 preserve 与禁止修改项（锁定事实 / 各 operation preserve / 角色 forbidden_changes）
- 调用 LLM 生成 RevisionResult：**完整新稿**（不输出原地 patch）+ 每个 operation 的执行记录
- 权威字段覆盖: episode_number / title / referenced_outline_artifact_id / source_* 由服务端决定，
  不信任 LLM 自报
- 服务端重新计算文本指标（word_count / dialogue_ratio），覆盖 LLM 自估值
- 执行记录规范化: 剔除臆造、补齐缺失、按计划顺序输出（每个 operation 有执行结果或未执行说明）

模块边界:
- Skill 只负责组装 Prompt、调用 LLM、工具计算、后校验
- 不直接访问 ORM、不操作前端
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any, cast

from app.agents.base import BaseAgent
from app.domain.revision import (
    OperationExecution,
    RevisionResult,
    RevisionTaskInput,
    normalize_executions,
)
from app.domain.script import ScriptDraft
from app.prompts.loader import PromptLoader
from app.skills.protocol import Skill, SkillMetadata
from app.tools.dialogue_ratio import DialogueRatioTool
from app.tools.word_count import WordCountTool

logger = logging.getLogger(__name__)

# 对白比例告警阈值（与 C-05 一致）
_DIALOGUE_RATIO_LOW_WARN = 0.15
_DIALOGUE_RATIO_HIGH_WARN = 0.80


class ReviserValidationError(Exception):
    """Reviser 后校验失败——输出不满足质量门禁。"""


class ReviserSkill(Skill):
    """单集剧本修订 Skill。

    按修订计划对原稿进行局部改写，输出完整新 ScriptDraft 与
    每个 operation 的执行情况。episode_number / title / source ids
    由服务端权威覆盖，文本指标由确定性工具重算。
    """

    metadata = SkillMetadata(
        name="revise_episode",
        version="1.0",
        description="按修订计划局部改写单集剧本，输出完整新稿与 operation 执行记录",
    )

    def __init__(self) -> None:
        super().__init__()
        self._word_counter = WordCountTool()
        self._dialogue_calc = DialogueRatioTool()

    # ---- 公开 API ----

    async def execute(self, context: dict[str, Any]) -> RevisionResult:
        """执行单集剧本修订。

        context 必需键:
            input: RevisionTaskInput — 原稿/计划/StoryBible/大纲/连续性状态
            agent: BaseAgent — 用于调用 LLM
            prompt_loader: PromptLoader — 用于加载 Prompt 模板

        Returns:
            权威字段已覆盖、指标已重算、执行记录全覆盖的 RevisionResult

        Raises:
            RuntimeError: LLM 调用失败
            ReviserValidationError: 修订结果结构校验失败
        """
        task_input: RevisionTaskInput = context["input"]
        agent: BaseAgent = context["agent"]
        prompt_loader: PromptLoader = context["prompt_loader"]

        # 1. 渲染 Prompt（显式列出 preserve 与禁止修改项）
        try:
            tpl = prompt_loader.get("revise_episode")
        except KeyError as e:
            logger.error("Prompt 加载失败: %s", e)
            raise

        rendered = tpl.render(
            episode_number=str(task_input.episode_number),
            script_draft=_json.dumps(
                task_input.script_draft.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
            revision_plan=_json.dumps(
                task_input.revision_plan.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
            protection_block=_build_protection_block(task_input),
            episode_outline=_json.dumps(
                task_input.episode_outline, ensure_ascii=False, indent=2
            ),
            story_bible=_json.dumps(
                task_input.story_bible, ensure_ascii=False, indent=2
            ),
            continuity_state=task_input.continuity_state or "(初始状态)",
        )

        # 2. 调用 LLM 生成完整新稿 + 执行记录
        messages: list[dict[str, str]] = [
            {"role": "user", "content": rendered},
        ]
        result = await agent.generate_structured(
            RevisionResult,
            messages,
            prompt_name="revise_episode",
            temperature=0.4,
        )

        if result.error_code or result.parsed is None:
            logger.error(
                "LLM 修订失败: code=%s detail=%s",
                result.error_code,
                result.error_detail,
            )
            raise RuntimeError(
                f"Reviser Skill LLM 调用失败: {result.error_code} - {result.error_detail}"
            )

        rev_result = cast(RevisionResult, result.parsed)

        # 3. 权威字段覆盖（不信任 LLM 自报）+ 服务端重算文本指标
        draft = rev_result.script_draft
        draft.episode_number = task_input.episode_number
        draft.title = task_input.script_draft.title
        draft.referenced_outline_artifact_id = (
            task_input.script_draft.referenced_outline_artifact_id
        )
        await self._override_metrics(draft)

        # 4. 执行记录规范化: 剔除臆造 / 补齐缺失 / 按计划顺序全覆盖
        executions = normalize_executions(
            task_input.revision_plan.operations,
            rev_result.operation_executions,
        )
        self._log_execution_summary(task_input, executions)

        # 5. 构建权威结果（source_* 来自计划与输入，不信任 LLM）
        final = RevisionResult(
            script_draft=draft,
            operation_executions=executions,
            source_script_artifact_id=task_input.revision_plan.source_script_artifact_id,
            source_evaluation_artifact_id=task_input.revision_plan.source_evaluation_artifact_id,
            source_revision_plan_artifact_id=task_input.source_revision_plan_artifact_id,
        )

        logger.info(
            "第 %d 集修订完成: applied=%d partial=%d skipped=%d",
            final.script_draft.episode_number,
            sum(1 for e in executions if e.status == "applied"),
            sum(1 for e in executions if e.status == "partial"),
            sum(1 for e in executions if e.status == "skipped"),
        )
        return final

    # ---- 文本指标重算（服务端确定性覆盖） ----

    async def _override_metrics(self, draft: ScriptDraft) -> None:
        """使用确定性工具重算并覆盖 LLM 自报的 word_count / dialogue_ratio。

        Args:
            draft: 修订后的剧本（原地覆盖指标）
        """
        wc_result = await self._word_counter.execute(plain_text=draft.plain_text)
        computed_wc = int(wc_result.get("chinese_chars_with_punct", 0))
        if draft.word_count != computed_wc:
            logger.info(
                "第 %d 集 word_count 覆盖: LLM=%d → Tool=%d",
                draft.episode_number, draft.word_count, computed_wc,
            )
            draft.word_count = computed_wc

        scenes_raw = [s.model_dump() for s in draft.scenes]
        dr_result = await self._dialogue_calc.execute(
            scenes=scenes_raw, plain_text=draft.plain_text,
        )
        computed_ratio = float(dr_result.get("dialogue_ratio", 0.0))
        if abs(draft.dialogue_ratio - computed_ratio) > 0.001:
            logger.info(
                "第 %d 集 dialogue_ratio 覆盖: LLM=%.3f → Tool=%.3f",
                draft.episode_number, draft.dialogue_ratio, computed_ratio,
            )
            draft.dialogue_ratio = computed_ratio

        if computed_ratio < _DIALOGUE_RATIO_LOW_WARN:
            logger.warning(
                "第 %d 集修订后对白比例过低 (%.1f%%), 动作描写可能过多",
                draft.episode_number, computed_ratio * 100,
            )
        elif computed_ratio > _DIALOGUE_RATIO_HIGH_WARN:
            logger.warning(
                "第 %d 集修订后对白比例过高 (%.1f%%), 台词可能过于密集",
                draft.episode_number, computed_ratio * 100,
            )

    # ---- 辅助 ----

    @staticmethod
    def _log_execution_summary(
        task_input: RevisionTaskInput,
        executions: list[OperationExecution],
    ) -> None:
        """记录执行覆盖情况；全部未执行时给出警告。"""
        if not executions:
            logger.warning(
                "第 %d 集修订计划为空，无执行记录", task_input.episode_number
            )
            return
        applied = [e.operation_id for e in executions if e.status == "applied"]
        partial = [e.operation_id for e in executions if e.status == "partial"]
        skipped = [e.operation_id for e in executions if e.status == "skipped"]
        logger.info(
            "第 %d 集 operation 覆盖: applied=%s partial=%s skipped=%s",
            task_input.episode_number, applied, partial, skipped,
        )
        if not applied and not partial:
            logger.warning(
                "第 %d 集全部 operation 未执行 (skipped=%s)，修订可能为空操作",
                task_input.episode_number, skipped,
            )


# ========================================================================
# 辅助函数
# ========================================================================


def _build_protection_block(task_input: RevisionTaskInput) -> str:
    """汇总 preserve 与禁止修改项，供 Prompt 显式列出（F-02 验收项）。

    包括:
    1. 本集标识（episode_number / title，禁止修改）;
    2. 计划中的锁定事实（禁止修改或违反）;
    3. 各 operation 的 preserve（修订时必须保留）;
    4. StoryBible 中角色的 forbidden_changes（禁止修改项）。

    Args:
        task_input: 修订任务输入。

    Returns:
        可直接注入 Prompt 的中文文本块。
    """
    lines: list[str] = []
    lines.append("### 1. 本集标识（禁止修改）")
    lines.append(f"- `episode_number` = {task_input.episode_number}")
    lines.append(f"- `title` = {task_input.script_draft.title}")
    lines.append("")

    lines.append("### 2. 锁定事实（禁止修改或违反）")
    locked = task_input.revision_plan.locked_facts
    if locked:
        lines.extend(f"- {fact}" for fact in locked)
    else:
        lines.append("- （无）")
    lines.append("")

    lines.append("### 3. 各 operation 的 preserve（修订时必须保留）")
    pres_present = False
    for op in task_input.revision_plan.operations:
        if op.preserve:
            pres_present = True
            lines.append(f"- operation `{op.operation_id}` 保留：{'；'.join(op.preserve)}")
    if not pres_present:
        lines.append("- （无）")
    lines.append("")

    lines.append("### 4. 角色禁止修改项（StoryBible.CharacterProfile.forbidden_changes）")
    profiles = _character_profiles(task_input.story_bible)
    forb_present = False
    for ch in profiles:
        forbidden = ch.get("forbidden_changes") or []
        if forbidden:
            forb_present = True
            name = ch.get("name") or ch.get("character_id") or "?"
            lines.append(f"- {name}：{'；'.join(forbidden)}")
    if not forb_present:
        lines.append("- （无）")

    return "\n".join(lines)


def _character_profiles(story_bible: dict[str, Any]) -> list[dict[str, Any]]:
    """从 StoryBible dict 收集全部角色档案（主角/反派/配角）。"""
    profiles: list[dict[str, Any]] = []
    for key in ("protagonist", "antagonist"):
        ch = story_bible.get(key)
        if ch and isinstance(ch, dict):
            profiles.append(ch)
    supporting = story_bible.get("supporting_characters") or []
    profiles.extend(ch for ch in supporting if isinstance(ch, dict))
    return profiles
