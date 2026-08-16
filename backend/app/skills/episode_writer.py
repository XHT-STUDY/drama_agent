"""EpisodeWriterSkill — 单集剧本写作技能 (C-05).

职责:
- 接收单集 Outline、StoryBible、前集摘要、连续性状态
- 调用 LLM 生成 ScriptDraft (Scene/DialogueLine + plain_text)
- 使用 WordCountTool/DialogueRatioTool 覆盖 LLM 自报指标
- 按 1→2→3 集顺序生成 (每集独立 ScriptDraft Artifact)
- 校验 Scene 编号连续性、角色可追溯、ending_hook 对应
- 不写数据库——Artifact 持久化由调用节点/Service 负责

模块边界:
- Skill 只负责组装 Prompt、调用 LLM、工具计算、校验结果
- 不直接访问 ORM、不操作前端
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any, cast
from uuid import UUID

from app.agents.base import BaseAgent
from app.domain.script import EpisodeWriterInput, ScriptDraft
from app.prompts.loader import PromptLoader
from app.skills.protocol import Skill, SkillMetadata
from app.tools.dialogue_ratio import DialogueRatioTool
from app.tools.word_count import WordCountTool

logger = logging.getLogger(__name__)

# 对白比例告警阈值 (C-05)
_DIALOGUE_RATIO_LOW_WARN = 0.15   # 低于此值 → 动作描写过多
_DIALOGUE_RATIO_HIGH_WARN = 0.80  # 高于此值 → 台词过于密集


class EpisodeWriterValidationError(Exception):
    """Episode Writer 后校验失败——输出不满足质量门禁。"""


# ========================================================================
# EpisodeWriterSkill
# ========================================================================


class EpisodeWriterSkill(Skill):
    """单集剧本写作 Skill。

    从单集大纲生成完整剧本草稿 (Scene/DialogueLine + plain_text)。
    使用确定性工具覆盖 LLM 自报的 word_count 和 dialogue_ratio。
    """

    metadata = SkillMetadata(
        name="write_episode",
        version="1.0",
        description="从单集大纲生成完整剧本 (Scene/DialogueLine/plain_text)",
    )

    def __init__(self) -> None:
        super().__init__()
        self._word_counter = WordCountTool()
        self._dialogue_calc = DialogueRatioTool()

    # ---- 公开 API ----

    async def execute(self, context: dict[str, Any]) -> ScriptDraft:
        """执行单集剧本写作。

        context 必需键:
            input: EpisodeWriterInput — 大纲/StoryBible/摘要/连续性
            agent: BaseAgent — 用于调用 LLM
            prompt_loader: PromptLoader — 用于加载 Prompt 模板
            outline_artifact_id: UUID — 关联的分集大纲 Artifact ID

        Returns:
            校验通过且指标已覆盖的 ScriptDraft

        Raises:
            EpisodeWriterValidationError: 结构校验失败
            RuntimeError: LLM 调用失败
        """
        ew_input: EpisodeWriterInput = context["input"]
        agent: BaseAgent = context["agent"]
        prompt_loader: PromptLoader = context["prompt_loader"]
        outline_artifact_id: UUID = context["outline_artifact_id"]

        # 1. 加载并渲染 Prompt
        try:
            tpl = prompt_loader.get("write_episode")
        except KeyError as e:
            logger.error("Prompt 加载失败: %s", e)
            raise

        outline_json = _json.dumps(ew_input.episode_outline, ensure_ascii=False, indent=2)
        story_bible_json = _json.dumps(ew_input.story_bible, ensure_ascii=False, indent=2)

        # G-02: write_episode 节点注入 assembled_context（ContextBuilder 组装）。
        # 兼容旧调用方（无 assembled_context）——回退分段拼装，保证 Skill 独立可用。
        if ew_input.assembled_context:
            assembled = ew_input.assembled_context
        else:
            assembled = "\n\n".join(
                [
                    f"## 本集大纲\n{outline_json}",
                    f"## 前集摘要\n{ew_input.previous_summary or '(第1集无前集)'}",
                    f"## 连续性状态\n{ew_input.continuity_state or '(初始状态)'}",
                    f"## StoryBible 参考\n{story_bible_json}",
                    f"## 知识库参考\n{ew_input.rag_context or '(无知识库参考资料)'}",
                ]
            )

        rendered = tpl.render(
            episode_number=str(ew_input.episode_number),
            assembled_context=assembled,
        )

        # 2. 调用 LLM 生成结构化输出
        messages: list[dict[str, str]] = [
            {"role": "user", "content": rendered},
        ]

        result = await agent.generate_structured(
            ScriptDraft,
            messages,
            prompt_name="write_episode",
            temperature=0.8,
        )

        if result.error_code or result.parsed is None:
            logger.error(
                "LLM 剧本写作失败: code=%s detail=%s",
                result.error_code,
                result.error_detail,
            )
            raise RuntimeError(
                f"Episode Writer LLM 调用失败: {result.error_code} - {result.error_detail}"
            )

        draft = cast(ScriptDraft, result.parsed)

        # 3. 覆盖 LLM 自报指标 (验收项 1)
        await self._override_metrics(draft)

        # 4. 后校验
        self._validate_draft(draft, ew_input)

        # 5. 设置关联 Artifact ID
        draft.referenced_outline_artifact_id = outline_artifact_id

        return draft

    # ---- 指标覆盖 ----

    async def _override_metrics(self, draft: ScriptDraft) -> None:
        """使用确定性工具计算并覆盖 LLM 自报的指标。

        word_count: 使用 WordCountTool 统计 plain_text 中文+标点数
        dialogue_ratio: 使用 DialogueRatioTool 计算 scenes 中对白占比

        仅记录告警，不因轻微越界直接失败。
        """
        # 字数覆盖
        wc_result = await self._word_counter.execute(plain_text=draft.plain_text)
        computed_wc = int(wc_result.get("chinese_chars_with_punct", 0))
        if draft.word_count != computed_wc:
            logger.info(
                "第 %d 集 word_count 覆盖: LLM=%d → Tool=%d",
                draft.episode_number, draft.word_count, computed_wc,
            )
            draft.word_count = computed_wc

        # 对白比例覆盖
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

        # 对白比例告警 (不阻断)
        if computed_ratio < _DIALOGUE_RATIO_LOW_WARN:
            logger.warning(
                "第 %d 集对白比例过低 (%.1f%%), 动作描写可能过多",
                draft.episode_number, computed_ratio * 100,
            )
        elif computed_ratio > _DIALOGUE_RATIO_HIGH_WARN:
            logger.warning(
                "第 %d 集对白比例过高 (%.1f%%), 台词可能过于密集",
                draft.episode_number, computed_ratio * 100,
            )

    # ---- 后校验 ----

    def _validate_draft(
        self,
        draft: ScriptDraft,
        ew_input: EpisodeWriterInput,
    ) -> None:
        """对 LLM 输出的 ScriptDraft 执行质量校验。

        检查项:
        - Scene 编号连续 (Pydantic 已处理)
        - Scene 数量 >= 2 (Pydantic 已处理)
        - ending_hook 与 Outline 对应
        - 角色名可追溯到 StoryBible (或为临时群众角色)
        - 第 2+ 集使用摘要而非全文 (context 传入检查)
        """
        errors: list[str] = []

        # ---- Scene 数量 ----
        if len(draft.scenes) < 2:
            errors.append(f"第 {draft.episode_number} 集场景数不足: {len(draft.scenes)} < 2")

        # ---- ending_hook 对应 (验收项 4) ----
        outline_ending = ew_input.episode_outline.get("ending_hook", "")
        if outline_ending and outline_ending.strip():
            # 检查剧本的 ending_hook 是否与大纲的 ending_hook 在主题上呼应
            # (至少有一个关键词重叠则表示有承接；无重叠则写入弱提示，不阻断)
            outline_keywords = _extract_keywords(outline_ending)
            draft_keywords = _extract_keywords(draft.ending_hook)
            if outline_keywords and draft_keywords:
                overlap = outline_keywords & draft_keywords
                if not overlap:
                    logger.warning(
                        "第 %d 集 ending_hook 与大纲无关键词重叠 (非阻断)",
                        draft.episode_number,
                    )
        if not draft.ending_hook.strip():
            errors.append(f"第 {draft.episode_number} 集 ending_hook 为空")

        # ---- 角色可追溯（警告，不阻断） ----
        known_names = _collect_character_names(ew_input.story_bible)
        for scene in draft.scenes:
            for char_name in scene.characters:
                if char_name not in known_names:
                    # 检查是否为常见群众角色（纯描述性名称，非专有名词）
                    # LLM 生成的临时角色名无法穷举，此处仅做信息记录
                    logger.info(
                        "第 %d 集 Scene %d 引入新角色 '%s'（不在 StoryBible 中）",
                        draft.episode_number, scene.scene_number, char_name,
                    )

        if errors:
            msg = f"Episode Writer 校验失败 (第 {draft.episode_number} 集):\n" + "\n".join(
                f"  - {e}" for e in errors
            )
            logger.error(msg)
            raise EpisodeWriterValidationError(msg)


# ========================================================================
# 辅助函数
# ========================================================================


def _collect_character_names(story_bible: dict[str, Any]) -> set[str]:
    """从 StoryBible dict 中收集所有角色名。

    Args:
        story_bible: StoryBible 的 dict 表示

    Returns:
        角色名集合
    """
    names: set[str] = set()
    for key in ("protagonist", "antagonist"):
        char = story_bible.get(key)
        if char and isinstance(char, dict):
            name = char.get("name", "")
            if name:
                names.add(name)
    for char in story_bible.get("supporting_characters", []) or []:
        if isinstance(char, dict):
            name = char.get("name", "")
            if name:
                names.add(name)
    return names


def _extract_keywords(text: str) -> set[str]:
    """从文本中提取关键词 (2+ 字的中文词)。

    用于比较大纲 ending_hook 与剧本 ending_hook 的主题重叠。
    """
    import re as _re
    # 提取连续中文字符 (2字及以上)
    words = _re.findall(r"[一-鿿]{2,}", text)
    return set(words)
