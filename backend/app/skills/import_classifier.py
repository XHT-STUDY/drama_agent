"""ImportClassifierSkill — 导入内容分类 (G-04)。

职责:
- 规则先行：对原始文本做确定性特征提取（字符数/行数/场景标记/分集标记/
  对白行数/参考关键词），命中明确类别直接返回，**不调 LLM**;
- LLM 兜底：规则未命中（模糊文本）时调 prompt "import_classifier"，
  FakeLLM 注册即测;

类别（enums.ContentType 五类）:
- idea_or_notes  创作灵感/片段笔记
- outline        分集大纲/剧情纲要
- full_script    完整剧本
- reference      参考资料/素材
- unknown        无法判断

模块边界（Skill 协议）:
- 只做"文本 → ImportClassification"的纯分类，不访问 ORM、不持久化。
"""

from __future__ import annotations

import logging
import re
from typing import Any, cast

from app.agents.base import BaseAgent
from app.domain.enums import ContentType
from app.domain.import_file import ImportClassification, ImportClassificationInput
from app.prompts.loader import PromptLoader
from app.skills.protocol import Skill, SkillMetadata
from app.tools.word_count import count_total_chars

logger = logging.getLogger(__name__)


# ========================================================================
# 确定性特征提取与规则
# ========================================================================

# 场景标记：第X场 / 第X幕 / 第X回 / scene 3 / 1-1 场 等
_SCENE_RE = re.compile(
    r"(第\s*[一二三四五六七八九十百\d]+\s*(场|幕|回))"
    r"|(^scene\s+\d+\b)"
    r"|(^\d+\.\d+\s*[场幕回])",
    re.IGNORECASE | re.MULTILINE,
)
# 分集标记：第X集
_EPISODE_RE = re.compile(r"第\s*[一二三四五六七八九十百\d]+\s*集")
# 对白行：一行内"名称[:：]对白"（名称 1~12 字符，冒号后至少 2 字符）
_DIALOGUE_RE = re.compile(r"^[^:：\n]{1,12}\s*[:：].{2,}$", re.MULTILINE)
# 参考资料关键词
_REFERENCE_RE = re.compile(
    r"(参考资料|参考素材|素材库|创作参考|参考文献|http[s]?://|www\.)"
)
# 规则判定为"短文本"的字符阈值（无结构特征时视为 idea_or_notes）
_IDEA_MAX_CHARS = 150


def extract_import_features(filename: str, text: str) -> dict[str, Any]:
    """从原始文本提取分类用客观特征。"""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    dialogue_lines = sum(1 for ln in lines if _DIALOGUE_RE.match(ln.strip()))
    scene_markers = len(_SCENE_RE.findall(text))
    episode_markers = len(_EPISODE_RE.findall(text))
    return {
        "char_count": count_total_chars(text),
        "line_count": len(lines),
        "scene_markers": scene_markers,
        "episode_markers": episode_markers,
        "dialogue_lines": dialogue_lines,
        "dialogue_ratio": round(dialogue_lines / len(lines), 3) if lines else 0.0,
        "has_reference_keywords": bool(_REFERENCE_RE.search(text)),
        "extension": filename.rsplit(".", 1)[-1].lower() if "." in filename else "",
    }


def _rule_result(
    content_type: ContentType,
    confidence: float,
    reason: str,
    features: dict[str, Any],
) -> ImportClassification:
    """构造规则命中的分类结果。"""
    return ImportClassification(
        content_type=content_type,
        confidence=confidence,
        reason=reason,
        detected_features=features,
    )


def classify_by_rules(features: dict[str, Any]) -> ImportClassification | None:
    """确定性规则分类；未命中（模糊）返回 None，交给 LLM。

    规则（按顺序，只判明确信号）：
    1. 文本过短（<20 字符）→ unknown（信息不足）;
    2. 含参考资料关键词 → reference;
    3. 短文本且无场景/分集标记 → idea_or_notes;
    4. 场景标记 ≥2 且对白行 ≥5 → full_script（强剧本结构）。
    """
    if features["char_count"] < 20:
        return _rule_result(
            "unknown", 0.9, "文本过短（<20 字符），无法判断内容类别", features
        )
    if features["has_reference_keywords"]:
        return _rule_result(
            "reference", 0.9, "命中参考资料/素材关键词，判定为参考资料", features
        )
    if (
        features["char_count"] < _IDEA_MAX_CHARS
        and features["scene_markers"] == 0
        and features["episode_markers"] == 0
    ):
        return _rule_result(
            "idea_or_notes", 0.85, "短文本且无剧本结构特征，判定为创作灵感/笔记", features
        )
    if features["scene_markers"] >= 2 and features["dialogue_lines"] >= 5:
        return _rule_result(
            "full_script", 0.85, "场景标记与对白行数构成完整剧本结构", features
        )
    return None


# ========================================================================
# ImportClassifierSkill
# ========================================================================


class ImportClassifierSkill(Skill):
    """导入内容分类 Skill（规则先行 + LLM 兜底）。"""

    metadata = SkillMetadata(
        name="import_classifier",
        version="1.0",
        description="把上传文本分类为 idea_or_notes/outline/full_script/reference/unknown",
    )

    async def execute(self, context: dict[str, Any]) -> ImportClassification:
        """执行分类。

        context 必需键:
            input: ImportClassificationInput — 文件名 + 解析文本
            agent: BaseAgent — 用于 LLM 兜底调用
            prompt_loader: PromptLoader — 用于加载 import_classifier 模板

        Returns:
            校验通过的 ImportClassification

        Raises:
            RuntimeError: LLM 兜底调用失败
        """
        cls_input: ImportClassificationInput = context["input"]
        agent: BaseAgent = context["agent"]
        prompt_loader: PromptLoader = context["prompt_loader"]

        # 1. 规则先行（不调 LLM）
        features = extract_import_features(cls_input.filename, cls_input.text)
        ruled = classify_by_rules(features)
        if ruled is not None:
            logger.info(
                "导入分类规则命中: %s (conf=%s)",
                ruled.content_type, ruled.confidence,
            )
            return ruled

        # 2. LLM 兜底（模糊文本）
        logger.info("导入分类规则未命中，回退 LLM: upload=%s", cls_input.upload_id)
        tpl = prompt_loader.get("import_classifier")
        preview = cls_input.text[:2000]
        rendered = tpl.render(
            filename=cls_input.filename,
            preview_chars=str(len(preview)),
            text_preview=preview,
        )

        messages: list[dict[str, str]] = [{"role": "user", "content": rendered}]
        result = await agent.generate_structured(
            ImportClassification,
            messages,
            prompt_name="import_classifier",
            temperature=0.2,
        )
        if result.error_code or result.parsed is None:
            logger.error(
                "LLM 导入分类失败: code=%s detail=%s",
                result.error_code,
                result.error_detail,
            )
            raise RuntimeError(
                f"导入分类 LLM 调用失败: {result.error_code} - {result.error_detail}"
            )

        # 补上客观特征供 Artifact 归档
        # 注：LLMCallResult.parsed 类型为 Any，此处用 cast 恢复领域类型
        #（已在上方校验 parsed 非 None，Pydantic 输出必为 ImportClassification）
        parsed = cast(ImportClassification, result.parsed)
        parsed.detected_features = features
        return parsed
