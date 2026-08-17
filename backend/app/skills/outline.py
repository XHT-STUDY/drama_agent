"""OutlineSkill — 分集大纲生成技能 (C-04).

职责:
- 接收 StoryBible，一次生成完整 N 集分集大纲（按 outline_count）
- 校验集数、编号连续性、四要素完整性
- 检查 required_characters 均在 StoryBible 中存在
- 检查 next_bridge 衔接
- 结构错误可重试；业务弱项写入 validation_notes
- 不写数据库——Artifact 持久化由调用节点/Service 负责

模块边界:
- Skill 只负责组装 Prompt、调用 LLM、校验结果
- 不直接访问 ORM、不操作前端、不决定工作流
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any, cast

from app.agents.base import BaseAgent
from app.domain.outline import EpisodeOutlineSet, OutlineInput
from app.prompts.loader import PromptLoader
from app.skills.protocol import Skill, SkillMetadata

logger = logging.getLogger(__name__)

# ---- 重试配置 (C-04) ----
_MAX_RETRIES = 2  # LLM 结构化输出 / 结构校验失败的最大重试次数


class OutlineValidationError(Exception):
    """Outline 后校验失败——输出不满足质量门禁 (结构错误)。"""


# ========================================================================
# OutlineSkill
# ========================================================================


class OutlineSkill(Skill):
    """分集大纲生成 Skill。

    从 StoryBible 一次生成完整 N 集 EpisodeOutlineSet（按 outline_count）。
    包含集间衔接检查、角色引用检查和四要素完整性检查。
    """

    metadata = SkillMetadata(
        name="outline",
        version="1.1",
        description="从 StoryBible 一次生成完整 N 集分集大纲 (EpisodeOutlineSet，按 outline_count)",
    )

    # ---- 公开 API ----

    async def execute(self, context: dict[str, Any]) -> EpisodeOutlineSet:
        """执行分集大纲生成。

        context 必需键:
            input: OutlineInput — StoryBible + RAG 上下文 + 目标集数
            agent: BaseAgent — 用于调用 LLM
            prompt_loader: PromptLoader — 用于加载 Prompt 模板

        Returns:
            校验通过的 EpisodeOutlineSet

        Raises:
            OutlineValidationError: 结构校验失败 (重试已用尽)
            RuntimeError: LLM 调用失败 (已用尽重试)
        """
        ol_input: OutlineInput = context["input"]
        agent: BaseAgent = context["agent"]
        prompt_loader: PromptLoader = context["prompt_loader"]

        # 1. 加载并渲染 Prompt
        try:
            tpl = prompt_loader.get("outline")
        except KeyError as e:
            logger.error("Prompt 加载失败: %s", e)
            raise

        story_bible_json = _json.dumps(
            ol_input.story_bible, ensure_ascii=False, indent=2
        )

        rendered = tpl.render(
            story_bible=story_bible_json,
            outline_count=str(ol_input.outline_count),
            rag_context=ol_input.rag_context or "(无知识库参考资料)",
        )

        # 2. 调用 LLM (含重试)
        messages: list[dict[str, str]] = [
            {"role": "user", "content": rendered},
        ]

        last_error_detail = ""
        outline_set: EpisodeOutlineSet | None = None
        for attempt in range(1, _MAX_RETRIES + 2):
            result = await agent.generate_structured(
                EpisodeOutlineSet,
                messages,
                prompt_name="outline",
                temperature=0.7,
            )

            if result.error_code or result.parsed is None:
                last_error_detail = (
                    f"code={result.error_code} detail={result.error_detail}"
                )
                logger.warning(
                    "Outline LLM 调用失败 (attempt %d/%d): %s",
                    attempt, _MAX_RETRIES + 1, last_error_detail,
                )
                if attempt <= _MAX_RETRIES:
                    # 重试时附带错误信息
                    messages.append({
                        "role": "system",
                        "content": (
                            f"前一次生成失败: {result.error_detail}。"
                            f"请确保输出为合法的 EpisodeOutlineSet JSON。"
                        ),
                    })
                    continue
                raise RuntimeError(
                    f"Outline LLM 调用失败 (已重试 {_MAX_RETRIES} 次): {last_error_detail}"
                )

            # 结构校验（集数、角色引用、四要素）——失败则带反馈重试
            candidate = cast(EpisodeOutlineSet, result.parsed)
            struct_errors = self._collect_struct_errors(
                candidate, ol_input.story_bible, ol_input.outline_count
            )
            if struct_errors:
                last_error_detail = "\n".join(struct_errors)
                logger.warning(
                    "Outline 结构校验失败 (attempt %d/%d):\n%s",
                    attempt, _MAX_RETRIES + 1, last_error_detail,
                )
                if attempt <= _MAX_RETRIES:
                    # 重试时附带具体结构错误作为反馈
                    messages.append({
                        "role": "system",
                        "content": (
                            "前一次输出的 EpisodeOutlineSet 未通过结构校验:\n"
                            + "\n".join(f"  - {e}" for e in struct_errors)
                            + "\n请修正后重新生成完整的 EpisodeOutlineSet JSON。"
                        ),
                    })
                    continue
                raise OutlineValidationError(
                    f"Outline 结构校验失败 (已重试 {_MAX_RETRIES} 次):\n"
                    + "\n".join(f"  - {e}" for e in struct_errors)
                )

            outline_set = candidate
            break

        assert outline_set is not None, "结构校验通过后必有合法大纲"

        # 3. 软校验（衔接、最后一集小阶段高潮）写入 validation_notes
        self._apply_soft_notes(outline_set)

        return outline_set

    # ---- 内部校验 ----

    def _collect_struct_errors(
        self,
        outline_set: EpisodeOutlineSet,
        story_bible: dict[str, Any],
        expected_count: int,
    ) -> list[str]:
        """收集结构错误（集数、角色引用、四要素）——供重试循环消费。

        与 _apply_soft_notes 分离：结构错误需带反馈重试，
        业务弱项仅对最终接受的对象写入 validation_notes。
        """
        struct_errors: list[str] = []

        # ---- 集数检查 (任务级不变量，期望集数来自 outline_count) ----
        actual = len(outline_set.episodes)
        if actual != expected_count:
            struct_errors.append(
                f"集数不符：需要 {expected_count} 集，实际输出 {actual} 集"
            )

        # ---- 角色引用检查 (结构错误) ----
        struct_errors.extend(outline_set.validate_characters(story_bible))

        # ---- 四要素检查 (结构错误) ----
        for ep in outline_set.episodes:
            if not ep.opening_hook.strip():
                struct_errors.append(f"第 {ep.episode_number} 集 opening_hook 为空")
            if not ep.core_conflict.strip():
                struct_errors.append(f"第 {ep.episode_number} 集 core_conflict 为空")
            if not ep.payoff.strip():
                struct_errors.append(f"第 {ep.episode_number} 集 payoff (爽点) 为空")
            if not ep.ending_hook.strip():
                struct_errors.append(f"第 {ep.episode_number} 集 ending_hook 为空")

        return struct_errors

    def _apply_soft_notes(self, outline_set: EpisodeOutlineSet) -> None:
        """对最终接受的大纲写入软校验备注 (validation_notes)。

        结构错误已在重试循环内拦截；这里只处理不阻断生成的业务弱项。
        """
        # ---- 衔接检查 (业务弱项 → validation_notes) ----
        seq_notes = outline_set.validate_sequence()
        if seq_notes:
            existing = set(outline_set.validation_notes)
            for note in seq_notes:
                if note not in existing:
                    outline_set.validation_notes.append(note)
            logger.info("Outline 衔接提示: %d 条", len(seq_notes))

        # ---- 最后一集小阶段高潮检查 ----
        if outline_set.episodes:
            last = outline_set.episodes[-1]
            arc_summary_lower = outline_set.arc_summary.lower()
            if "大结局" in arc_summary_lower or "全剧终" in arc_summary_lower:
                outline_set.validation_notes.append(
                    f"arc_summary 包含 '大结局'/'全剧终'——"
                    f"第 {last.episode_number} 集应为小阶段高潮而非强制结束"
                )
