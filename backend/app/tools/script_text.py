"""FullScript 文本 → ScriptDraft 确定性转换 (G-06)。

把上传的完整剧本文本转换为最小合法 ScriptDraft content，
使"完整剧本文件能进入评估流程"（阶段 G Exit Gate）端到端成立，
不依赖 LLM（CI 全 FakeLLM 约束）。

支持常见剧本格式：
- 场景标记：`第X场 地点（时间）` / `第X场 地点[时间]` / 裸 `第X场`;
- 对白行：`角色：对白` / `角色:对白`；
- 非场景非对白的非空行 → 动作/描写（拼进当前场）。

转换是 best-effort：无法构造合法结构（如不足 2 场戏 / 空文本）
返回 None，调用方放弃持久化脚本（仍保留分类结果与警告）。
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from app.tools.dialogue_ratio import compute_dialogue_ratio
from app.tools.word_count import count_total_chars

# 场景标记：`第X场 地点（时间）` 或 `第X场 地点[时间]`
_SCENE_RE = re.compile(
    r"^\s*第\s*([0-9]+)\s*场[\s、.．]*(.*?)\s*[（(\[]([^）)\]　\s]+)[）)\]]\s*$"
)
# 兜底：仅 `第X场`（无地点 / 时间）
_SCENE_BARE_RE = re.compile(r"^\s*第\s*([0-9]+)\s*场\s*$")
# 对白：`角色：对白` / `角色:对白`
_DIALOGUE_RE = re.compile(r"^\s*([^：:]{1,20})[：:]\s*(.+?)\s*$")

_LOCATION_FALLBACK = "室内"
_TIME_DEFAULT = "日"
_ACTION_FALLBACK = "（转场）"


def _parse_scene_header(line: str) -> tuple[int | None, str | None, str | None]:
    """尝试从行首解析 `第X场 地点（时间）`。

    Returns:
        (场景号, 地点, 时间)；非场景标记返回 (None, None, None)。
    """
    m = _SCENE_RE.match(line)
    if m:
        num = int(m.group(1))
        location = (m.group(2).strip() or _LOCATION_FALLBACK).strip()
        time_of_day = (m.group(3).strip() or _TIME_DEFAULT).strip()
        return num, location, time_of_day
    m = _SCENE_BARE_RE.match(line)
    if m:
        return int(m.group(1)), _LOCATION_FALLBACK, _TIME_DEFAULT
    return None, None, None


def _finalize_scene(scene: dict[str, Any]) -> dict[str, Any]:
    """结算一场戏：保证 action 非空（Scene 校验 min_length=1）。"""
    if not (scene.get("action") or "").strip():
        scene["action"] = _ACTION_FALLBACK
    return scene


def full_script_to_script_draft(
    text: str,
    *,
    title: str,
    episode_number: int = 1,
    referenced_outline_artifact_id: str | None = None,
) -> dict[str, Any] | None:
    """把完整剧本文本转换为 ScriptDraft content（best-effort）。

    Args:
        text: 解析后的剧本文本
        title: 剧本标题（可取自文件名）
        episode_number: 集号（导入默认为 1）
        referenced_outline_artifact_id: 引用大纲 Artifact ID（缺省生成随机 UUID）

    Returns:
        可通过 ScriptDraft 校验的 content dict；无法构造合法结构返回 None。
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None

    scenes: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for ln in lines:
        num, location, time_of_day = _parse_scene_header(ln)
        if num is not None:
            if current is not None:
                scenes.append(_finalize_scene(current))
            current = {
                "scene_number": len(scenes) + 1,
                "location": location or _LOCATION_FALLBACK,
                "time_of_day": time_of_day or _TIME_DEFAULT,
                "characters": [],
                "action": "",
                "dialogue": [],
            }
            continue
        if current is None:
            continue  # 首个场景标记之前的内容忽略
        dm = _DIALOGUE_RE.match(ln)
        if dm:
            speaker = dm.group(1).strip()
            line_text = dm.group(2).strip()
            current["dialogue"].append({"speaker": speaker, "text": line_text})
            if speaker not in current["characters"]:
                current["characters"].append(speaker)
        else:
            # 非场景非对白 → 动作 / 描写
            current["action"] = (current["action"] + " " + ln).strip()

    if current is not None:
        scenes.append(_finalize_scene(current))

    if len(scenes) < 2:
        return None

    # 钩子（min_length=1）：取首 / 末对白文本，无对白时退化为首 / 末行
    all_dialogue = [d for s in scenes for d in s["dialogue"]]
    opening_hook = all_dialogue[0]["text"] if all_dialogue else lines[0]
    ending_hook = all_dialogue[-1]["text"] if all_dialogue else lines[-1]

    plain_text = "\n".join(lines)
    total_chars = count_total_chars(plain_text)
    word_count = total_chars
    dialogue_ratio = compute_dialogue_ratio(scenes, plain_text)

    return {
        "episode_number": episode_number,
        "title": title,
        "opening_hook": opening_hook,
        "scenes": scenes,
        "ending_hook": ending_hook,
        "plain_text": plain_text,
        "word_count": word_count,
        "dialogue_ratio": dialogue_ratio,
        "referenced_outline_artifact_id": referenced_outline_artifact_id
        or str(uuid.uuid4()),
    }
