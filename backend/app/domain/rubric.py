"""评估标准 Rubric 模型与加载 (§5.8, E-01).

Rubric 是纯数据资产（权威版本位于 knowledge/rubric/mvp_v1.yaml）：
- 定义 9 个评估维度、权重与 1/3/5 档锚点说明；
- 服务端校验权重和为 1、维度齐全、锚点完整；
- 权重与 domain/enums.py 的 DEFAULT_EVALUATION_WEIGHTS 保持一致。

模块边界：本模块只做"加载 + 校验 + 查询"，不做任何评估计算；
维度分的加权计算在 domain/evaluation.py。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from app.domain.enums import DEFAULT_EVALUATION_WEIGHTS, EvaluationDimension

# knowledge/rubric 目录相对于本文件（backend/app/domain/ → 仓库根）
_DEFAULT_RUBRIC_PATH = (
    Path(__file__).resolve().parents[3] / "knowledge" / "rubric" / "mvp_v1.yaml"
)

# 锚点档位：必须且只能包含 1/3/5 三档
ANCHOR_LEVELS = (1, 3, 5)


class RubricScoreRules(BaseModel):
    """评分触发规则（服务端确定性规则）。"""

    model_config = {"extra": "forbid"}

    revision_threshold: float = Field(
        default=75.0, description="总分低于该值触发 need_revision"
    )
    compliance_threshold: float = Field(
        default=60.0, description="合规维度低于该值独立触发 need_revision"
    )
    need_revision: str = Field(
        default="overall < 75 或存在 severity=high 的问题 或 compliance_safety < 60",
        description="need_revision 规则的文字说明",
    )


class RubricDimension(BaseModel):
    """单个评估维度定义。"""

    model_config = {"extra": "forbid"}

    dimension: EvaluationDimension = Field(..., description="维度标识（与枚举一致）")
    label: str = Field(..., description="中文标签", min_length=1)
    weight: float = Field(..., description="权重（0-1，全部维度权重和为 1）", gt=0.0)
    description: str = Field(..., description="维度说明", min_length=1)
    anchors: dict[int, str] = Field(
        ..., description="1/3/5 三档锚点说明：档位 → 描述"
    )


class Rubric(BaseModel):
    """完整评估标准（9 个维度）。

    校验：
    - 必须覆盖全部 9 个 EvaluationDimension；
    - 维度不得重复；
    - 权重和为 1（容差 1e-6）；
    - 每个维度锚点必须包含 1/3/5 三档。
    """

    model_config = {"extra": "forbid"}

    version: str = Field(..., description="Rubric 版本号（进入 Artifact metadata）", min_length=1)
    description: str = Field(default="", description="Rubric 总体说明")
    score_rules: RubricScoreRules = Field(
        default_factory=RubricScoreRules, description="评分触发规则"
    )
    dimensions: list[RubricDimension] = Field(
        ..., description="9 个评估维度定义"
    )

    @model_validator(mode="after")
    def _check_dimensions_complete(self) -> Rubric:
        """必须覆盖全部 9 个维度且不重复。"""
        seen: set[EvaluationDimension] = set()
        for spec in self.dimensions:
            if spec.dimension in seen:
                raise ValueError(f"维度重复定义: {spec.dimension.value}")
            seen.add(spec.dimension)
        missing = set(EvaluationDimension) - seen
        if missing:
            raise ValueError(
                f"Rubric 缺少维度: {', '.join(d.value for d in sorted(missing, key=lambda d: d.value))}"
            )
        return self

    @model_validator(mode="after")
    def _check_weights_sum_to_one(self) -> Rubric:
        """权重和必须等于 1（容差 1e-6）。"""
        total = sum(spec.weight for spec in self.dimensions)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"维度权重之和为 {total}，必须等于 1.0")
        return self

    @model_validator(mode="after")
    def _check_anchors_complete(self) -> Rubric:
        """每个维度锚点必须包含 1/3/5 三档。"""
        for spec in self.dimensions:
            missing = [lv for lv in ANCHOR_LEVELS if lv not in spec.anchors]
            if missing:
                raise ValueError(
                    f"维度 {spec.dimension.value} 锚点缺少档位: {missing}"
                )
        return self

    # ---- 查询接口 ----

    def weights(self) -> dict[EvaluationDimension, float]:
        """维度权重映射（用于 compute_overall_score）。"""
        return {spec.dimension: spec.weight for spec in self.dimensions}

    def dimension_spec(self, dim: EvaluationDimension) -> RubricDimension:
        """按维度枚举获取定义。"""
        for spec in self.dimensions:
            if spec.dimension is dim:
                return spec
        raise KeyError(f"Rubric 中不存在维度: {dim.value}")

    def anchors_text(self) -> str:
        """渲染维度锚点说明文本（供注入 Prompt）。"""
        lines = []
        for spec in self.dimensions:
            lines.append(f"[{spec.dimension.value}] {spec.label}（权重 {spec.weight}）")
            lines.append(f"  说明：{spec.description}")
            for lv in ANCHOR_LEVELS:
                lines.append(f"  - {lv} 档：{spec.anchors[lv]}")
        return "\n".join(lines)


class RubricLoadError(Exception):
    """Rubric 加载失败——文件不存在、YAML 损坏或校验失败。"""


def load_rubric(path: str | Path | None = None) -> Rubric:
    """从 YAML 加载并校验 Rubric。

    Args:
        path: rubric YAML 路径；为 None 时使用默认
              knowledge/rubric/mvp_v1.yaml。

    Returns:
        校验通过的 Rubric 模型。

    Raises:
        RubricLoadError: 文件不存在、YAML 解析失败或校验失败。
    """
    target = Path(path) if path is not None else _DEFAULT_RUBRIC_PATH

    if not target.exists():
        raise RubricLoadError(f"Rubric 文件不存在: {target}")

    try:
        raw: dict[str, Any] = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RubricLoadError(f"Rubric YAML 解析失败: {target}\n{e}") from e

    if not isinstance(raw, dict) or "rubric" not in raw:
        raise RubricLoadError(f"Rubric 内容格式错误（缺少 rubric 根键）: {target}")

    try:
        return Rubric.model_validate(raw["rubric"])
    except Exception as e:  # pydantic.ValidationError 及其子类
        raise RubricLoadError(f"Rubric 校验失败 ({target}):\n{e}") from e


def ensure_weights_match_enums(rubric: Rubric) -> bool:
    """校验 Rubric 权重与 domain/enums.py 默认权重一致。

    权重变更必须同步两处，此函数用于回归测试与启动自检。

    Args:
        rubric: 已加载的 Rubric。

    Returns:
        一致返回 True，否则返回 False。
    """
    return rubric.weights() == DEFAULT_EVALUATION_WEIGHTS
