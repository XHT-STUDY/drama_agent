"""连续性检查单元测试 (F-03).

覆盖验收:
- 固定事实被反转时失败（语义检查发现 → status=fail）
- 轻微措辞改变不误判为事实丢失（规则匹配容忍措辞变化）
- required event 被删除时失败（规则检查 → status=fail，且跳过语义检查）
- warnings 与 violations 分开（阻断/非阻断分列）
- 失败稿仍保存为 invalid/candidate 版本用于诊断（结果可序列化持久化）
- 规则检查优先: 规则失败不调用 LLM；规则通过才执行独立语义 Skill
- 语义检查输出 source 由服务端权威置为 "semantic"
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.agents.base import BaseAgent
from app.domain.continuity import ContinuityState
from app.domain.revision import (
    ContinuityCheckInput,
    ContinuityCheckResult,
    ContinuitySemanticCheck,
    ContinuityViolation,
    ContinuityWarning,
)
from app.domain.script import ScriptDraft
from app.llm.fake import FakeLLM
from app.memory.continuity import (
    ContinuityManager,
    character_name_by_id,
    extract_content_chars,
    fact_preserved_in_text,
    normalize_check_text,
)
from app.prompts.loader import PromptLoader
from app.skills.continuity_check import (
    ContinuityCheckSkill,
    ContinuitySemanticCheckSkill,
)
from app.tools.continuity_check import ContinuityCheckTool

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"


def _load_golden(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8")))


def _script_draft() -> ScriptDraft:
    return ScriptDraft.model_validate(_load_golden("script_draft_valid.json"))


def _continuity_state() -> ContinuityState:
    return ContinuityState.model_validate(_load_golden("continuity_state_valid.json"))


def _ep1_outline() -> dict[str, Any]:
    data = _load_golden("outline_set_valid.json")
    return cast(dict[str, Any], data["episodes"][0])


def _story_bible() -> dict[str, Any]:
    return _load_golden("story_bible_valid.json")


def _locked_facts() -> list[str]:
    return list(_continuity_state().locked_facts)


def _check_input(
    script: ScriptDraft | None = None,
    *,
    original: ScriptDraft | None = None,
    locked_facts: list[str] | None = None,
) -> ContinuityCheckInput:
    return ContinuityCheckInput(
        episode_number=1,
        script_draft=script or _script_draft(),
        original_script_draft=original,
        episode_outline=_ep1_outline(),
        story_bible=_story_bible(),
        continuity_state=_continuity_state(),
        locked_facts=locked_facts if locked_facts is not None else _locked_facts(),
    )


def _with_plain_text(draft: ScriptDraft, plain_text: str) -> ScriptDraft:
    """返回 plain_text 被替换的新稿副本（场景结构保持不变）。"""
    return draft.model_copy(deep=True, update={"plain_text": plain_text})


def _replacing(draft: ScriptDraft, old: str, new: str) -> ScriptDraft:
    """在原稿 plain_text 基础上做局部替换（保持其余内容与大纲事件完整）。"""
    return _with_plain_text(draft, draft.plain_text.replace(old, new))


def _without_character(draft: ScriptDraft, name: str) -> ScriptDraft:
    """返回从所有场景移除指定角色后的新稿副本。"""
    scenes = [
        scene.model_copy(
            update={
                "characters": [c for c in scene.characters if c != name]
            }
        )
        for scene in draft.scenes
    ]
    return draft.model_copy(deep=True, update={"scenes": scenes})


# ========================================================================
# 纯函数: 文本匹配
# ========================================================================


class TestTextMatching:
    """连续性检查的文本归一化与内容字符匹配。"""

    def test_normalize_check_text_strips_punctuation(self) -> None:
        assert normalize_check_text("林峰，你……知道吗？") == "林峰你知道吗"

    def test_normalize_check_text_strips_whitespace(self) -> None:
        assert normalize_check_text("青训营 更衣室\n【第1场】") == "青训营更衣室第1场"

    def test_extract_content_chars_drops_stopwords(self) -> None:
        chars = extract_content_chars("林峰的核心天赋是战术视野，不是超能力")
        assert "是" not in chars
        assert "的" not in chars
        assert "不" not in chars
        assert chars == "林峰核心天赋战术视野超能力"

    def test_substring_match_returns_full_coverage(self) -> None:
        preserved, coverage = fact_preserved_in_text(
            "青训教练宣布淘汰名单", "【第1场】青训教练宣布淘汰名单。"
        )
        assert preserved is True
        assert coverage == 1.0

    def test_slight_wording_change_not_flagged(self) -> None:
        """轻微措辞改变不误判为事实丢失（验收项）。"""
        preserved, coverage = fact_preserved_in_text(
            "林峰的核心天赋是战术视野，不是超能力",
            "林峰真正的天赋是战术视野——这并不是什么超能力，而是多年对比赛的阅读。",
        )
        assert preserved is True
        assert coverage >= 0.5

    def test_missing_fact_flagged(self) -> None:
        preserved, _ = fact_preserved_in_text(
            "林峰的核心天赋是战术视野",
            "他只想每天机械地训练，从没想过什么战术。",
        )
        assert preserved is False

    def test_empty_fact_treated_as_preserved(self) -> None:
        """空事实无可判 → 视为保留（由语义层兜底），避免误判。"""
        preserved, coverage = fact_preserved_in_text("", "任意文本")
        assert preserved is True
        assert coverage == 1.0


class TestCharacterNameById:
    """角色 ID → 名称映射。"""

    def test_maps_protagonist(self) -> None:
        assert character_name_by_id(_story_bible(), "char_protagonist_001") == "林峰"

    def test_maps_supporting(self) -> None:
        assert character_name_by_id(_story_bible(), "char_support_001") == "张德胜"

    def test_unknown_id_returns_none(self) -> None:
        assert character_name_by_id(_story_bible(), "char_ghost_999") is None


# ========================================================================
# 规则检查（ContinuityManager.run_rule_checks）
# ========================================================================


class TestRuleChecks:
    """确定性规则检查（F-03 规则优先）。"""

    def _run(
        self,
        script: ScriptDraft,
        *,
        original: ScriptDraft | None = None,
        locked_facts: list[str] | None = None,
    ) -> tuple[list[ContinuityViolation], list[ContinuityWarning], list[str]]:
        return ContinuityManager.run_rule_checks(
            episode_number=1,
            script_draft=script,
            original_script_draft=original,
            episode_outline=_ep1_outline(),
            story_bible=_story_bible(),
            locked_facts=locked_facts if locked_facts is not None else _locked_facts(),
        )

    def test_happy_path_no_violations(self) -> None:
        """原稿上跑规则检查：无违规、无警告（校验无误报）。"""
        violations, warnings, checks = self._run(_script_draft(), original=_script_draft())
        assert violations == []
        assert warnings == []
        assert checks == [
            "locked_facts_preserved",
            "required_events_present",
            "required_characters_present",
        ]

    def test_required_event_deleted_fails(self) -> None:
        """required event 被删除时失败（验收项）。"""
        gutted = _with_plain_text(
            _script_draft(),
            "青训营的更衣室里，林峰盯着淘汰名单上自己的名字。"
            "教练宣布了淘汰名单，林峰沉默地收拾行李。",
        )
        violations, _, _ = self._run(gutted, original=_script_draft())
        assert any(v.kind == "required_event_missing" for v in violations)
        # 被删除的关键事件（公园练球）出现在违规中
        assert any(v.target == "林峰在公园独自练球到深夜" for v in violations)

    def test_required_character_missing_fails(self) -> None:
        """大纲必需角色未出场时失败。"""
        no_zhang = _without_character(_script_draft(), "张德胜")
        violations, _, _ = self._run(no_zhang, original=_script_draft())
        assert any(
            v.kind == "required_character_missing" and v.target == "char_support_001"
            for v in violations
        )

    def test_locked_fact_removed_fails(self) -> None:
        """原稿中已有的锁定事实被移除 → 规则违规。"""
        fact = "林峰的身体素质不达标"
        original = _with_plain_text(
            _script_draft(),
            "教练说：林峰的身体素质不达标。他的传球角度却无懈可击。",
        )
        revised = _with_plain_text(
            _script_draft(),
            "教练说：林峰的传球角度无懈可击，是队里最出色的。",
        )
        violations, _, _ = self._run(revised, original=original, locked_facts=[fact])
        assert any(v.kind == "locked_fact_missing" and v.target == fact for v in violations)
        assert all(v.source == "rule" for v in violations)

    def test_locked_fact_not_in_original_not_flagged(self) -> None:
        """原稿中不存在的事实，规则不判缺失（由语义层兜底）。"""
        fact = "林峰获得联赛冠军"  # 与本集（第 1 集）内容无关，原稿不含
        revised = _replacing(_script_draft(), "公园", "酒吧")
        violations, _, _ = self._run(revised, original=_script_draft(), locked_facts=[fact])
        assert violations == []

    def test_slight_wording_change_no_violation(self) -> None:
        """轻微措辞改变不误判为事实丢失（验收项，规则层）。"""
        fact = "林峰的身体素质不达标"
        revised = _replacing(
            _script_draft(),
            "你的身体素质确实达不到我们的标准",
            "你的身体素质确实还差些火候，暂时达不到队里的标准",
        )
        violations, _, _ = self._run(revised, original=_script_draft(), locked_facts=[fact])
        assert violations == []

    def test_unmappable_character_id_warns_not_violates(self) -> None:
        """无法映射为姓名的角色 ID → 警告（非阻断），不误判为违规。"""
        outline = {**_ep1_outline(), "required_characters": ["char_ghost_999"]}
        violations, warnings, _ = ContinuityManager.run_rule_checks(
            episode_number=1,
            script_draft=_script_draft(),
            original_script_draft=_script_draft(),
            episode_outline=outline,
            story_bible=_story_bible(),
            locked_facts=_locked_facts(),
        )
        assert violations == []
        assert any(w.target == "char_ghost_999" for w in warnings)


# ========================================================================
# Schema 校验
# ========================================================================


def _violation(
    kind: str = "locked_fact_reversed",
    source: str = "semantic",
    target: str = "某事实",
) -> ContinuityViolation:
    return ContinuityViolation(
        kind=cast(Any, kind),
        target=target,
        expected="期望状态",
        actual="实际状态",
        evidence="第一场：台词证据。",
        source=cast(Any, source),
    )


def _warning(target: str = "某对象") -> ContinuityWarning:
    return ContinuityWarning(
        kind="semantic_inconsistency",
        target=target,
        message="轻微的时间线细节模糊",
        source="semantic",
    )


class TestContinuityCheckResultSchema:
    """ContinuityCheckResult 结构校验。"""

    def test_pass_without_violations(self) -> None:
        result = ContinuityCheckResult(
            status="pass", checked_episode_number=1,
            rule_checks_run=["required_events_present"],
        )
        assert result.status == "pass"
        assert result.violations == []

    def test_fail_with_violations(self) -> None:
        result = ContinuityCheckResult(
            status="fail", checked_episode_number=1,
            violations=[_violation()],
        )
        assert result.status == "fail"

    def test_pass_with_violations_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ContinuityCheckResult(
                status="pass", checked_episode_number=1, violations=[_violation()]
            )

    def test_fail_without_violations_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ContinuityCheckResult(
                status="fail", checked_episode_number=1,
            )

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ContinuityCheckResult.model_validate(
                {
                    "status": "pass",
                    "checked_episode_number": 1,
                    "extra_field": "x",
                }
            )

    def test_invalid_violation_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ContinuityCheckResult(
                status="fail", checked_episode_number=1,
                violations=[_violation(kind="bogus_kind")],
            )

    def test_invalid_violation_source_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _violation(source="llm")

    def test_serializable_for_diagnosis(self) -> None:
        """失败稿保存为 invalid/candidate 版本时，结果可序列化用于诊断。"""
        result = ContinuityCheckResult(
            status="fail", checked_episode_number=1,
            violations=[_violation()],
            warnings=[_warning()],
            rule_checks_run=["required_events_present"],
            semantic_checks_run=["locked_fact_reversal"],
        )
        dumped = json.loads(result.model_dump_json())
        assert dumped["status"] == "fail"
        assert dumped["violations"][0]["source"] == "semantic"
        assert dumped["warnings"][0]["message"]
        # 重新解析往返一致
        restored = ContinuityCheckResult.model_validate(dumped)
        assert restored == result


class TestContinuityCheckInputSchema:
    """ContinuityCheckInput 结构校验。"""

    def test_valid_input(self) -> None:
        check_input = _check_input()
        assert check_input.episode_number == 1
        assert check_input.locked_facts == _locked_facts()

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ContinuityCheckInput.model_validate(
                {**_check_input().model_dump(), "extra_field": "x"}
            )

    def test_original_optional(self) -> None:
        check_input = _check_input(original=None)
        assert check_input.original_script_draft is None


# ========================================================================
# 语义检查 Skill（FakeLLM）
# ========================================================================


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM(seed=42)


@pytest.fixture
def agent(fake_llm: FakeLLM) -> BaseAgent:
    return BaseAgent(name="reviser", llm=fake_llm)


@pytest.fixture
def prompt_loader() -> PromptLoader:
    return PromptLoader()


@pytest.fixture
def semantic_skill() -> ContinuitySemanticCheckSkill:
    return ContinuitySemanticCheckSkill()


@pytest.fixture
def check_skill() -> ContinuityCheckSkill:
    return ContinuityCheckSkill()


def _register_semantic(
    agent: BaseAgent,
    result: ContinuitySemanticCheck,
) -> None:
    cast(FakeLLM, agent.llm).register("continuity_semantic_check", result)


async def _semantic_execute(
    skill: ContinuitySemanticCheckSkill,
    agent: BaseAgent,
    prompt_loader: PromptLoader,
    check_input: ContinuityCheckInput | None = None,
) -> ContinuitySemanticCheck:
    return await skill.execute(
        {
            "input": check_input or _check_input(),
            "agent": agent,
            "prompt_loader": prompt_loader,
        }
    )


class TestContinuitySemanticCheckSkill:
    """ContinuitySemanticCheckSkill 行为（FakeLLM，确定性）。"""

    async def test_happy_path_returns_structured(
        self,
        semantic_skill: ContinuitySemanticCheckSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        _register_semantic(agent, ContinuitySemanticCheck(violations=[], warnings=[]))
        semantic = await _semantic_execute(semantic_skill, agent, prompt_loader)
        assert isinstance(semantic, ContinuitySemanticCheck)
        assert semantic.violations == []
        assert semantic.warnings == []

    async def test_source_forced_to_semantic(
        self,
        semantic_skill: ContinuitySemanticCheckSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        """LLM 自报 source 不信任——一律由服务端置为 semantic。"""
        llm_result = ContinuitySemanticCheck(
            violations=[_violation(source="rule")],
            warnings=[_warning()],
        )
        _register_semantic(agent, llm_result)
        semantic = await _semantic_execute(semantic_skill, agent, prompt_loader)
        assert all(v.source == "semantic" for v in semantic.violations)
        assert all(w.source == "semantic" for w in semantic.warnings)

    async def test_llm_failure_raises(
        self,
        semantic_skill: ContinuitySemanticCheckSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        cast(FakeLLM, agent.llm).inject_fault(1, "timeout")
        with pytest.raises(RuntimeError):
            await _semantic_execute(semantic_skill, agent, prompt_loader)


# ========================================================================
# 连续性检查 Skill（规则优先 + 必要语义）
# ========================================================================


class TestContinuityCheckSkill:
    """ContinuityCheckSkill 集成（FakeLLM，确定性）。"""

    async def _execute(
        self,
        check_skill: ContinuityCheckSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        check_input: ContinuityCheckInput | None = None,
    ) -> ContinuityCheckResult:
        return await check_skill.execute(
            {
                "input": check_input or _check_input(),
                "agent": agent,
                "prompt_loader": prompt_loader,
            }
        )

    async def test_happy_path_pass(
        self,
        check_skill: ContinuityCheckSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        """规则通过 + 语义无问题 → status=pass。"""
        _register_semantic(agent, ContinuitySemanticCheck(violations=[], warnings=[]))
        result = await self._execute(check_skill, agent, prompt_loader)
        assert result.status == "pass"
        assert result.violations == []
        assert result.semantic_checks_run == [
            "locked_fact_reversal",
            "character_state_change",
            "loop_consistency",
        ]

    async def test_rule_failure_short_circuits_llm(
        self,
        check_skill: ContinuityCheckSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        """规则失败 → 直接 fail，不调用语义 LLM（规则优先原则）。"""
        gutted = _with_plain_text(
            _script_draft(),
            "青训营的更衣室里，林峰盯着淘汰名单上自己的名字。"
            "教练宣布了淘汰名单，林峰沉默地收拾行李。",
        )
        check_input = _check_input(script=gutted, original=_script_draft())
        result = await self._execute(check_skill, agent, prompt_loader, check_input)

        assert result.status == "fail"
        assert any(v.kind == "required_event_missing" for v in result.violations)
        assert result.semantic_checks_run == []
        # 关键断言：没有发起任何 LLM 调用
        assert cast(FakeLLM, agent.llm).get_call_history() == []

    async def test_fixed_fact_reversed_fails(
        self,
        check_skill: ContinuityCheckSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        """固定事实被反转时失败（验收项，语义层发现）。"""
        fact = "林峰的身体素质不达标"
        original = _script_draft()  # 原稿含「你的身体素质确实达不到我们的标准」
        reversed_rev = _replacing(
            _script_draft(),
            "你的身体素质确实达不到我们的标准",
            "你的身体素质完全达标了，教练当场撤回了淘汰决定",
        )
        check_input = _check_input(
            script=reversed_rev, original=original, locked_facts=[fact]
        )
        _register_semantic(
            agent,
            ContinuitySemanticCheck(
                violations=[
                    _violation(kind="locked_fact_reversed", target=fact),
                ],
                warnings=[],
            ),
        )
        result = await self._execute(check_skill, agent, prompt_loader, check_input)

        assert result.status == "fail"
        assert result.violations[0].kind == "locked_fact_reversed"
        assert result.violations[0].target == fact
        assert result.violations[0].source == "semantic"

    async def test_warnings_separated_from_violations(
        self,
        check_skill: ContinuityCheckSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        """warnings 与 violations 分开（验收项）：仅有 warning 时 status=pass。"""
        _register_semantic(
            agent,
            ContinuitySemanticCheck(
                violations=[],
                warnings=[_warning(target="时间线细节")],
            ),
        )
        result = await self._execute(check_skill, agent, prompt_loader)
        assert result.status == "pass"
        assert result.violations == []
        assert len(result.warnings) == 1
        assert result.warnings[0].source == "semantic"

    async def test_character_state_violation_fails(
        self,
        check_skill: ContinuityCheckSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        """语义层发现关键人物状态变化矛盾 → fail。"""
        _register_semantic(
            agent,
            ContinuitySemanticCheck(
                violations=[
                    _violation(kind="character_state_change", target="char_protagonist_001"),
                ],
                warnings=[],
            ),
        )
        result = await self._execute(check_skill, agent, prompt_loader)
        assert result.status == "fail"
        assert result.violations[0].kind == "character_state_change"

    async def test_llm_failure_raises_when_semantic_needed(
        self,
        check_skill: ContinuityCheckSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        """规则通过后语义 LLM 失败 → RuntimeError。"""
        cast(FakeLLM, agent.llm).inject_fault(1, "timeout")
        with pytest.raises(RuntimeError):
            await self._execute(check_skill, agent, prompt_loader)

    async def test_result_serializable(
        self,
        check_skill: ContinuityCheckSkill,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
    ) -> None:
        """结果可序列化，随 invalid/candidate 稿持久化诊断。"""
        _register_semantic(
            agent,
            ContinuitySemanticCheck(
                violations=[_violation()],
                warnings=[_warning()],
            ),
        )
        result = await self._execute(check_skill, agent, prompt_loader)
        dumped = json.loads(result.model_dump_json())
        restored = ContinuityCheckResult.model_validate(dumped)
        assert restored == result
        assert restored.status == "fail"


# ========================================================================
# Tool 集成
# ========================================================================


class TestContinuityCheckTool:
    """ContinuityCheckTool 包装 run_rule_checks。"""

    async def test_tool_returns_dicts(self) -> None:
        tool = ContinuityCheckTool()
        out = await tool.execute(
            episode_number=1,
            script_draft=_script_draft(),
            original_script_draft=_script_draft(),
            episode_outline=_ep1_outline(),
            story_bible=_story_bible(),
            locked_facts=_locked_facts(),
        )
        assert out["violations"] == []
        assert out["warnings"] == []
        assert out["checks_run"] == [
            "locked_facts_preserved",
            "required_events_present",
            "required_characters_present",
        ]
