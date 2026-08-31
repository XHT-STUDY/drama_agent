"""评估标准 Rubric 模型与加载 (§5.8, E-01; v2 可解释性升级).

Rubric 是纯数据资产（权威版本位于 knowledge/rubric/mvp_v2.yaml）：
- 定义 9 个评估维度、权重、1-5 共 5 档锚点说明、可观察信号；
- score_bands 定义档位 → 分数区间映射，供服务端做"分数-档位自洽"校验；
- 服务端校验权重和为 1、维度齐全、锚点完整、分带无缝覆盖 0-100；
- 权重与 domain/enums.py 的 DEFAULT_EVALUATION_WEIGHTS 保持一致。

模块边界：本模块只做"加载 + 校验 + 查询"，不做任何评估计算；
维度分的加权计算与分带 clamp 在 domain/evaluation.py。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from app.domain.enums import DEFAULT_EVALUATION_WEIGHTS, EvaluationDimension

# knowledge/rubric 目录相对于本文件（backend/app/domain/ → 仓库根）
_DEFAULT_RUBRIC_PATH = (
    Path(__file__).resolve().parents[3] / "knowledge" / "rubric" / "mvp_v2.yaml"
)

# 锚点档位：必须且只能包含 1-5 五档（v2 起从 1/3/5 三档扩展）
ANCHOR_LEVELS = (1, 2, 3, 4, 5)


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
        ..., description="1-5 五档锚点说明：档位 → 描述"
    )
    signals: list[str] = Field(
        default_factory=list,
        description="可观察信号（剧本中可核对的具体事实，引导评估引用原文证据）",
    )


def _normalize_bands(raw: Any) -> dict[int, tuple[int, int]]:
    """将 score_bands 原始数据规范化为 {level: (min, max)}。

    Args:
        raw: YAML 解析出的分带数据（dict[level, [min, max]]）。

    Returns:
        规范化后的分带映射。

    Raises:
        ValueError: 档位非 1-5、区间端点非法或结构错误。
    """
    if not isinstance(raw, dict):
        raise ValueError("score_bands 必须是 {档位: [min, max]} 映射")
    bands: dict[int, tuple[int, int]] = {}
    for key, value in raw.items():
        try:
            level = int(key)
        except (TypeError, ValueError) as e:
            raise ValueError(f"score_bands 档位非法: {key!r}") from e
        if level not in ANCHOR_LEVELS:
            raise ValueError(f"score_bands 档位必须在 {ANCHOR_LEVELS}: {level}")
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError(f"档位 {level} 的分带必须是 [min, max]: {value!r}")
        lo, hi = int(value[0]), int(value[1])
        if not (0 <= lo <= hi <= 100):
            raise ValueError(
                f"档位 {level} 分带 [{lo}, {hi}] 非法（需 0 <= min <= max <= 100）"
            )
        bands[level] = (lo, hi)
    missing = [lv for lv in ANCHOR_LEVELS if lv not in bands]
    if missing:
        raise ValueError(f"score_bands 缺少档位: {missing}")
    return bands


class Rubric(BaseModel):
    """完整评估标准（9 个维度）。

    校验：
    - 必须覆盖全部 9 个 EvaluationDimension；
    - 维度不得重复；
    - 权重和为 1（容差 1e-6）；
    - 每个维度锚点必须包含 1-5 五档；
    - score_bands 五档齐全且区间无缝覆盖 0-100（升序不重叠不留隙）。
    """

    model_config = {"extra": "forbid"}

    version: str = Field(..., description="Rubric 版本号（进入 Artifact metadata）", min_length=1)
    description: str = Field(default="", description="Rubric 总体说明")
    score_bands: dict[int, tuple[int, int]] = Field(
        ...,
        description="档位 → 分数区间映射（1-5 档，无缝覆盖 0-100）",
    )
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
        """每个维度锚点必须包含 1-5 五档。"""
        for spec in self.dimensions:
            missing = [lv for lv in ANCHOR_LEVELS if lv not in spec.anchors]
            if missing:
                raise ValueError(
                    f"维度 {spec.dimension.value} 锚点缺少档位: {missing}"
                )
        return self

    @model_validator(mode="after")
    def _check_score_bands(self) -> Rubric:
        """分带必须五档齐全且无缝覆盖 0-100。"""
        bands = _normalize_bands(self.score_bands)
        ordered = sorted(bands.values(), key=lambda b: b[0])
        if ordered[0][0] != 0:
            raise ValueError(f"score_bands 未从 0 开始: {ordered[0]}")
        if ordered[-1][1] != 100:
            raise ValueError(f"score_bands 未覆盖到 100: {ordered[-1]}")
        # 错位配对（长度差 1 是有意的），显式 strict=False
        for prev, nxt in zip(ordered, ordered[1:], strict=False):
            if nxt[0] != prev[1] + 1:
                raise ValueError(
                    f"score_bands 存在间隙或重叠: {prev} 与 {nxt}"
                )
        return self

    # ---- 查询接口 ----

    def weights(self) -> dict[EvaluationDimension, float]:
        """维度权重映射（用于 compute_overall_score）。"""
        return {spec.dimension: spec.weight for spec in self.dimensions}

    def score_band(self, level: int) -> tuple[int, int]:
        """查询档位对应的分数区间 [min, max]。"""
        bands = _normalize_bands(self.score_bands)
        if level not in bands:
            raise KeyError(f"Rubric 分带不存在档位: {level}")
        return bands[level]

    def dimension_spec(self, dim: EvaluationDimension) -> RubricDimension:
        """按维度枚举获取定义。"""
        for spec in self.dimensions:
            if spec.dimension is dim:
                return spec
        raise KeyError(f"Rubric 中不存在维度: {dim.value}")

    def anchors_text(self) -> str:
        """渲染维度锚点说明文本（供注入 Prompt）。

        输出 5 档锚点与可观察信号，并附档位-分带映射说明，
        引导模型"先定位档位、再给档内分数"。
        """
        lines = []
        band_desc = "、".join(
            f"{lv}档={self.score_band(lv)[0]}-{self.score_band(lv)[1]}分"
            for lv in ANCHOR_LEVELS
        )
        lines.append(f"档位与分数区间映射：{band_desc}")
        for spec in self.dimensions:
            lines.append(f"[{spec.dimension.value}] {spec.label}（权重 {spec.weight}）")
            lines.append(f"  说明：{spec.description}")
            if spec.signals:
                lines.append("  可观察信号（评分时必须核对并在评估中引用）：")
                for sig in spec.signals:
                    lines.append(f"    - {sig}")
            for lv in ANCHOR_LEVELS:
                lo, hi = self.score_band(lv)
                lines.append(f"  - {lv} 档（{lo}-{hi}分）：{spec.anchors[lv]}")
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
