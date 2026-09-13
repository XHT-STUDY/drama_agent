"""正文证据纯函数（W1-03）——原文规范化、场景文本汇总与引文溯源。

从 Evaluator 的私有实现提取（不能复制第二份校验算法）：评估的
evidence 溯源与 Agent 的原文解释引文验证共用同一套归一化匹配，
"引用是否真在剧本里"只有一种答案。
"""

from __future__ import annotations

import re
from typing import Any

# 归一化时移除的字符：所有空白 + 中英文标点（引用漂移最常见的差异来源）
_NORMALIZE_STRIP_RE = re.compile(r"[\s，。！？；：、“”‘’（）《》〈〉【】—…·,.\!?;:\"'()\[\]<>{}]")


def normalize_text(text: str) -> str:
    """归一化文本用于引用匹配：去空白与标点、转小写。

    LLM 摘抄常伴随标点改写或换行差异，归一化后做子串匹配
    可以容忍这类无关差异，只惩罚实质性的内容改写。
    """
    return _NORMALIZE_STRIP_RE.sub("", text).lower()


def scene_texts_from_content(content: dict[str, Any]) -> dict[int, str]:
    """按场次汇总可检索文本（动作描写 + 对白），从 Artifact content 读取。"""
    texts: dict[int, str] = {}
    for scene in content.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        parts = [scene.get("action") or ""]
        for line in scene.get("dialogue") or []:
            if isinstance(line, dict):
                parts.append(line.get("text") or "")
        texts[int(scene.get("scene_number", 0))] = "".join(parts)
    return texts


def find_scene(norm_quote: str, scene_texts: dict[int, str]) -> int | None:
    """在全部场次中检索引用（须先归一化），返回命中的场次号。"""
    for scene_number, text in scene_texts.items():
        if norm_quote in normalize_text(text):
            return scene_number
    return None


def verify_quote(
    quote: str,
    scene_number: int | None,
    scene_texts: dict[int, str],
) -> tuple[bool, int | None]:
    """验证一条引文是否真实存在于剧本原文（评估与解释共用）。

    策略（软校验，不抛异常）：
    - 指定场次且能在该场匹配 → (True, None)；
    - 指定场次未命中但在其他场命中 → (True, 纠正后的场次号)；
    - 未指定场次且全文命中 → (True, None)；
    - 都不命中 → (False, None)。

    Returns:
        (verified, corrected_scene_number)
    """
    norm_quote = normalize_text(quote)
    if not norm_quote:
        return False, None

    if scene_number is not None:
        scene_text = normalize_text(scene_texts.get(scene_number, ""))
        if norm_quote in scene_text:
            return True, None
        corrected = find_scene(norm_quote, scene_texts)
        if corrected is not None:
            return True, corrected
        return False, None

    full_text = normalize_text("".join(scene_texts.values()))
    if norm_quote in full_text:
        return True, None
    return False, None
