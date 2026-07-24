"""OutlineSkill — 分集大纲生成技能 (C-04).

职责:
- 接收 StoryBible，一次生成完整 10 集分集大纲
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
_MAX_RETRIES = 2  # 结构化输出验证失败的最大重试次数


class OutlineValidationError(Exception):
    """Outline 后校验失败——输出不满足质量门禁 (结构错误)。"""


# ========================================================================
# OutlineSkill
# ========================================================================


class OutlineSkill(Skill):
    """分集大纲生成 Skill。

    从 StoryBible 一次生成完整的 10 集 EpisodeOutlineSet。
    包含集间衔接检查、角色引用检查和四要素完整性检查。
    """

    metadata = SkillMetadata(
        name="outline",
        version="1.0",
        description="从 StoryBible 一次生成完整 10 集分集大纲 (EpisodeOutlineSet)",
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
            OutlineValidationError: 结构校验失败 (可重试)
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

            outline_set = cast(EpisodeOutlineSet, result.parsed)
            break

        # 3. 后校验
        self._validate_outline(outline_set, ol_input.story_bible)

        return outline_set

    # ---- 内部校验 ----

    def _validate_outline(
        self,
        outline_set: EpisodeOutlineSet,
        story_bible: dict[str, Any],
    ) -> None:
        """对 LLM 输出的 EpisodeOutlineSet 执行质量门禁校验。

        结构错误 (需重试):
        - 集数不是 10 → Pydantic 已捕获
        - 集号不连续 → Pydantic 已捕获
        - 角色引用不存在 → 收集错误

        业务弱项 (写入 validation_notes):
        - next_bridge 衔接不完整
        - 第 10 集强制大结局关键词
        """
        struct_errors: list[str] = []

        # ---- 角色引用检查 (结构错误) ----
        char_errors = outline_set.validate_characters(story_bible)
        if char_errors:
            struct_errors.extend(char_errors)

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

        if struct_errors:
            msg = "Outline 结构校验失败:\n" + "\n".join(f"  - {e}" for e in struct_errors)
            logger.error(msg)
            raise OutlineValidationError(msg)

        # ---- 衔接检查 (业务弱项 → validation_notes) ----
        seq_notes = outline_set.validate_sequence()
        if seq_notes:
            existing = set(outline_set.validation_notes)
            for note in seq_notes:
                if note not in existing:
                    outline_set.validation_notes.append(note)
            logger.info("Outline 衔接提示: %d 条", len(seq_notes))

        # ---- 第 10 集小阶段高潮检查 ----
        if len(outline_set.episodes) >= 10:
            ep10_obj = outline_set.episodes[9]
            ep10_obj.objective = ep10_obj.objective  # no-op: ensure access works
            arc_summary_lower = outline_set.arc_summary.lower()
            if "大结局" in arc_summary_lower or "全剧终" in arc_summary_lower:
                outline_set.validation_notes.append(
                    "arc_summary 包含 '大结局'/'全剧终'——第 10 集应为小阶段高潮而非强制结束"
                )
