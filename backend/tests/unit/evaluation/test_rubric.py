"""Rubric 与确定性指标单元测试 (E-01).

覆盖验收项：
- 权重和严格等于 1；
- overall_score 只由服务端计算；
- 高风险问题可独立触发 need_revision；
- Rubric 版本进入 Artifact metadata（rubric.version 非空且可读）；
- 辅助特征（ScriptStructureTool）不替代 LLM 维度分。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.domain.enums import (
    DEFAULT_EVALUATION_WEIGHTS,
    EvaluationDimension,
)
from app.domain.evaluation import (
    EvaluationIssue,
    compute_need_revision,
    compute_overall_score,
)
from app.domain.rubric import (
    Rubric,
    RubricLoadError,
    ensure_weights_match_enums,
    load_rubric,
)
from app.tools.script_structure import (
    ScriptStructureTool,
    compute_script_features,
)


def _sample_rubric_dict() -> dict[str, Any]:
    """构造一份合法的 Rubric dict（用于非法用例的对比与裁剪）。"""
    dims = []
    for dim, weight in DEFAULT_EVALUATION_WEIGHTS.items():
        dims.append(
            {
                "dimension": dim.value,
                "label": dim.value,
                "weight": weight,
                "description": f"{dim.value} 说明",
                "anchors": {1: "一档", 3: "三档", 5: "五档"},
            }
        )
    return {
        "version": "1.0.0",
        "description": "测试 rubric",
        "dimensions": dims,
    }


class TestRubricLoad:
    """默认 rubric 配置的加载与完整性。"""

    def test_load_default_rubric(self) -> None:
        """默认 mvp_v1.yaml 可加载并通过全部校验。"""
        rubric = load_rubric()
        assert isinstance(rubric, Rubric)
        assert rubric.version  # 版本非空，进入 Artifact metadata
        assert len(rubric.dimensions) == 9

    def test_weights_sum_to_one(self) -> None:
        """权重和严格等于 1（容差 1e-6）。"""
        rubric = load_rubric()
        total = sum(spec.weight for spec in rubric.dimensions)
        assert total == 1.0

    def test_all_nine_dimensions(self) -> None:
        """覆盖全部 9 个评估维度。"""
        rubric = load_rubric()
        dims = {spec.dimension for spec in rubric.dimensions}
        assert dims == set(EvaluationDimension)

    def test_anchors_complete(self) -> None:
        """每个维度锚点包含 1/3/5 三档。"""
        rubric = load_rubric()
        for spec in rubric.dimensions:
            assert {1, 3, 5} <= set(spec.anchors.keys())

    def test_weights_align_with_enums(self) -> None:
        """Rubric 权重与 domain/enums.py 默认权重一致（必须同步）。"""
        rubric = load_rubric()
        assert ensure_weights_match_enums(rubric) is True


class TestRubricValidation:
    """非法 Rubric 数据的校验失败。"""

    def test_weights_not_sum_to_one(self) -> None:
        """权重和不等于 1 时校验失败。"""
        data = _sample_rubric_dict()
        data["dimensions"][0]["weight"] = 0.99  # 破坏权重和
        with pytest.raises(Exception) as exc:
            Rubric.model_validate(data)
        assert "权重之和" in str(exc.value)

    def test_missing_dimension(self) -> None:
        """缺少维度时校验失败。"""
        data = _sample_rubric_dict()
        data["dimensions"].pop()  # 去掉最后一个维度
        with pytest.raises(Exception) as exc:
            Rubric.model_validate(data)
        assert "缺少维度" in str(exc.value)

    def test_duplicate_dimension(self) -> None:
        """重复维度时校验失败。"""
        data = _sample_rubric_dict()
        data["dimensions"].append(data["dimensions"][0])  # 复制第一个维度
        with pytest.raises(Exception) as exc:
            Rubric.model_validate(data)
        assert "重复" in str(exc.value)

    def test_missing_anchor_level(self) -> None:
        """锚点缺少 1/3/5 档位时校验失败。"""
        data = _sample_rubric_dict()
        data["dimensions"][0]["anchors"] = {1: "一档", 3: "三档"}  # 缺 5 档
        with pytest.raises(Exception) as exc:
            Rubric.model_validate(data)
        assert "锚点缺少档位" in str(exc.value)


class TestRubricLoadErrors:
    """加载失败场景。"""

    def test_missing_file(self) -> None:
        """文件不存在抛出 RubricLoadError。"""
        with pytest.raises(RubricLoadError):
            load_rubric("/nonexistent/rubric.yaml")

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        """YAML 损坏抛出 RubricLoadError。"""
        p = tmp_path / "bad.yaml"
        p.write_text("rubric: [unclosed", encoding="utf-8")
        with pytest.raises(RubricLoadError):
            load_rubric(p)

    def test_missing_root_key(self, tmp_path: Path) -> None:
        """缺少 rubric 根键抛出 RubricLoadError。"""
        p = tmp_path / "no_root.yaml"
        p.write_text("foo: bar\n", encoding="utf-8")
        with pytest.raises(RubricLoadError):
            load_rubric(p)


class TestOverallScore:
    """overall_score 只由服务端确定性规则计算。"""

    def test_compute_overall_score_weighted(self) -> None:
        """加权总分正确，且为浮点一位小数。"""
        scores = {
            EvaluationDimension.OPENING_HOOK: 80,
            EvaluationDimension.MAIN_CLARITY: 70,
            EvaluationDimension.CHARACTER_APPEAL: 60,
            EvaluationDimension.CONFLICT_INTENSITY: 90,
            EvaluationDimension.PAYOFF_DENSITY: 50,
            EvaluationDimension.ENDING_HOOK: 100,
            EvaluationDimension.PACING: 80,
            EvaluationDimension.VISUALIZABILITY: 70,
            EvaluationDimension.COMPLIANCE_SAFETY: 100,
        }
        # 手工计算：0.15*80 + 0.10*70 + 0.10*60 + 0.15*90 + 0.15*50
        #           + 0.15*100 + 0.10*80 + 0.05*70 + 0.05*100
        expected = (
            0.15 * 80 + 0.10 * 70 + 0.10 * 60 + 0.15 * 90 + 0.15 * 50
            + 0.15 * 100 + 0.10 * 80 + 0.05 * 70 + 0.05 * 100
        )
        assert compute_overall_score(scores) == round(expected, 1)

    def test_compute_overall_score_with_custom_weights(self) -> None:
        """支持自定义权重（服务端传入）。"""
        scores = {
            EvaluationDimension.OPENING_HOOK: 100,
            EvaluationDimension.MAIN_CLARITY: 0,
            EvaluationDimension.CHARACTER_APPEAL: 0,
            EvaluationDimension.CONFLICT_INTENSITY: 0,
            EvaluationDimension.PAYOFF_DENSITY: 0,
            EvaluationDimension.ENDING_HOOK: 0,
            EvaluationDimension.PACING: 0,
            EvaluationDimension.VISUALIZABILITY: 0,
            EvaluationDimension.COMPLIANCE_SAFETY: 0,
        }
        weights = {
            dim: (1.0 if dim is EvaluationDimension.OPENING_HOOK else 0.0)
            for dim in EvaluationDimension
        }
        assert compute_overall_score(scores, weights) == 100.0


def _make_issue(severity: str = "low") -> EvaluationIssue:
    """构造一个测试 issue。"""
    return EvaluationIssue(
        issue_id="iss_test",
        dimension=EvaluationDimension.CONFLICT_INTENSITY,
        severity=severity,  # type: ignore[arg-type]
        evidence="证据",
        diagnosis="诊断",
        suggestion="建议",
    )


class TestNeedRevision:
    """need_revision 确定性规则。"""

    def test_overall_below_threshold(self) -> None:
        """overall < 75 触发 need_revision。"""
        assert compute_need_revision(74.9, []) is True
        assert compute_need_revision(75.0, []) is False

    def test_high_severity_issue_triggers(self) -> None:
        """high 严重度问题独立触发 need_revision（即使总分足够）。"""
        issues = [_make_issue(severity="high")]
        assert compute_need_revision(90.0, issues) is True

    def test_low_medium_issues_do_not_trigger(self) -> None:
        """low/medium 问题且总分足够时不触发。"""
        assert compute_need_revision(90.0, [_make_issue("low")]) is False
        assert compute_need_revision(90.0, [_make_issue("medium")]) is False

    def test_compliance_below_threshold(self) -> None:
        """compliance_safety < 60 独立触发 need_revision。"""
        scores = dict.fromkeys(EvaluationDimension, 90)
        scores[EvaluationDimension.COMPLIANCE_SAFETY] = 59
        assert compute_need_revision(90.0, [], scores) is True
        scores[EvaluationDimension.COMPLIANCE_SAFETY] = 60
        assert compute_need_revision(90.0, [], scores) is False

    def test_no_trigger_when_healthy(self) -> None:
        """健康剧本不触发。"""
        scores = dict.fromkeys(EvaluationDimension, 85)
        assert compute_need_revision(85.0, [], scores) is False


class TestScriptStructure:
    """客观辅助特征——不替代 LLM 维度分。"""

    def _script(self) -> dict[str, Any]:
        return {
            "episode_number": 1,
            "opening_hook": "开场钩子",
            "ending_hook": "",
            "plain_text": "开场钩子 甲说：你好。",
            "scenes": [
                {
                    "scene_number": 1,
                    "characters": ["林峰", "教练"],
                    "dialogue": [{"speaker": "林峰", "text": "你好"}],
                },
                {
                    "scene_number": 2,
                    "characters": ["林峰", "路人"],
                    "dialogue": [
                        {"speaker": "林峰", "text": "再见"},
                        {"speaker": "路人", "text": "加油"},
                    ],
                },
            ],
        }

    def test_scene_and_character_features(self) -> None:
        """场景数与去重角色数正确。"""
        features = compute_script_features(self._script())
        assert features["scene_count"] == 2
        assert features["character_count"] == 3  # 林峰/教练/路人
        assert features["dialogue_line_count"] == 3

    def test_hook_features(self) -> None:
        """钩子字段的存在性与长度。"""
        features = compute_script_features(self._script())
        assert features["opening_hook_present"] is True
        assert features["opening_hook_length"] == 4
        assert features["ending_hook_present"] is False
        assert features["ending_hook_length"] == 0

    def test_dialogue_ratio_feature(self) -> None:
        """对白占比特征为 0-1 浮点。"""
        features = compute_script_features(self._script())
        assert 0.0 <= features["dialogue_ratio"] <= 1.0

    def test_tool_does_not_produce_dimension_scores(self) -> None:
        """辅助特征不包含 dimension_scores，不替代 LLM 维度分。"""
        features = compute_script_features(self._script())
        assert "dimension_scores" not in features
        assert "overall_score" not in features

    def test_tool_async_execute(self) -> None:
        """ScriptStructureTool 可直接执行。"""
        import asyncio

        tool = ScriptStructureTool()
        result = asyncio.run(tool.execute(script=self._script()))
        assert result["scene_count"] == 2
        assert tool.metadata.name == "compute_script_structure"

    def test_empty_scenes(self) -> None:
        """空场景不抛异常，返回安全默认值。"""
        features = compute_script_features({"scenes": [], "plain_text": ""})
        assert features["scene_count"] == 0
        assert features["character_count"] == 0
        assert features["dialogue_ratio"] == 0.0
