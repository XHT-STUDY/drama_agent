"""Diff 领域模型 — 两版剧本对比结果 (F-04).

纯 Pydantic 数据模型，不含算法逻辑——算法在 app/tools/diff.py。
diff 为确定性计算输出（非 LLM），但按项目风格用 Pydantic 建模，
model_dump(mode="json") 自动完成 UUID→str 与中文原样保留。
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

SceneChangeType = Literal["added", "removed", "modified", "unchanged"]


class DiffLineStats(BaseModel):
    """行级 / 字符级统计（均 >= 0）。

    changed_chars = added_chars + removed_chars；
    from_chars / to_chars 为两版本场景文本的字符总数（与分子同口径）。
    """

    model_config = {"extra": "forbid"}

    added_lines: int = Field(..., description="新增行数", ge=0)
    removed_lines: int = Field(..., description="删除行数", ge=0)
    modified_lines: int = Field(..., description="修改行数", ge=0)
    added_chars: int = Field(..., description="新增字符数", ge=0)
    removed_chars: int = Field(..., description="删除字符数", ge=0)
    changed_chars: int = Field(..., description="变更字符数（added+removed）", ge=0)
    from_chars: int = Field(..., description="from 版本场景文本字符数", ge=0)
    to_chars: int = Field(..., description="to 版本场景文本字符数", ge=0)


class SceneDiffSummary(BaseModel):
    """场景级汇总统计。"""

    model_config = {"extra": "forbid"}

    from_scene_count: int = Field(..., description="from 场景数", ge=0)
    to_scene_count: int = Field(..., description="to 场景数", ge=0)
    added: int = Field(..., description="新增场景数", ge=0)
    removed: int = Field(..., description="删除场景数", ge=0)
    modified: int = Field(..., description="修改场景数", ge=0)
    unchanged: int = Field(..., description="未变场景数", ge=0)


class LineChange(BaseModel):
    """单行变化（mode=scene 时是场景内行；mode=line 时是全文行）。"""

    model_config = {"extra": "forbid"}

    change_type: SceneChangeType
    old_line_number: int | None = Field(default=None, description="from 侧行号（1-based）", ge=1)
    new_line_number: int | None = Field(default=None, description="to 侧行号（1-based）", ge=1)
    old_text: str | None = Field(default=None, description="from 侧文本")
    new_text: str | None = Field(default=None, description="to 侧文本")


class SceneChange(BaseModel):
    """单场景变化。"""

    model_config = {"extra": "forbid"}

    change_type: SceneChangeType
    old_scene_number: int | None = Field(default=None, description="from 侧场景号", ge=1)
    new_scene_number: int | None = Field(default=None, description="to 侧场景号", ge=1)
    location: str = Field(default="", description="场景地点")
    time_of_day: str = Field(default="", description="时间（日/夜等）")
    similarity: float = Field(..., description="场景文本相似度", ge=0.0, le=1.0)
    added_lines: int = Field(default=0, description="本场景新增行数", ge=0)
    removed_lines: int = Field(default=0, description="本场景删除行数", ge=0)
    modified_lines: int = Field(default=0, description="本场景修改行数", ge=0)
    added_chars: int = Field(default=0, description="本场景新增字符数", ge=0)
    removed_chars: int = Field(default=0, description="本场景删除字符数", ge=0)
    line_changes: list[LineChange] = Field(default_factory=list, description="行级变化明细")
    line_changes_truncated: bool = Field(
        default=False, description="本场景行明细因超大 diff 被截断"
    )


class ScriptDiff(BaseModel):
    """完整 Diff 结果。

    mode="scene" 表示结构化场景对齐；mode="line" 表示无法解析 ScriptDraft
    时回退的全文行 diff。Artifact 元数据（from_artifact_id 等）由
    DiffService 填充，纯函数输出时均为 None。
    """

    model_config = {"extra": "forbid"}

    mode: Literal["scene", "line"] = Field(..., description="diff 模式")
    # ---- Artifact 元数据（diff_service 填充，纯函数为 None）----
    from_artifact_id: UUID | None = None
    to_artifact_id: UUID | None = None
    from_version: int | None = Field(default=None, description="from 版本号", ge=1)
    to_version: int | None = Field(default=None, description="to 版本号", ge=1)
    project_id: UUID | None = None
    episode_number: int | None = Field(default=None, description="集号", ge=1)
    # ---- 结果 ----
    change_ratio: float = Field(
        ..., description="对称变化比例 (removed+added)/(from+to)，方向无关", ge=0.0, le=1.0
    )
    scene_summary: SceneDiffSummary
    stats: DiffLineStats
    scene_changes: list[SceneChange] = Field(default_factory=list)
    line_changes: list[LineChange] = Field(
        default_factory=list, description="仅 mode=line 填充"
    )
    truncated: bool = Field(default=False, description="超大 diff 已截断响应体")
