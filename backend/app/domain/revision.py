"""修订计划模型 — RevisionOperation 与 RevisionPlan（§5.9）。

RevisionPlan 由评估报告驱动，指定要修改的场景、保留项和变化上限。
"""

from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class RevisionOperation(BaseModel):
    """单个修订操作。

    每个操作绑定到具体的评估问题，包含修改指令和必须保留的内容。
    """

    model_config = {"extra": "forbid"}

    operation_id: str = Field(..., description="操作唯一标识", min_length=1)
    target_scene_number: int | None = Field(
        default=None, description="目标场景编号，null 表示跨场景修改", ge=1
    )
    issue_ids: list[str] = Field(
        default_factory=list, description="此操作应对的 issue ID 列表"
    )
    instruction: str = Field(..., description="修订指令", min_length=1)
    preserve: list[str] = Field(
        default_factory=list, description="必须保留的内容（不可修改）"
    )
    expected_effect: str = Field(
        default="", description="预期效果描述"
    )


class RevisionPlan(BaseModel):
    """修订计划。

    指定要修订哪一集、修订操作列表、锁定事实和最大修改比例。
    MVP 中 max_change_ratio 默认 0.35。
    """

    model_config = {"extra": "forbid"}

    episode_number: int = Field(..., description="待修订的集号", ge=1)
    source_script_artifact_id: UUID = Field(
        ..., description="原稿 Artifact ID"
    )
    source_evaluation_artifact_id: UUID = Field(
        ..., description="评估报告 Artifact ID"
    )
    operations: list[RevisionOperation] = Field(
        ..., description="修订操作列表"
    )
    locked_facts: list[str] = Field(
        default_factory=list, description="本次修订必须遵守的锁定事实"
    )
    max_change_ratio: float = Field(
        default=0.35,
        description="允许的最大文本变化比例",
        ge=0.0,
        le=1.0,
    )

    @model_validator(mode="after")
    def _check_operations_non_empty(self) -> "RevisionPlan":
        """至少需要一个修订操作。"""
        if len(self.operations) < 1:
            raise ValueError("修订计划至少需要一个 operation")
        return self
