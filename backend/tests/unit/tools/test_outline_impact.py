"""OutlineImpactTool 单元测试（J-07）。

确定性影响分析（纯 Python，不调用 LLM）:
- 相同大纲（含纯空白差异）→ 空影响;
- TDD anchor: 变化集会报告依赖旧大纲的剧本（来源指向新大纲的不算）;
- 字段级明细、弧线变化与 follow-up 建议。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

import pytest

from app.domain.outline import EpisodeOutlineSet
from app.tools.outline_impact import (
    DependentScript,
    OutlineImpactTool,
)

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"


def _load(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))
    )


def _old_outline() -> EpisodeOutlineSet:
    return EpisodeOutlineSet.model_validate(_load("outline_set_valid"))


def _new_outline() -> EpisodeOutlineSet:
    return EpisodeOutlineSet.model_validate(_load("outline_revision_valid"))


@pytest.mark.asyncio
class TestOutlineImpactTool:
    async def test_identical_outlines_return_empty_impact(self) -> None:
        """相同大纲 → 空变更集、无受影响剧本、无后续动作之外的空建议。"""
        old = _old_outline()
        result = await OutlineImpactTool().execute(old=old, new=old)
        assert result.changed_episodes == []
        assert result.field_changes == []
        assert result.arc_summary_changed is False
        assert result.dependent_script_ids == []
        assert result.follow_ups == ["新旧大纲无实质差异，无需后续动作"]

    async def test_whitespace_only_differences_are_not_changes(self) -> None:
        """纯空白差异规范化后不算变化。"""
        old = _old_outline()
        data = _load("outline_set_valid")
        data["episodes"][0]["title"] = " " + data["episodes"][0]["title"] + "\n"
        data["episodes"][0]["key_events"] = [
            f" {e} " for e in data["episodes"][0]["key_events"]
        ]
        whitespace_new = EpisodeOutlineSet.model_validate(data)
        result = await OutlineImpactTool().execute(old=old, new=whitespace_new)
        assert result.changed_episodes == []
        assert result.arc_summary_changed is False

    async def test_changed_episode_reports_scripts_derived_from_old_outline(self) -> None:
        """TDD anchor: 变化集报告依赖旧大纲的剧本；来源为新大纲的不受影响。"""
        old, new = _old_outline(), _new_outline()
        old_id, new_id = str(uuid.uuid4()), str(uuid.uuid4())
        changed_ep = 3  # golden 修订只改第 3 集
        dependent = [
            # 依赖旧大纲 + 所在集变化 → 受影响
            DependentScript(
                script_artifact_id=str(uuid.uuid4()),
                episode_number=changed_ep,
                source_outline_artifact_id=old_id,
            ),
            # 依赖旧大纲但所在集未变 → 不受影响
            DependentScript(
                script_artifact_id=str(uuid.uuid4()),
                episode_number=5,
                source_outline_artifact_id=old_id,
            ),
            # 来源是新大纲（同集变化）→ 不算依赖旧大纲
            DependentScript(
                script_artifact_id=str(uuid.uuid4()),
                episode_number=changed_ep,
                source_outline_artifact_id=new_id,
            ),
        ]

        result = await OutlineImpactTool().execute(
            old=old,
            new=new,
            dependent_scripts=dependent,
            old_outline_artifact_id=old_id,
        )

        assert result.changed_episodes == [changed_ep]
        assert result.dependent_script_ids == [dependent[0].script_artifact_id]
        follow_up = result.follow_ups[0]
        assert f"第 {changed_ep} 集剧本" in follow_up
        assert dependent[0].script_artifact_id in follow_up

    async def test_field_changes_report_details_per_field(self) -> None:
        """字段级明细逐字段给出旧值/新值（空白规范化）。"""
        old, new = _old_outline(), _new_outline()
        result = await OutlineImpactTool().execute(old=old, new=new)

        fields = {c.field for c in result.field_changes}
        assert {"title", "core_conflict", "key_events", "payoff"} <= fields
        assert all(c.episode_number == 3 for c in result.field_changes)
        title_change = next(c for c in result.field_changes if c.field == "title")
        assert title_change.old_value == old.episodes[2].title.replace(" ", "")
        assert title_change.new_value == "试训风波：替补席上的暗流"
        assert result.arc_summary_changed is True

    async def test_metadata_declares_no_llm_deterministic_tool(self) -> None:
        """Tool 元数据完整（MCP 契约），且 Tool 是确定性抽象。"""
        tool = OutlineImpactTool()
        assert tool.metadata.name == "outline_impact"
        assert tool.metadata.input_schema["required"] == ["old", "new"]
        assert tool.metadata.output_schema["properties"]["dependent_script_ids"]
