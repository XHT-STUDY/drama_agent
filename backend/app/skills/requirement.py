"""RequirementSkill — 需求归一化技能 (C-02).

职责:
- 接收用户的 Idea / Outline / TXT / DOCX 输入
- 校验关键信息 (主角设定、核心冲突) 是否缺失
- 关键信息缺失时返回 NeedsUserInput，不让 LLM 猜测
- 非关键缺省由 LLM 生成 assumptions
- 不写数据库——Artifact 持久化由调用节点/Service 负责

模块边界:
- Skill 只负责组装 Prompt、调用 LLM、校验结果
- 不直接访问 ORM、不操作前端、不决定工作流
"""

from __future__ import annotations

import logging
from typing import Any, cast

from app.agents.base import BaseAgent
from app.domain.requirement import NeedsUserInput, NormalizedRequirement, RequirementInput
from app.prompts.loader import PromptLoader
from app.skills.protocol import Skill, SkillMetadata

logger = logging.getLogger(__name__)

# ---- 主角与冲突检测关键词 (C-02) ----
# 用于在调用 LLM 前快速判断用户输入是否包含足够的
# 主角信息与核心冲突信息。缺失任一类键词 → NeedsUserInput。

_PROTAGONIST_KEYWORDS: list[str] = [
    "主角", "少年", "少女", "男主", "女主", "总裁", "王妃", "千金",
    "高手", "兵王", "神医", "保镖", "穿越者", "重生者", "废柴", "天才",
    "修仙", "武者", "特工", "杀手", "医仙", "厨神", "战神", "龙王",
    "赘婿", "庶女", "嫡女", "世子", "公主", "太子", "将军",
]

_CONFLICT_KEYWORDS: list[str] = [
    "逆袭", "复仇", "冲突", "对抗", "打压", "抛弃", "追杀", "争夺",
    "拯救", "翻身", "逆天", "重振", "崛起", "打脸", "碾压",
    "觉醒", "翻身", "复仇", "逃亡", "卧底", "潜伏", "挑战",
    "碾压", "逆天改命", "扮猪吃虎", "隐藏身份", "被退婚", "被废",
    "重生", "穿越",
]

# 最小输入长度 (字符) — 低于此阈值直接判定信息不足
_MIN_INPUT_LENGTH = 8


def _has_protagonist_info(user_input: str) -> bool:
    """检测用户输入中是否包含主角相关信息。

    使用关键词匹配：至少命中一个主角类关键词。
    对于含有人名或角色描述的输入，通常都能匹配。
    """
    return any(kw in user_input for kw in _PROTAGONIST_KEYWORDS)


def _has_conflict_info(user_input: str) -> bool:
    """检测用户输入中是否包含核心冲突相关信息。

    使用关键词匹配：至少命中一个冲突类关键词。
    """
    return any(kw in user_input for kw in _CONFLICT_KEYWORDS)


# ========================================================================
# RequirementSkill
# ========================================================================


