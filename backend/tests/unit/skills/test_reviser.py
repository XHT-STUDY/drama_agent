"""ReviserSkill 单元测试 (F-02).

覆盖验收:
- 新稿可被 ScriptDraft Schema 解析（RevisionResult.script_draft）
- episode_number 与 title 规则不被误改（服务端权威覆盖）
- 原稿 Artifact content 完全不变（Skill 不修改输入 draft）
- 每个 operation 有执行结果或未执行说明（normalize_executions 全覆盖）
- 新稿 source 包含原稿、评估、计划（source_* 权威回填）
- 在模型输入中显式列出 preserve 与禁止修改项（protection_block）
- 服务端重算文本指标（word_count / dialogue_ratio 覆盖 LLM 自报）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from app.agents.base import BaseAgent
from app.agents.revision import RevisionAgent
from app.domain.revision import (
    OperationExecution,
    RevisionOperation,
    RevisionPlan,
    RevisionResult,
    RevisionTaskInput,
    normalize_executions,
)
from app.domain.script import ScriptDraft
from app.llm.fake import FakeLLM
from app.prompts.loader import PromptLoader
from app.skills.registry import SkillRegistry
from app.skills.reviser import ReviserSkill, _build_protection_block
from app.tools.dialogue_ratio import DialogueRatioTool
from app.tools.word_count import WordCountTool

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"

_SID = UUID("00000000-0000-0000-0000-000000000010")
_EID = UUID("00000000-0000-0000-0000-000000000020")
_PID = UUID("00000000-0000-0000-0000-000000000030")


def _load_golden(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8")))


def _script_draft() -> ScriptDraft:
    return ScriptDraft.model_validate(_load_golden("script_draft_valid.json"))


def _revision_plan() -> RevisionPlan:
    return RevisionPlan.model_validate(_load_golden("revision_plan_valid.json"))


def _revised_result() -> RevisionResult:
    return RevisionResult.model_validate(_load_golden("revised_episode_football.json"))


def _story_bible() -> dict[str, Any]:
    return _load_golden("story_bible_valid.json")


def _ep1_outline() -> dict[str, Any]:
    data = _load_golden("outline_set_valid.json")
    return cast(dict[str, Any], data["episodes"][0])


def _task_input() -> RevisionTaskInput:
    return RevisionTaskInput(
        episode_number=1,
        script_draft=_script_draft(),
        revision_plan=_revision_plan(),
        story_bible=_story_bible(),
        episode_outline=_ep1_outline(),
        continuity_state="## 锁定事实\n- 林峰的核心天赋是战术视野，不是超能力",
        source_revision_plan_artifact_id=_PID,
    )


# ========================================================================
# 纯函数: normalize_executions
# ========================================================================


class TestNormalizeExecutions:
    """LLM 执行记录规范化（F-02 验收：每个 operation 有执行结果或未执行说明）。"""

    def _plan_ops(self) -> list[RevisionOperation]:
        return list(_revision_plan().operations)

    def _exec(self, operation_id: str, status: str = "applied") -> OperationExecution:
        return OperationExecution(
            operation_id=operation_id, status=cast(Any, status), note="执行说明"
        )

    def test_keeps_valid_in_plan_order(self) -> None:
        ops = self._plan_ops()
        execs = [self._exec("op_001")]
        assert normalize_executions(ops, execs) == execs

    def test_drops_hallucinated_operation_ids(self) -> None:
        ops = self._plan_ops()
        execs = [self._exec("op_001"), self._exec("op_ghost")]
        result = normalize_executions(ops, execs)
        assert [e.operation_id for e in result] == ["op_001"]

    def test_dedups_keep_first(self) -> None:
        """同 operation_id 去重，保留 LLM 输出中的第一条。"""
        ops = self._plan_ops()
        first = self._exec("op_001", "skipped")
        dup = self._exec("op_001", "applied")
        result = normalize_executions(ops, [first, dup])
        assert result == [first]

    def test_missing_filled_as_skipped(self) -> None:
        ops = self._plan_ops()
        result = normalize_executions(ops, [])
        assert len(result) == 1
        assert result[0].operation_id == "op_001"
        assert result[0].status == "skipped"
        assert result[0].note == "未提供执行说明，视为未执行"

    def test_empty_plan_returns_empty(self) -> None:
        assert normalize_executions([], [self._exec("op_ghost")]) == []

    def test_mixed_ordering_full_coverage(self) -> None:
        """多 operation 下: 剔除臆造 + 去重 + 补齐缺失 + 按计划顺序。"""
        ops = [
            RevisionOperation(operation_id="op_001", issue_ids=["iss_001"], instruction="改A"),
            RevisionOperation(operation_id="op_002", issue_ids=["iss_002"], instruction="改B"),
            RevisionOperation(operation_id="op_003", issue_ids=["iss_003"], instruction="改C"),
        ]
        execs = [
            self._exec("op_ghost"),   # 臆造 → 剔除
            self._exec("op_003", "applied"),
            self._exec("op_001", "partial"),
            self._exec("op_001", "skipped"),  # 重复 → 保留第一条
            # op_002 缺失 → 补齐 skipped
        ]
        result = normalize_executions(ops, execs)
        assert [(e.operation_id, e.status) for e in result] == [
            ("op_001", "partial"),
            ("op_002", "skipped"),
            ("op_003", "applied"),
        ]


# ========================================================================
# Schema 校验
# ========================================================================


class TestRevisionResultSchema:
    """RevisionResult / RevisionTaskInput 结构校验。"""

    def test_golden_parses(self) -> None:
        result = _revised_result()
        assert result.script_draft.episode_number == 1
        assert result.operation_executions[0].operation_id == "op_001"
        assert result.source_revision_plan_artifact_id == _PID

    def test_invalid_status_rejected(self) -> None:
        from pydantic import ValidationError

        data = _load_golden("revised_episode_football.json")
        data["operation_executions"][0]["status"] = "rewritten"
        with pytest.raises(ValidationError):
            RevisionResult.model_validate(data)

    def test_extra_field_forbidden(self) -> None:
        from pydantic import ValidationError

        data = _load_golden("revised_episode_football.json")
        data["extra_field"] = "x"
        with pytest.raises(ValidationError):
            RevisionResult.model_validate(data)

    def test_invalid_nested_script_rejected(self) -> None:
        from pydantic import ValidationError

        data = _load_golden("revised_episode_football.json")
        data["script_draft"]["title"] = ""  # title 必填 → 嵌套校验失败
        with pytest.raises(ValidationError):
            RevisionResult.model_validate(data)

    def test_task_input_valid(self) -> None:
        task = _task_input()
        assert task.episode_number == 1
        assert task.source_revision_plan_artifact_id == _PID

    def test_task_input_extra_field_forbidden(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RevisionTaskInput.model_validate(
                {**_task_input().model_dump(), "extra_field": "x"}
            )


# ========================================================================
# protection_block 组装
# ========================================================================


class TestProtectionBlock:
    """在模型输入中显式列出 preserve 与禁止修改项（F-02 验收项）。"""

    def test_lists_title_and_episode(self) -> None:
        block = _build_protection_block(_task_input())
        assert "被抛弃的天才" in block
        assert "episode_number" in block

    def test_lists_locked_facts(self) -> None:
        block = _build_protection_block(_task_input())
        assert "林峰的核心天赋是战术视野，不是超能力" in block
        assert "陈浩是林峰在青训时期的前队友" in block

    def test_lists_operation_preserve(self) -> None:
        block = _build_protection_block(_task_input())
        assert "林峰的核心性格特质（坚韧、不善言辞）" in block

    def test_lists_character_forbidden_changes(self) -> None:
        block = _build_protection_block(_task_input())
        assert "林峰不能主动使用暴力" in block


# ========================================================================
# ReviserSkill（FakeLLM）
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
def skill() -> ReviserSkill:
    return ReviserSkill()


@pytest.fixture
def skill_registry(skill: ReviserSkill) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(skill)
    return registry


@pytest.fixture
def revision_agent(agent: BaseAgent, skill_registry: SkillRegistry) -> RevisionAgent:
    return RevisionAgent(base_agent=agent, skill_registry=skill_registry)


def _register_rev(agent: BaseAgent, result: RevisionResult) -> None:
    cast(FakeLLM, agent.llm).register("revise_episode", result)


async def _execute(
    skill: ReviserSkill,
    agent: BaseAgent,
    prompt_loader: PromptLoader,
    task_input: RevisionTaskInput | None = None,
) -> RevisionResult:
    return await skill.execute(
        {
            "input": task_input or _task_input(),
            "agent": agent,
            "prompt_loader": prompt_loader,
        }
    )


async def _tool_metrics(draft: ScriptDraft) -> tuple[int, float]:
    """计算给定剧本的服务端确定性指标（用于断言覆盖）。"""
    wc = await WordCountTool().execute(plain_text=draft.plain_text)
    ratio = await DialogueRatioTool().execute(
        scenes=[s.model_dump() for s in draft.scenes], plain_text=draft.plain_text
    )
    return int(wc["chinese_chars_with_punct"]), float(ratio["dialogue_ratio"])


class TestReviserSkill:
    """ReviserSkill 行为测试（FakeLLM，确定性）。"""

    async def test_happy_path(
        self, skill: ReviserSkill, agent: BaseAgent, prompt_loader: PromptLoader
    ) -> None:
        """完整修订流程: 计划 → 完整新稿 + 全覆盖执行记录。"""
        _register_rev(agent, _revised_result())
        result = await _execute(skill, agent, prompt_loader)

        assert isinstance(result, RevisionResult)
        assert result.script_draft.episode_number == 1
        assert result.script_draft.title == "被抛弃的天才"
        assert len(result.script_draft.scenes) >= 2
        assert [e.operation_id for e in result.operation_executions] == ["op_001"]
        assert result.operation_executions[0].status == "applied"

    async def test_new_draft_parses_as_script_draft(
        self, skill: ReviserSkill, agent: BaseAgent, prompt_loader: PromptLoader
    ) -> None:
        """验收项: 新稿可被 ScriptDraft Schema 解析。"""
        _register_rev(agent, _revised_result())
        result = await _execute(skill, agent, prompt_loader)
        parsed = ScriptDraft.model_validate(result.script_draft.model_dump())
        assert parsed.title == result.script_draft.title

    async def test_episode_number_overridden(
        self, skill: ReviserSkill, agent: BaseAgent, prompt_loader: PromptLoader
    ) -> None:
        """验收项: episode_number 规则不被误改（LLM 谎报集号被覆盖）。"""
        rev = _revised_result()
        rev.script_draft.episode_number = 9
        _register_rev(agent, rev)
        result = await _execute(skill, agent, prompt_loader)
        assert result.script_draft.episode_number == 1

    async def test_title_overridden(
        self, skill: ReviserSkill, agent: BaseAgent, prompt_loader: PromptLoader
    ) -> None:
        """验收项: title 规则不被误改（LLM 改写标题被覆盖回原稿）。"""
        rev = _revised_result()
        rev.script_draft.title = "被恶意改写的标题"
        _register_rev(agent, rev)
        result = await _execute(skill, agent, prompt_loader)
        assert result.script_draft.title == "被抛弃的天才"

    async def test_outline_artifact_id_preserved(
        self, skill: ReviserSkill, agent: BaseAgent, prompt_loader: PromptLoader
    ) -> None:
        """referenced_outline_artifact_id 沿用原稿（修订不改大纲关联）。"""
        rev = _revised_result()
        rev.script_draft.referenced_outline_artifact_id = uuid4()
        _register_rev(agent, rev)
        result = await _execute(skill, agent, prompt_loader)
        assert (
            result.script_draft.referenced_outline_artifact_id
            == _script_draft().referenced_outline_artifact_id
        )

    async def test_word_count_recomputed(
        self, skill: ReviserSkill, agent: BaseAgent, prompt_loader: PromptLoader
    ) -> None:
        """验收项: 服务端重算文本指标（LLM 自报 word_count 被覆盖）。"""
        rev = _revised_result()
        rev.script_draft.word_count = 99999
        _register_rev(agent, rev)
        result = await _execute(skill, agent, prompt_loader)
        expected_wc, _ = await _tool_metrics(result.script_draft)
        assert result.script_draft.word_count == expected_wc
        assert result.script_draft.word_count != 99999

    async def test_dialogue_ratio_recomputed(
        self, skill: ReviserSkill, agent: BaseAgent, prompt_loader: PromptLoader
    ) -> None:
        """验收项: dialogue_ratio 被服务端工具重算。"""
        rev = _revised_result()
        rev.script_draft.dialogue_ratio = 0.999
        _register_rev(agent, rev)
        result = await _execute(skill, agent, prompt_loader)
        _, expected_ratio = await _tool_metrics(result.script_draft)
        assert abs(result.script_draft.dialogue_ratio - expected_ratio) < 0.001
        assert result.script_draft.dialogue_ratio != 0.999

    async def test_source_ids_authoritative(
        self, skill: ReviserSkill, agent: BaseAgent, prompt_loader: PromptLoader
    ) -> None:
        """验收项: 新稿 source 包含原稿、评估、计划（不信任 LLM 自报）。"""
        rev = _revised_result()
        rev.source_script_artifact_id = uuid4()
        rev.source_evaluation_artifact_id = uuid4()
        rev.source_revision_plan_artifact_id = uuid4()
        _register_rev(agent, rev)
        result = await _execute(skill, agent, prompt_loader)

        assert result.source_script_artifact_id == _SID
        assert result.source_evaluation_artifact_id == _EID
        assert result.source_revision_plan_artifact_id == _PID

    async def test_hallucinated_execution_dropped(
        self, skill: ReviserSkill, agent: BaseAgent, prompt_loader: PromptLoader
    ) -> None:
        """LLM 虚构不存在的 operation 执行记录被剔除。"""
        rev = _revised_result()
        rev.operation_executions = [
            OperationExecution(operation_id="op_ghost", status="applied", note="虚构"),
        ]
        _register_rev(agent, rev)
        result = await _execute(skill, agent, prompt_loader)
        assert [e.operation_id for e in result.operation_executions] == ["op_001"]

    async def test_missing_execution_filled_as_skipped(
        self, skill: ReviserSkill, agent: BaseAgent, prompt_loader: PromptLoader
    ) -> None:
        """验收项: 每个 operation 有执行结果或未执行说明（缺失自动补齐 skipped）。"""
        rev = _revised_result()
        rev.operation_executions = []
        _register_rev(agent, rev)
        result = await _execute(skill, agent, prompt_loader)
        assert len(result.operation_executions) == 1
        assert result.operation_executions[0].operation_id == "op_001"
        assert result.operation_executions[0].status == "skipped"
        assert result.operation_executions[0].note

    async def test_executions_follow_plan_order(
        self, skill: ReviserSkill, agent: BaseAgent, prompt_loader: PromptLoader
    ) -> None:
        """执行记录按计划 operation 顺序稳定输出。"""
        plan = _revision_plan()
        plan.operations.append(
            RevisionOperation(
                operation_id="op_002", target_scene_number=2,
                issue_ids=["iss_002"], instruction="强化第二场结尾张力",
            )
        )
        rev = _revised_result()
        rev.operation_executions = [
            OperationExecution(operation_id="op_002", status="applied", note="第二场"),
            OperationExecution(operation_id="op_001", status="partial", note="第一场"),
        ]
        _register_rev(agent, rev)
        task_input = _task_input().model_copy(update={"revision_plan": plan})
        result = await _execute(skill, agent, prompt_loader, task_input)
        assert [(e.operation_id, e.status) for e in result.operation_executions] == [
            ("op_001", "partial"),
            ("op_002", "applied"),
        ]

    async def test_original_draft_not_mutated(
        self, skill: ReviserSkill, agent: BaseAgent, prompt_loader: PromptLoader
    ) -> None:
        """验收项: 原稿 Artifact content 完全不变（输入 draft 不被修改）。"""
        original = _script_draft()
        snapshot = original.model_dump()
        task_input = _task_input()
        _register_rev(agent, _revised_result())
        await _execute(skill, agent, prompt_loader, task_input)

        assert original.model_dump() == snapshot
        assert task_input.script_draft.model_dump() == snapshot

    async def test_llm_failure_raises(
        self, skill: ReviserSkill, agent: BaseAgent, prompt_loader: PromptLoader
    ) -> None:
        """LLM 调用失败时抛出 RuntimeError。"""
        cast(FakeLLM, agent.llm).inject_fault(1, "timeout")
        with pytest.raises(RuntimeError):
            await _execute(skill, agent, prompt_loader)


# ========================================================================
# RevisionAgent 集成
# ========================================================================


class TestRevisionAgent:
    """RevisionAgent.revise_episode 集成测试。"""

    async def test_revise_episode(
        self,
        revision_agent: RevisionAgent,
        prompt_loader: PromptLoader,
        fake_llm: FakeLLM,
    ) -> None:
        """端到端: 原稿 + 计划 → 完整新稿 + 执行记录。"""
        _register_rev(revision_agent._agent, _revised_result())
        result = await revision_agent.revise_episode(
            script_draft=_script_draft(),
            revision_plan=_revision_plan(),
            story_bible=_story_bible(),
            episode_outline=_ep1_outline(),
            source_revision_plan_artifact_id=_PID,
            prompt_loader=prompt_loader,
            continuity_state="## 锁定事实\n- 林峰的核心天赋是战术视野，不是超能力",
        )

        assert isinstance(result, RevisionResult)
        assert result.script_draft.episode_number == 1
        assert result.operation_executions[0].operation_id == "op_001"
