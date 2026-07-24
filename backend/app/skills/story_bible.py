"""StoryBibleSkill — 故事设定生成技能 (C-03).

职责:
- 接收 NormalizedRequirement，生成完整的 StoryBible
- 校验 locked_facts、story_rules、open_loops 最小数量
- 校验角色 ID 稳定性、同名角色去重、空目标检查
- 确保主角/反派/配角字段完整性
- 不写数据库——Artifact 持久化由调用节点/Service 负责

模块边界:
- Skill 只负责组装 Prompt、调用 LLM、校验结果
- 不直接访问 ORM、不操作前端、不决定工作流
"""

from __future__ import annotations

import logging
from typing import Any, cast

from app.agents.base import BaseAgent
from app.domain.story_bible import StoryBible, StoryBibleInput
from app.prompts.loader import PromptLoader
from app.skills.protocol import Skill, SkillMetadata

logger = logging.getLogger(__name__)

# ---- 校验阈值 (C-03) ----

_MIN_LOCKED_FACTS = 3      # locked_facts 最少条数
_MIN_STORY_RULES = 3        # story_rules 最少条数
_MIN_OPEN_LOOPS = 1         # open_loops 最少条数
_MIN_SUPPORTING_CHARS = 1   # 至少需要 1 个配角


class StoryBibleValidationError(Exception):
    """StoryBible 后校验失败——输出不满足质量门禁。"""


# ========================================================================
# StoryBibleSkill
# ========================================================================