class RequirementSkill(Skill):
    """需求归一化 Skill。

    将用户原始输入归一化为 NormalizedRequirement 结构化对象。
    关键信息缺失时返回 NeedsUserInput。
    """

    metadata = SkillMetadata(
        name="normalize_requirement",
        version="1.0",
        description="将用户 Idea/Outline/TXT/DOCX 输入归一化为结构化创作需求",
    )

    # ---- 公开 API ----

    async def execute(self, context: dict[str, Any]) -> NormalizedRequirement | NeedsUserInput:
        """执行需求归一化。

        context 必需键:
            input: RequirementInput — 用户输入
            agent: BaseAgent — 用于调用 LLM
            prompt_loader: PromptLoader — 用于加载 Prompt 模板

        Returns:
            NormalizedRequirement — 归一化成功
            NeedsUserInput — 关键信息缺失, 需用户补充
        """
        req_input: RequirementInput = context["input"]
        agent: BaseAgent = context["agent"]
        prompt_loader: PromptLoader = context["prompt_loader"]

        # 1. 校验关键信息
        needs = self._check_critical_fields(req_input)
        if needs is not None:
            logger.info(
                "需求归一化阻断: 缺少关键字段 %s",
                needs.missing_fields,
            )
            return needs

        # 2. 加载并渲染 Prompt
        try:
            tpl = prompt_loader.get("normalize_requirement")
        except KeyError as e:
            logger.error("Prompt 加载失败: %s", e)
            raise

        rendered = tpl.render(
            user_input=req_input.user_input,
            target_episode_count=str(req_input.target_episode_count),
            episode_duration_seconds=str(req_input.episode_duration_seconds),
        )

        # 3. 调用 LLM 生成结构化输出
        messages: list[dict[str, str]] = [
            {"role": "user", "content": rendered},
        ]

        result = await agent.generate_structured(
            NormalizedRequirement,
            messages,
            prompt_name="normalize_requirement",
            temperature=0.5,
        )

        if result.error_code or result.parsed is None:
            logger.error(
                "LLM 归一化失败: code=%s detail=%s",
                result.error_code,
                result.error_detail,
            )
            raise RuntimeError(
                f"需求归一化 LLM 调用失败: {result.error_code} - {result.error_detail}"
            )

        normalized = cast(NormalizedRequirement, result.parsed)

        # 4. 后校验: LLM 输出的关键字段不能为空
        if not normalized.protagonist_seed.strip():
            return NeedsUserInput(
                missing_fields=["protagonist_seed"],
                current_understanding=f"用户描述了 '{req_input.user_input[:100]}...' 但未明确主角",
                questions=["请描述主角的基本设定 (身份、背景、性格)"],
            )
        if not normalized.conflict_seed.strip():
            return NeedsUserInput(
                missing_fields=["conflict_seed"],
                current_understanding=f"用户描述了 '{req_input.user_input[:100]}...' 但未明确核心冲突",
                questions=["请描述故事的核心冲突或主要矛盾"],
            )

        # 5. 校验 target_episode_count 范围
        if not (1 <= normalized.target_episode_count <= 100):
            logger.warning(
                "LLM 输出 target_episode_count=%d 超出合法范围, 强制修正为 %d",
                normalized.target_episode_count,
                req_input.target_episode_count,
            )
            normalized.target_episode_count = req_input.target_episode_count

        return normalized

    # ---- 内部方法 ----

    def _check_critical_fields(self, req_input: RequirementInput) -> NeedsUserInput | None:
        """前置校验: 用户输入是否包含主角和冲突信息。

        如果信息不足, 返回 NeedsUserInput 让用户补充;
        如果信息足够, 返回 None 表示可以继续 LLM 调用。

        判断依据:
        - 输入长度 >= _MIN_INPUT_LENGTH
        - 包含至少一个主角关键词
        - 包含至少一个冲突关键词
        """
        user_input = req_input.user_input.strip()
        missing: list[str] = []
        questions: list[str] = []

        # 长度检查
        if len(user_input) < _MIN_INPUT_LENGTH:
            missing.append("user_input (输入过短, 缺少足够信息)")
            questions.append("请提供更详细的创作需求 (至少包含主角设定和核心冲突)")

        # 主角信息检查
        if not _has_protagonist_info(user_input):
            missing.append("protagonist_seed")
            questions.append("请提供主角的基本信息 (如身份、背景、特殊能力等)")

        # 冲突信息检查
        if not _has_conflict_info(user_input):
            missing.append("conflict_seed")
            questions.append("请描述故事的核心冲突或主要矛盾 (如逆袭、复仇、争夺等)")

        if missing:
            return NeedsUserInput(
                missing_fields=missing,
                current_understanding=(
                    f"已收到输入 ({req_input.source_type}, "
                    f"{len(user_input)} 字符): '{user_input[:200]}'"
                ),
                questions=questions,
            )

        return None
