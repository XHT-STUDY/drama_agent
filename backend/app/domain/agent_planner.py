"""对话命令 Planner 的非执行领域契约（J-03，IR-3 增加目标解析纯函数）。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.agent_command import ActiveArtifactContext

# ---- 目标对象关键词表（W1-03：Planner 预检与解释目标解析的同一词表） ----
TARGET_KEYWORDS: dict[str, list[str]] = {
    "outline": ["大纲"],
    "story_bible": ["设定", "故事圣经", "story bible", "storybible"],
}
TARGET_OUTLINE_RE = re.compile("|".join(TARGET_KEYWORDS["outline"]))
TARGET_STORY_BIBLE_RE = re.compile("|".join(TARGET_KEYWORDS["story_bible"]), re.IGNORECASE)
# 指代词（"这里/当前稿"）：目标只能来自活动上下文，否则澄清
CONTEXT_REFERENCE_RE = re.compile(r"(这里|此处|这个版本|当前稿|当前剧本|上面|这场)")


class AgentPlannerInput(BaseModel):
    """Planner 的服务端输入；available_intents 由服务端生成。"""

    model_config = {"extra": "forbid"}

    user_request: str = Field(..., min_length=1, max_length=4000)
    project_title: str = Field(default="", max_length=200)
    target_episode_count: int = Field(default=1, ge=1, le=500)
    available_intents: list[str] = Field(..., min_length=1)
    active_context: ActiveArtifactContext | None = None
    # W1-03：builder 按 agent_context_budget_tokens（token）裁剪；字符上限
    # 只防异常超大输入，不再是二次裁切
    project_context: str = Field(default="", max_length=60000)
    unresolved_turn_count: int = Field(default=0, ge=0, le=3)


class PlannerTarget(BaseModel):
    """仅描述用户可读目标，不携带 Artifact ID 或执行句柄。"""

    model_config = {"extra": "forbid"}

    target_type: Literal["project", "story_bible", "outline", "script", "evaluation"]
    episode_number: int | None = Field(default=None, ge=1)


class PlannerStep(BaseModel):
    """可读的意图说明；不是可执行 ActionStep。"""

    model_config = {"extra": "forbid"}

    title: str = Field(..., min_length=1, max_length=120)
    description: str = Field(..., min_length=1, max_length=500)


class AgentPlannerOutput(BaseModel):
    """Planner 输出的非执行计划。"""

    model_config = {"extra": "forbid"}

    turn_type: Literal["clarification", "answer", "plan"]
    intent: str | None = Field(default=None, min_length=1, max_length=40)
    target: PlannerTarget | None = None
    constraints: list[str] = Field(default_factory=list, max_length=20)
    steps: list[PlannerStep] = Field(default_factory=list, max_length=12)
    expected_impact: list[str] = Field(default_factory=list, max_length=20)
    clarification_question: str | None = Field(default=None, max_length=1000)
    answer: str | None = Field(default=None, max_length=4000)
    batch_size: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="仅 intent=continue 时有意义：本批集数，缺省写完全部",
    )


# ============================================================
# 目标解析（IR-3 §8.2）——确定性优先的纯函数合同
# ============================================================

# 集数解析（W1-03）：阿拉伯数字与常见中文数字（一~九十九），
# 如"第3集/第3集剧本/第三集/EP3"
_EPISODE_RE = re.compile(
    r"(?:第\s*|ep(?:isode)?[\s_-]*)(\d+|[一二两三四五六七八九十]+)\s*(?:[集话回期])?",
    re.IGNORECASE,
)
_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def parse_cn_int(raw: str) -> int | None:
    """中文数字（一~九十九）→ 整数；非法返回 None。"""
    if raw.isdigit():
        return int(raw)
    if not raw:
        return None
    if raw == "十":
        return 10
    if "十" in raw:
        high, _, low = raw.partition("十")
        tens = _CN_DIGITS.get(high, 1) if high else 1
        ones = _CN_DIGITS.get(low, 0) if low else 0
        if (high and high not in _CN_DIGITS) or (low and low not in _CN_DIGITS):
            return None
        return tens * 10 + ones
    if raw in _CN_DIGITS:
        return _CN_DIGITS[raw]
    return None


def extract_episode_numbers(request: str) -> list[int]:
    """提取请求中的全部集数（阿拉伯/中文），按出现顺序去重。"""
    episodes: list[int] = []
    for match in _EPISODE_RE.finditer(request):
        value = parse_cn_int(match.group(1))
        if value is not None and value >= 1 and value not in episodes:
            episodes.append(value)
    return episodes


def explicit_objects(request: str) -> set[str]:
    """请求中明确点名的目标对象类别（outline / story_bible / script）。"""
    objects: set[str] = set()
    if TARGET_OUTLINE_RE.search(request):
        objects.add("outline")
    if TARGET_STORY_BIBLE_RE.search(request):
        objects.add("story_bible")
    if re.search(r"剧本", request):
        objects.add("script")
    return objects


# 目标分歧类型（低基数，供日志与 IR-4 指标 label 使用）
TargetDisagreement = Literal[
    "episode_normalized",  # 文本明确集数 ≠ 模型集数 → 规范化为文本
    "context_normalized",  # 仅指代 + 合法上下文 ≠ 模型目标 → 规范化为上下文
    "object_mismatch",  # 文本明确对象与模型意图方向相反 → 拒绝（澄清）
]


def resolve_plan_target(
    *,
    user_request: str,
    active_context: ActiveArtifactContext | Mapping[str, Any] | None,
    intent: str,
    model_target: PlannerTarget | None,
    target_episode_count: int,
) -> tuple[PlannerTarget | None, TargetDisagreement | None]:
    """按确定性优先级解析最终目标（IR-3 §8.2）。

    优先级：文本明确对象/集数 > 合法活动上下文 > 模型推断的 selector。
    - 文本明确集数与模型集数不一致 → 规范化为文本集数（episode_normalized）；
      仅对集数承载目标的意图生效（evaluate/revise_script/explain）——
      revise_outline 文本里的集数是大纲条目引用，不是执行目标；
    - 文本明确大纲/设定而模型意图指向剧本 → object_mismatch
      （调用方应转为澄清，不生成 Action；反向不做此判——大纲目标可由
      活动上下文或对话历史合法给出，由确认卡片兜底）；
    - 仅指代且活动上下文合法 → 上下文目标优先于模型推断（context_normalized）；
    - 文本与上下文指向不同目标 → 文本优先（同一 episode_normalized 路径）。
    其余情况返回模型目标（无分歧）。
    """
    episodes = extract_episode_numbers(user_request)
    objects = explicit_objects(user_request)

    # 文本明确对象 vs 模型意图方向相反：宁可澄清不可错执行
    outline_like = bool(objects & {"outline", "story_bible"})
    if outline_like and intent == "revise_script":
        return None, "object_mismatch"

    target = model_target

    # 1) 文本明确集数：绝对优先（覆盖上下文与模型推断）
    if episodes and intent in {"evaluate", "revise_script", "explain"}:
        explicit_ep = episodes[0]
        if 1 <= explicit_ep <= target_episode_count:
            current_ep = target.episode_number if target else None
            if current_ep != explicit_ep:
                base = target or PlannerTarget(target_type="script")
                target = base.model_copy(update={"episode_number": explicit_ep}, deep=True)
                return target, "episode_normalized"
    # 2) 仅指代 + 合法活动上下文：上下文优先于模型推断
    elif CONTEXT_REFERENCE_RE.search(user_request) and active_context is not None:
        ac_ep = (
            active_context.get("episode_number")
            if isinstance(active_context, Mapping)
            else active_context.episode_number
        )
        ac_type = _context_target_type(
            active_context.get("artifact_type")
            if isinstance(active_context, Mapping)
            else active_context.artifact_type
        )
        current_ep = target.episode_number if target else None
        if (ac_ep is not None and current_ep != ac_ep) or (
            ac_type is not None and (target is None or target.target_type != ac_type)
        ):
            base = target or PlannerTarget(target_type=ac_type or "script")
            target = base.model_copy(update={"episode_number": ac_ep}, deep=True)
            return target, "context_normalized"

    return target, None


_PlannerTargetType = Literal["project", "story_bible", "outline", "script", "evaluation"]


def _context_target_type(artifact_type: str | None) -> _PlannerTargetType | None:
    """活动上下文的 Artifact 类型 → PlannerTarget 类型。"""
    if artifact_type == "script_draft":
        return "script"
    if artifact_type == "episode_outline_set":
        return "outline"
    if artifact_type == "story_bible":
        return "story_bible"
    return None


# ============================================================
# 原文解释（W1-03）——ArtifactExplainerSkill 的输入/输出契约
# ============================================================


class ExplanationSourceText(BaseModel):
    """解释的单个原文来源（服务端组装并编号，模型只回引 source_index）。"""

    model_config = {"extra": "forbid"}

    source_index: int = Field(..., ge=1)
    kind: Literal["script_scene", "story_bible", "outline", "evaluation"]
    label: str = Field(..., max_length=120, description="给模型看的位置说明")
    scene_number: int | None = Field(default=None, ge=1)
    text: str = Field(..., max_length=60000)


class ArtifactExplanationInput(BaseModel):
    """解释模型输入：用户问题 + 已读原文（受预算约束的服务端组装）。"""

    model_config = {"extra": "forbid"}

    question: str = Field(..., min_length=1, max_length=4000)
    target_label: str = Field(..., max_length=200, description="解释对象（如 第3集 v2）")
    sources: list[ExplanationSourceText] = Field(..., min_length=1, max_length=20)


class ExplanationCitationOutput(BaseModel):
    """模型输出的一条引文：仅 source_index + 场次 + 逐字原文。

    服务端验证 quote 确实出现在对应来源原文中，再回填
    ArtifactCitation（artifact_id/version/checksum）。
    """

    model_config = {"extra": "forbid"}

    source_index: int = Field(..., ge=1)
    scene_number: int | None = Field(default=None, ge=1)
    quote: str = Field(..., min_length=1, max_length=200)


class ArtifactExplanationOutput(BaseModel):
    """解释模型输出：回答 + 引用的 source_index（不含任何 Artifact ID）。"""

    model_config = {"extra": "forbid"}

    answer: str = Field(..., min_length=1, max_length=2000)
    citations: list[ExplanationCitationOutput] = Field(
        default_factory=list,
        max_length=10,
        description="支撑回答的原文引文；无法引用原文的论断不应出现在 answer 中",
    )
