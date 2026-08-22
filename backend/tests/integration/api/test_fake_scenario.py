"""FakeLLM 场景开关测试 (H-07 E2E 支撑).

验证 FAKE_LLM_SCENARIO 对 _register_fake_fixtures 注册 evaluate_episode
fixture 的影响：

- 默认（无开关）：高分报告（overall >= 75）→ 服务端确定性回填
  need_revision=False → 自动修订选不出集、走 finalize/completed;
- FAKE_LLM_SCENARIO=revision：低分报告（overall < 75）→ need_revision=True →
  自动修订按 F-05 确定性选最低分集（平局取最小集号）、恰好只修 1 集
  （E2E 验收「每次只修订一个低分集」）。

仅验证 fixture 注册与回填规则的组合，不启动服务、不连数据库。
"""

from __future__ import annotations

from typing import cast

import pytest

from app.application.workflow_dispatcher import _register_fake_fixtures
from app.domain.evaluation import EvaluationReport, compute_need_revision
from app.llm.fake import FakeLLM


async def _registered_eval_report(monkeypatch: pytest.MonkeyPatch) -> EvaluationReport:
    """按当前 FAKE_LLM_SCENARIO 注册 fixture 并取回 evaluate_episode 报告。"""
    llm = FakeLLM(seed=42)
    _register_fake_fixtures(llm)
    result = await llm.generate_structured(
        EvaluationReport, [], prompt_name="evaluate_episode",
    )
    assert result.parsed is not None
    return cast(EvaluationReport, result.parsed)


async def test_default_scenario_registers_high_score_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """默认无开关：高分报告，服务端回填规则判定无需修订。"""
    monkeypatch.delenv("FAKE_LLM_SCENARIO", raising=False)
    report = await _registered_eval_report(monkeypatch)

    assert report.overall_score >= 75
    assert report.need_revision is True  # LLM 自报字段，服务端会回填
    # 服务端确定性回填判定：高分 → 无需修订
    assert compute_need_revision(
        report.overall_score, report.issues, report.dimension_scores,
    ) is False


async def test_revision_scenario_registers_low_score_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FAKE_LLM_SCENARIO=revision：低分报告，触发修订闭环。"""
    monkeypatch.setenv("FAKE_LLM_SCENARIO", "revision")
    report = await _registered_eval_report(monkeypatch)

    assert report.overall_score < 75
    # 服务端确定性回填判定：低分 → 需要修订（E2E 修订分支的前提）
    assert compute_need_revision(
        report.overall_score, report.issues, report.dimension_scores,
    ) is True


async def test_scenario_switch_does_not_leak_to_default_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """场景开关互不影响：默认路径注册不受历史 env 干扰。"""
    monkeypatch.setenv("FAKE_LLM_SCENARIO", "revision")
    _ = await _registered_eval_report(monkeypatch)
    monkeypatch.delenv("FAKE_LLM_SCENARIO")
    default_report = await _registered_eval_report(monkeypatch)
    assert default_report.overall_score >= 75
