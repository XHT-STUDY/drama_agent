"""ScriptStructureTool — 剧本结构客观特征计算工具 (E-01).

为评估器提供"客观辅助特征"：场景数、去重角色数、对白行数、对白占比、
钩子字段存在性与长度。这些特征是确定性计算的，**不替代任何 LLM 维度分**，
仅作为评估 Prompt 的辅助信息（如：钩子缺失可由特征直接佐证）。

不可隐式调用 LLM——纯 Python 实现。
"""

from __future__ import annotations

from typing import Any

from app.tools.dialogue_ratio import compute_dialogue_ratio
from app.tools.protocol import Tool, ToolMetadata


def count_scenes(scenes: list[dict[str, Any]]) -> int:
    """统计场次数。

    Args:
        scenes: Scene dict 列表

    Returns:
        场次数
    """
    return len(scenes)


def count_unique_characters(scenes: list[dict[str, Any]]) -> int:
    """统计去重后的角色数（来自每场的 characters 列表）。

    Args:
        scenes: Scene dict 列表

    Returns:
        去重角色数
    """
    chars: set[str] = set()
    for scene in scenes:
        for name in scene.get("characters", []):
            if name:
                chars.add(name)
    return len(chars)


def count_dialogue_lines(scenes: list[dict[str, Any]]) -> int:
    """统计对白总行数。

    Args:
        scenes: Scene dict 列表

    Returns:
        对白行数
    """
    return sum(len(scene.get("dialogue", [])) for scene in scenes)


def compute_script_features(script: dict[str, Any]) -> dict[str, Any]:
    """计算剧本的客观结构特征。

    Args:
        script: ScriptDraft 的 dict 表示（含 opening_hook/ending_hook/scenes/plain_text）

    Returns:
        特征字典：
        {
            "scene_count": int,
            "character_count": int,
            "dialogue_line_count": int,
            "dialogue_ratio": float,
            "opening_hook_present": bool,
            "opening_hook_length": int,
            "ending_hook_present": bool,
            "ending_hook_length": int,
        }
    """
    scenes: list[dict[str, Any]] = script.get("scenes", []) or []
    opening_hook: str = script.get("opening_hook", "") or ""
    ending_hook: str = script.get("ending_hook", "") or ""
    plain_text: str = script.get("plain_text", "") or ""

    return {
        "scene_count": count_scenes(scenes),
        "character_count": count_unique_characters(scenes),
        "dialogue_line_count": count_dialogue_lines(scenes),
        "dialogue_ratio": compute_dialogue_ratio(scenes, plain_text),
        "opening_hook_present": bool(opening_hook),
        "opening_hook_length": len(opening_hook),
        "ending_hook_present": bool(ending_hook),
        "ending_hook_length": len(ending_hook),
    }


# ========================================================================
# ScriptStructureTool
# ========================================================================


class ScriptStructureTool(Tool):
    """剧本结构客观特征工具。

    输入剧本 dict，输出场景数、角色数、对白占比等确定性特征。
    用于评估 Prompt 的辅助信息，不产生任何维度评分。
    """

    metadata = ToolMetadata(
        name="compute_script_structure",
        version="1.0",
        description="计算剧本客观结构特征（场景数/角色数/对白占比/钩子）——不产生维度评分",
    )

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """计算剧本结构特征。

        Args:
            script: dict — ScriptDraft 的 dict 表示

        Returns:
            客观特征字典（见 compute_script_features）
        """
        script: dict[str, Any] = kwargs.get("script", {})
        return compute_script_features(script)
