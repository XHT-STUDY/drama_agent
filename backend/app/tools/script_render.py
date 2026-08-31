"""剧本正文渲染 — 从结构化 ScriptDraft 确定性渲染中文短剧剧本纯文本 (G-06 配套)。

目标格式（plain_text、导出、前端展示、diff 行构造共用同一套行构造）：

    1-1 日 外 冰封荒原-风雪坡
    人物：苏翊
    △狂风裹挟着冰屑横扫荒原，天地间白茫茫一片。
    △风雪中，苏翊裹紧领口，手套上结满冰霜。
    苏翊OS（冷静）：雪质干硬。局部结霜有差异。
    林晓VO（高亢）：试炼开启。
    苏翊（凝重）：爪印深达十公分，步幅超两米。是极地冰熊。

- 场景头：`{集号}-{场号} {时间} {内/外} {地点}`，缺省内外景按 外 处理；
- 出场人物：`人物：A、B`（无人物信息时省略）；
- 动作：action 按行拆分，每行加 `△` 前缀；
- 台词：`角色名（情绪）：文本`；旁白为 `角色名VO（情绪）：文本`，
  内心独白为 `角色名OS（情绪）：文本`。

模块边界：纯函数渲染，输入为 dict（Pydantic 模型先 model_dump()），
不调用 LLM、不触碰存储。
"""

from __future__ import annotations

from typing import Any

# 台词类型 → 角色名后缀
_LINE_TYPE_SUFFIX: dict[str, str] = {"dialogue": "", "vo": "VO", "os": "OS"}

# 内外景缺省值
DEFAULT_INT_EXT = "外"


def scene_heading(episode_number: int, scene: dict[str, Any]) -> str:
    """场景头行：`1-1 日 外 冰封荒原-风雪坡`。"""
    int_ext = str(scene.get("int_ext") or DEFAULT_INT_EXT).strip() or DEFAULT_INT_EXT
    return (
        f"{episode_number}-{scene.get('scene_number', '')} "
        f"{scene.get('time_of_day', '')} {int_ext} {scene.get('location', '')}"
    )


def _characters_line(scene: dict[str, Any]) -> str | None:
    """出场人物行：`人物：A、B`；无人物信息返回 None。"""
    chars = [str(c).strip() for c in scene.get("characters") or [] if str(c).strip()]
    if not chars:
        return None
    return f"人物：{'、'.join(chars)}"


def _action_lines(scene: dict[str, Any]) -> list[str]:
    """动作描述行：action 按行拆分，每行加 △ 前缀。"""
    raw = str(scene.get("action") or "").strip()
    if not raw:
        return []
    return [f"△{line.strip()}" for line in raw.splitlines() if line.strip()]


def dialogue_line(d: dict[str, Any]) -> str:
    """单句台词行：`苏翊（冷静）：…` / `苏翊OS（冷静）：…` / `林晓VO（高亢）：…`。"""
    suffix = _LINE_TYPE_SUFFIX.get(str(d.get("line_type") or "dialogue"), "")
    head = f"{d.get('speaker', '')}{suffix}"
    parenthetical = str(d.get("parenthetical") or "").strip()
    if parenthetical:
        head += f"（{parenthetical}）"
    return f"{head}：{d.get('text', '')}"


def scene_lines(
    episode_number: int,
    scene: dict[str, Any],
    *,
    with_heading: bool = True,
) -> list[str]:
    """单场戏 → 与 plain_text 同构的行列表（确定性）。

    diff、导出与渲染共用，保证行结构一致。
    """
    lines: list[str] = []
    if with_heading:
        lines.append(scene_heading(episode_number, scene))
    characters_line = _characters_line(scene)
    if characters_line:
        lines.append(characters_line)
    lines.extend(_action_lines(scene))
    lines.extend(dialogue_line(d) for d in scene.get("dialogue") or [])
    return lines


def script_plain_text(episode_number: int, scenes: list[dict[str, Any]]) -> str:
    """整集剧本 → plain_text（场次间以空行分隔）。"""
    blocks = ["\n".join(scene_lines(episode_number, scene)) for scene in scenes]
    return "\n\n".join(blocks)
