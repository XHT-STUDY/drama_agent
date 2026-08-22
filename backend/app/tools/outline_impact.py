"""确定性大纲影响分析工具 — OutlineImpactTool（J-07）。

纯 Python 实现，不调用 LLM。逐字段比较新旧 EpisodeOutlineSet：
- 文本空白差异规范化后不算变化（normalize_text）;
- 输出变更集（集号 + 字段级明细）、整体弧线是否变化;
- dependent_scripts（携带 source_outline_artifact_id 的剧本引用）中
  依赖旧大纲且所在集发生变化的剧本 ID;
- 基于以上事实生成 follow-up 建议（供 Planner/影响摘要与后续计划使用）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.domain.outline import EpisodeOutlineSet
from app.domain.outline_revision import normalize_text
from app.tools.protocol import Tool, ToolMetadata

# 参与比较的单集字段（标量文本 + 列表字段拼接后比较）
_EPISODE_FIELDS: tuple[str, ...] = (
    "title", "opening_hook", "objective", "core_conflict", "key_events",
    "payoff", "ending_hook", "next_bridge", "introduced_loops",
    "resolved_loops", "required_characters",
)


class DependentScript(BaseModel):
    """引用旧大纲的剧本（由调用方从 Artifact source 链解析）。"""

    model_config = {"extra": "forbid"}

    script_artifact_id: str = Field(..., description="剧本 Artifact UUID")
    episode_number: int = Field(..., ge=1, description="剧本所属集号")
    source_outline_artifact_id: str = Field(..., description="剧本 derived_from 的大纲 Artifact UUID")


class EpisodeFieldChange(BaseModel):
    """单集单字段的变化明细。"""

    model_config = {"extra": "forbid"}

    episode_number: int = Field(..., ge=1)
    field: str
    old_value: str
    new_value: str


class OutlineImpactResult(BaseModel):
    """大纲影响分析结果。"""

    model_config = {"extra": "forbid"}

    changed_episodes: list[int] = Field(default_factory=list, description="发生字段变化的集号列表（升序）")
    field_changes: list[EpisodeFieldChange] = Field(default_factory=list, description="字段级变化明细")
    arc_summary_changed: bool = Field(default=False, description="整体弧线说明是否变化")
    dependent_script_ids: list[str] = Field(
        default_factory=list, description="依赖旧大纲且所在集发生变化的剧本 Artifact ID"
    )
    follow_ups: list[str] = Field(default_factory=list, description="确定性后续建议")


def _field_value(episode: Any, field: str) -> str:
    """读取字段并规范化为可比较/可展示的字符串（列表拼接，空白归一）。"""
    value = getattr(episode, field)
    if isinstance(value, list):
        value = "；".join(str(item) for item in value)
    return normalize_text(str(value))


class OutlineImpactTool(Tool):
    """新旧大纲确定性影响分析（不调用 LLM）。"""

    metadata = ToolMetadata(
        name="outline_impact",
        version="1.0",
        description="逐字段比较新旧分集大纲，输出变更集、受影响剧本与后续建议",
        input_schema={
            "type": "object",
            "properties": {
                "old": {"type": "object", "description": "旧 EpisodeOutlineSet"},
                "new": {"type": "object", "description": "新 EpisodeOutlineSet"},
                "dependent_scripts": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "引用旧大纲的剧本列表",
                },
                "old_outline_artifact_id": {
                    "type": "string",
                    "description": "旧大纲 Artifact UUID；提供时按它过滤 dependent_scripts",
                },
            },
            "required": ["old", "new"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "changed_episodes": {"type": "array", "items": {"type": "integer"}},
                "field_changes": {"type": "array", "items": {"type": "object"}},
                "arc_summary_changed": {"type": "boolean"},
                "dependent_script_ids": {"type": "array", "items": {"type": "string"}},
                "follow_ups": {"type": "array", "items": {"type": "string"}},
            },
        },
    )

    async def execute(self, **kwargs: Any) -> OutlineImpactResult:
        """逐字段比较新旧大纲并汇总影响。

        Args（关键字参数）:
            old / new: 旧/新 EpisodeOutlineSet（或其 dict 表示）;
            dependent_scripts: 引用旧大纲的剧本（含来源大纲 ID 与集号）;
            old_outline_artifact_id: 提供时只把 source 指向该大纲的剧本视为依赖方。

        Returns:
            OutlineImpactResult；新旧一致（空白规范化后）时全部字段为空/False。
        """
        old: Any = kwargs["old"]
        new: Any = kwargs["new"]
        dependent_scripts: Any = kwargs.get("dependent_scripts")
        old_outline_artifact_id: str | None = kwargs.get("old_outline_artifact_id")

        old_set = old if isinstance(old, EpisodeOutlineSet) else EpisodeOutlineSet.model_validate(old)
        new_set = new if isinstance(new, EpisodeOutlineSet) else EpisodeOutlineSet.model_validate(new)
        scripts = [
            s if isinstance(s, DependentScript) else DependentScript.model_validate(s)
            for s in (dependent_scripts or [])
        ]

        old_by_ep = {ep.episode_number: ep for ep in old_set.episodes}
        new_by_ep = {ep.episode_number: ep for ep in new_set.episodes}

        field_changes: list[EpisodeFieldChange] = []
        for ep_number in sorted(set(old_by_ep) & set(new_by_ep)):
            for field in _EPISODE_FIELDS:
                old_value = _field_value(old_by_ep[ep_number], field)
                new_value = _field_value(new_by_ep[ep_number], field)
                if old_value != new_value:
                    field_changes.append(
                        EpisodeFieldChange(
                            episode_number=ep_number,
                            field=field,
                            old_value=old_value,
                            new_value=new_value,
                        )
                    )

        changed_episodes = sorted({change.episode_number for change in field_changes})
        arc_summary_changed = (
            normalize_text(old_set.arc_summary) != normalize_text(new_set.arc_summary)
        )

        # 依赖旧大纲 = 来源指向旧大纲（提供了 ID 时按 ID 过滤）；只有所在集
        # 变化的剧本才真正受影响。
        dependent_script_ids = sorted(
            s.script_artifact_id
            for s in scripts
            if (
                old_outline_artifact_id is None
                or s.source_outline_artifact_id == old_outline_artifact_id
            )
            and s.episode_number in changed_episodes
        )

        follow_ups: list[str] = []
        for script_id in dependent_script_ids:
            episode = next(
                s.episode_number
                for s in scripts
                if s.script_artifact_id == script_id
            )
            follow_ups.append(
                f"第 {episode} 集剧本依赖旧大纲且该集大纲已变化，建议发起剧本修订（script {script_id}）"
            )
        if arc_summary_changed:
            follow_ups.append("整体弧线说明已变化，建议复核各集之间的衔接（next_bridge）")
        if not changed_episodes and not arc_summary_changed:
            follow_ups.append("新旧大纲无实质差异，无需后续动作")

        return OutlineImpactResult(
            changed_episodes=changed_episodes,
            field_changes=field_changes,
            arc_summary_changed=arc_summary_changed,
            dependent_script_ids=dependent_script_ids,
            follow_ups=follow_ups,
        )