class StoryBibleSkill(Skill):
    """故事设定 (StoryBible) 生成 Skill。

    从归一化需求生成完整的故事宝典：
    世界观、人物档案、冲突、规则、伏笔和锁定事实。
    """

    metadata = SkillMetadata(
        name="story_bible",
        version="1.0",
        description="从归一化需求生成完整 StoryBible (人物/世界观/规则/伏笔)",
    )

    # ---- 公开 API ----

    async def execute(self, context: dict[str, Any]) -> StoryBible:
        """执行 StoryBible 生成。

        context 必需键:
            input: StoryBibleInput — 归一化需求 + RAG 上下文
            agent: BaseAgent — 用于调用 LLM
            prompt_loader: PromptLoader — 用于加载 Prompt 模板

        Returns:
            校验通过的 StoryBible

        Raises:
            StoryBibleValidationError: 校验不通过
            RuntimeError: LLM 调用失败
        """
        sb_input: StoryBibleInput = context["input"]
        agent: BaseAgent = context["agent"]
        prompt_loader: PromptLoader = context["prompt_loader"]

        # 1. 加载并渲染 Prompt
        try:
            tpl = prompt_loader.get("story_bible")
        except KeyError as e:
            logger.error("Prompt 加载失败: %s", e)
            raise

        # 将 normalized_requirement dict 转为可读 JSON 字符串
        import json as _json
        norm_json = _json.dumps(sb_input.normalized_requirement, ensure_ascii=False, indent=2)

        rendered = tpl.render(
            normalized_requirement=norm_json,
            rag_context=sb_input.rag_context or "(无知识库参考资料)",
        )

        # 2. 调用 LLM 生成结构化输出
        messages: list[dict[str, str]] = [
            {"role": "user", "content": rendered},
        ]

        result = await agent.generate_structured(
            StoryBible,
            messages,
            prompt_name="story_bible",
            temperature=0.7,
        )

        if result.error_code or result.parsed is None:
            logger.error(
                "LLM StoryBible 生成失败: code=%s detail=%s",
                result.error_code,
                result.error_detail,
            )
            raise RuntimeError(
                f"StoryBible LLM 调用失败: {result.error_code} - {result.error_detail}"
            )

        story_bible = cast(StoryBible, result.parsed)

        # 3. 后校验
        self._validate_story_bible(story_bible)

        return story_bible

    # ---- 内部校验 ----

    def _validate_story_bible(self, sb: StoryBible) -> None:
        """对 LLM 输出的 StoryBible 执行质量门禁校验。

        检查项:
        - 角色信息完整性 (主角/反派/至少一个配角)
        - 角色 ID 稳定性 (char_ 前缀)
        - 同名角色和空目标
        - locked_facts / story_rules / open_loops 最小数量
        - 所有角色 character_id 唯一
        """
        errors: list[str] = []

        # ---- 角色完整性 ----
        errors.extend(self._check_character("protagonist", sb.protagonist))
        errors.extend(self._check_character("antagonist", sb.antagonist))

        if len(sb.supporting_characters) < _MIN_SUPPORTING_CHARS:
            errors.append(
                f"配角数量不足: 需要至少 {_MIN_SUPPORTING_CHARS} 个, 实际 {len(sb.supporting_characters)} 个"
            )
        else:
            for i, char in enumerate(sb.supporting_characters):
                errors.extend(self._check_character(f"supporting_characters[{i}]", char))

        # ---- 角色 ID 唯一性 ----
        all_ids = [sb.protagonist.character_id, sb.antagonist.character_id]
        for char in sb.supporting_characters:
            all_ids.append(char.character_id)
        if len(all_ids) != len(set(all_ids)):
            errors.append("角色 character_id 存在重复: 所有角色 ID 必须唯一")

        # ---- 角色 ID 稳定性 ----
        for char in [sb.protagonist, sb.antagonist] + sb.supporting_characters:
            if not char.character_id.startswith("char_"):
                errors.append(
                    f"角色 '{char.name}' 的 character_id='{char.character_id}' "
                    f"不满足稳定命名规则 (应以 'char_' 开头)"
                )

        # ---- 同名角色检查 ----
        all_names = [sb.protagonist.name, sb.antagonist.name]
        for char in sb.supporting_characters:
            all_names.append(char.name)
        name_counts: dict[str, int] = {}
        for n in all_names:
            name_counts[n] = name_counts.get(n, 0) + 1
        duplicates = {n for n, c in name_counts.items() if c > 1}
        if duplicates:
            errors.append(f"存在同名角色: {', '.join(sorted(duplicates))}")

        # ---- 锁定事实 ----
        if len(sb.locked_facts) < _MIN_LOCKED_FACTS:
            errors.append(
                f"locked_facts 数量不足: 需要至少 {_MIN_LOCKED_FACTS} 条, "
                f"实际 {len(sb.locked_facts)} 条"
            )

        # ---- 故事规则 ----
        if len(sb.story_rules) < _MIN_STORY_RULES:
            errors.append(
                f"story_rules 数量不足: 需要至少 {_MIN_STORY_RULES} 条, "
                f"实际 {len(sb.story_rules)} 条"
            )

        # ---- 开放线索 ----
        if len(sb.open_loops) < _MIN_OPEN_LOOPS:
            errors.append(
                f"open_loops 数量不足: 需要至少 {_MIN_OPEN_LOOPS} 条, "
                f"实际 {len(sb.open_loops)} 条"
            )

        # ---- 标题/梗概/世界观 ----
        if not sb.title.strip():
            errors.append("title 为空")
        if not sb.logline.strip():
            errors.append("logline 为空")
        if not sb.world_setting.strip():
            errors.append("world_setting 为空")
        if not sb.main_conflict.strip():
            errors.append("main_conflict 为空")
        if not sb.stakes.strip():
            errors.append("stakes 为空")

        if errors:
            msg = "StoryBible 校验失败:\n" + "\n".join(f"  - {e}" for e in errors)
            logger.error(msg)
            raise StoryBibleValidationError(msg)

    @staticmethod
    def _check_character(label: str, char: Any) -> list[str]:
        """校验单个角色档案的字段完整性。

        Args:
            label: 角色标签 (用于错误消息)
            char: CharacterProfile 实例

        Returns:
            错误消息列表 (空列表表示通过)
        """
        errors: list[str] = []
        if not char.name.strip():
            errors.append(f"{label}.name 为空")
        if not char.role.strip():
            errors.append(f"{label}.role 为空")
        if not char.visible_goal.strip():
            errors.append(f"{label}.visible_goal 为空 (不能有空洞目标)")
        if not char.traits:
            errors.append(f"{label}.traits 为空 (至少需要一个性格特质)")
        if not char.strengths:
            errors.append(f"{label}.strengths 为空 (至少需要一个优势)")
        if not char.flaws:
            errors.append(f"{label}.flaws 为空 (至少需要一个缺陷)")
        return errors
