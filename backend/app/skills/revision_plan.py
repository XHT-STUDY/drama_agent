"""RevisionPlanSkill — 修订计划生成技能 (F-01).

职责:
- 接收选中的评估报告、原稿剧本与锁定事实 (RevisionPlanInput)
- 调用 LLM 生成 RevisionPlan（operations 绑定 issue_ids、目标场景与 preserve）
- 后校验:只保留有据可依的 operation（无来源 issue 的空泛任务剔除）
- LLM 计划全部失实时的确定性兜底（operations_from_issues）
- 权威字段覆盖:episode_number / source ids / locked_facts / max_change_ratio
  由服务端决定，不信任 LLM 自报
- scene_number 超范围降级为 null

模块边界:
- Skill 只负责组装 Prompt、调用 LLM、后校验与兜底
- 不直接访问 ORM、不操作前端
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any, cast

from app.agents.base import BaseAgent
from app.domain.revision import (
    RevisionOperation,
    RevisionPlan,
    RevisionPlanInput,
    filter_grounded_operations,
    operations_from_issues,
)
from app.prompts.loader import PromptLoader
from app.skills.protocol import Skill, SkillMetadata

logger = logging.getLogger(__name__)


class RevisionPlanValidationError(Exception):
    """修订计划后校验失败——无法生成任何有据可依的修订操作。"""


class RevisionPlanSkill(Skill):
    """修订计划生成 Skill。

    从评估问题生成有据可依的修订计划：
    每个 operation 必须引用评估报告中的 issue_id，
    锁定事实与来源 Artifact ID 由服务端权威填充。
    """

    metadata = SkillMetadata(
        name="revision_plan",
        version="1.0",
        description="从评估问题生成有据可依的修订计划（绑定 issue、目标场景与 preserve）",
    )

    # ---- 公开 API ----

    async def execute(self, context: dict[str, Any]) -> RevisionPlan:
        """执行修订计划生成。

        context 必需键:
            input: RevisionPlanInput — 报告/原稿/锁定事实/来源 ID
            agent: BaseAgent — 用于调用 LLM
            prompt_loader: PromptLoader — 用于加载 Prompt 模板

        Returns:
            已通过有据可依校验、权威字段已覆盖的 RevisionPlan

        Raises:
            RuntimeError: LLM 调用失败
            RevisionPlanValidationError: 无法生成任何有据可依的修订操作
        """
        plan_input: RevisionPlanInput = context["input"]
        agent: BaseAgent = context["agent"]
        prompt_loader: PromptLoader = context["prompt_loader"]

        # 1. 渲染 Prompt
        try:
            tpl = prompt_loader.get("revision_plan")
        except KeyError as e:
            logger.error("Prompt 加载失败: %s", e)
            raise

        rendered = tpl.render(
            episode_number=str(plan_input.episode_number),
            script_draft=_json.dumps(
                plan_input.script_draft.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
            evaluation_report=_json.dumps(
                plan_input.evaluation_report.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
            locked_facts=_json.dumps(plan_input.locked_facts, ensure_ascii=False, indent=2),
            max_change_ratio=str(plan_input.max_change_ratio),
            # loader render() 严格：必须恒提供模板变量；无用户要求时占位
            user_instruction=str(plan_input.user_instruction or "（无）"),
        )

        # 2. 调用 LLM 生成结构化计划
        messages: list[dict[str, str]] = [
            {"role": "user", "content": rendered},
        ]
        result = await agent.generate_structured(
            RevisionPlan,
            messages,
            prompt_name="revision_plan",
            temperature=0.3,
        )

        if result.error_code or result.parsed is None:
            logger.error(
                "修订计划 LLM 调用失败: code=%s detail=%s",
                result.error_code,
                result.error_detail,
            )
            raise RuntimeError(
                f"RevisionPlan Skill LLM 调用失败: {result.error_code} - {result.error_detail}"
            )

        llm_plan = cast(RevisionPlan, result.parsed)

        # 3. 有据可依校验:剔除无来源 issue 的空泛任务
        grounded = filter_grounded_operations(
            llm_plan.operations, plan_input.evaluation_report
        )
        if not grounded:
            # LLM 计划全部失实 → 确定性兜底（从 issue 生成，保证有据可依）
            logger.warning(
                "第 %d 集 LLM 计划全部失实（无 issue 依据），回退为确定性生成",
                plan_input.episode_number,
            )
            grounded = operations_from_issues(
                plan_input.evaluation_report.issues,
                locked_facts=plan_input.locked_facts,
            )

        if not grounded:
            raise RevisionPlanValidationError(
                f"第 {plan_input.episode_number} 集无法生成有据可依的修订操作:"
                "评估报告不含任何 issue"
            )

        # 4. 场景号软校验:超范围降级为 null
        grounded = self._clamp_scene_numbers(grounded, plan_input.script_draft)

        # 5. 锁定事实并入每个 operation 的 preserve（硬约束兜底）
        grounded = self._merge_locked_facts_into_preserve(grounded, plan_input.locked_facts)

        # 6. 权威字段覆盖（不信任 LLM 自报）
        plan = RevisionPlan(
            episode_number=plan_input.episode_number,
            source_script_artifact_id=plan_input.source_script_artifact_id,
            source_evaluation_artifact_id=plan_input.source_evaluation_artifact_id,
            operations=grounded,
            locked_facts=list(plan_input.locked_facts),
            max_change_ratio=plan_input.max_change_ratio,
            user_instruction=plan_input.user_instruction,
        )

        logger.info(
            "第 %d 集修订计划生成: operations=%d locked_facts=%d",
            plan.episode_number,
            len(plan.operations),
            len(plan.locked_facts),
        )
        return plan

    # ---- 后校验辅助 ----

    def _merge_locked_facts_into_preserve(
        self,
        operations: list[RevisionOperation],
        locked_facts: list[str],
    ) -> list[RevisionOperation]:
        """将锁定事实并入每个 operation 的 preserve（去重、保序）。

        锁定事实是修订的硬约束——即使 LLM 未在 preserve 中列出，
        也要兜底保证修订操作不会破坏它们。
        """
        if not locked_facts:
            return operations
        merged: list[RevisionOperation] = []
        for op in operations:
            preserve = list(dict.fromkeys(list(locked_facts) + list(op.preserve)))
            if preserve != list(op.preserve):
                merged.append(op.model_copy(update={"preserve": preserve}))
            else:
                merged.append(op)
        return merged

    def _clamp_scene_numbers(
        self,
        operations: list[RevisionOperation],
        script_draft: Any,
    ) -> list[RevisionOperation]:
        """scene_number 超出现有场景范围时降级为 null（软校验，不阻断）。"""
        max_scene = max(
            (s.scene_number for s in script_draft.scenes), default=0
        )
        updated: list[RevisionOperation] = []
        for op in operations:
            if op.target_scene_number is not None and op.target_scene_number > max_scene:
                logger.warning(
                    "operation %s 的 scene_number=%d 超出范围 (%d)，降级为 null",
                    op.operation_id, op.target_scene_number, max_scene,
                )
                updated.append(op.model_copy(update={"target_scene_number": None}))
            else:
                updated.append(op)
        return updated
