"""OutlineReviserSkill 与大纲修订不变量单元测试（J-07）。

覆盖:
- happy path: FakeLLM 返回 golden 修订大纲 → 通过全部服务端不变量;
- 集数变化 / 集号不连续 / 引用不存在角色 → 带反馈重试，耗尽后
  OutlineRevisionValidationError（含可诊断错误）;
- locked_facts 被否定插入反转 → 校验失败;
- 输出契约: 只接受完整 EpisodeOutlineSet（patch 结构无法通过 Schema）。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel

from app.agents.base import BaseAgent
from app.domain.outline import EpisodeOutlineSet
from app.domain.outline_revision import (
    OutlineRevisionInput,
    check_locked_facts,
    collect_invariant_errors,
)
from app.llm.fake import FakeLLM
from app.llm.models import LLMCallResult
from app.prompts.loader import PromptLoader
from app.skills.outline_reviser import (
    OutlineReviserSkill,
    OutlineRevisionValidationError,
)

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"


def _load(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))
    )


def _old_outline() -> EpisodeOutlineSet:
    return EpisodeOutlineSet.model_validate(_load("outline_set_valid"))


def _story_bible() -> dict[str, Any]:
    return _load("story_bible_valid")


def _make_input(**overrides: Any) -> OutlineRevisionInput:
    data = {
        "old_outline": _old_outline(),
        "story_bible": _story_bible(),
        "user_constraints": ["第 3 集增加林峰与陈浩的正面冲突"],
        "source_outline_artifact_id": uuid.uuid4(),
    }
    data.update(overrides)
    return OutlineRevisionInput.model_validate(data)


class SequenceFakeLLM(FakeLLM):
    """按调用顺序依次返回夹具的 FakeLLM（重试恢复路径），并记录每次调用的消息。"""

    def __init__(self, sequence: list[BaseModel]) -> None:
        super().__init__(seed=42)
        self._sequence = list(sequence)
        self.captured_messages: list[list[dict[str, str]]] = []

    async def generate_structured(
        self,
        schema: type[BaseModel],
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> LLMCallResult:
        self.captured_messages.append([dict(m) for m in messages])
        if self._sequence:
            prompt_name = cast(str, kwargs.get("prompt_name", ""))
            self._registry[prompt_name] = self._sequence.pop(0)
        return await super().generate_structured(schema, messages, **kwargs)


# ========================================================================
# 服务端不变量
# ========================================================================


class TestInvariants:
    def test_valid_revision_passes_all_invariants(self) -> None:
        new = EpisodeOutlineSet.model_validate(_load("outline_revision_valid"))
        errors = collect_invariant_errors(
            old_outline=_old_outline(), new_outline=new, story_bible=_story_bible()
        )
        assert errors == []

    def test_episode_count_must_not_change(self) -> None:
        old = _old_outline()
        data = _load("outline_revision_valid")
        data["episodes"] = data["episodes"][:9]  # 删一集
        shorter = EpisodeOutlineSet.model_validate(data)
        errors = collect_invariant_errors(
            old_outline=old, new_outline=shorter, story_bible=_story_bible()
        )
        assert any("集数必须保持" in e for e in errors)

    def test_unknown_character_is_not_traceable(self) -> None:
        data = _load("outline_revision_valid")
        data["episodes"][0]["required_characters"] = ["ghost_char"]
        outline = EpisodeOutlineSet.model_validate(data)
        errors = collect_invariant_errors(
            old_outline=_old_outline(), new_outline=outline, story_bible=_story_bible()
        )
        assert any("ghost_char" in e for e in errors)

    def test_locked_fact_negation_insertion_is_reversed(self) -> None:
        data = _load("outline_revision_valid")
        # 锁定事实 "张德胜教练曾是职业球员，因伤退役" 的否定改写
        data["episodes"][0]["opening_hook"] = (
            "张德胜教练不再是职业球员，也从未因伤退役，他只是个普通老人。"
        )
        outline = EpisodeOutlineSet.model_validate(data)
        errors = check_locked_facts(
            outline, ["张德胜教练曾是职业球员，因伤退役"]
        )
        assert any("锁定事实疑似被反转" in e for e in errors)

    def test_locked_fact_restated_verbatim_is_not_reversed(self) -> None:
        data = _load("outline_revision_valid")
        data["episodes"][0]["opening_hook"] = (
            "张德胜教练曾是职业球员，因伤退役，如今在低级别球队执教。"
        )
        outline = EpisodeOutlineSet.model_validate(data)
        assert (
            check_locked_facts(outline, ["张德胜教练曾是职业球员，因伤退役"]) == []
        )


# ========================================================================
# Skill
# ========================================================================


class TestOutlineReviserSkill:
    async def test_happy_path_returns_full_revised_outline(self) -> None:
        """golden 修订大纲通过全部不变量，返回完整 EpisodeOutlineSet。"""
        llm = FakeLLM(seed=42)
        llm.register(
            "outline_reviser",
            EpisodeOutlineSet.model_validate(_load("outline_revision_valid")),
        )
        agent = BaseAgent(name="planner", llm=llm)

        result = await OutlineReviserSkill().execute(
            {"input": _make_input(), "agent": agent, "prompt_loader": PromptLoader()}
        )

        assert isinstance(result, EpisodeOutlineSet)
        assert len(result.episodes) == len(_old_outline().episodes)
        assert result.episodes[2].title == "试训风波：替补席上的暗流"

    async def test_invariant_violation_retries_with_feedback_then_succeeds(self) -> None:
        """集数变化 → 带反馈重试；第二次输出合法 → 成功，反馈消息含诊断。"""
        bad = EpisodeOutlineSet.model_validate(
            {**_load("outline_revision_valid"), "episodes": _load("outline_revision_valid")["episodes"][:9]}
        )
        good = EpisodeOutlineSet.model_validate(_load("outline_revision_valid"))
        llm = SequenceFakeLLM([bad, good])
        agent = BaseAgent(name="planner", llm=llm)

        result = await OutlineReviserSkill().execute(
            {"input": _make_input(), "agent": agent, "prompt_loader": PromptLoader()}
        )

        assert len(result.episodes) == 10
        # 第二次调用前追加了带不变量反馈的 system 消息
        assert len(llm.captured_messages) == 2
        feedback = llm.captured_messages[1][-1]
        assert feedback["role"] == "system"
        assert "集数必须保持" in feedback["content"]

    async def test_invariant_violation_exhausts_retries_with_diagnostics(self) -> None:
        """持续违反集数不变量 → 重试耗尽，错误包含可诊断详情。"""
        bad = EpisodeOutlineSet.model_validate(
            {**_load("outline_revision_valid"), "episodes": _load("outline_revision_valid")["episodes"][:8]}
        )
        llm = SequenceFakeLLM([bad, bad, bad])
        agent = BaseAgent(name="planner", llm=llm)

        with pytest.raises(OutlineRevisionValidationError) as exc_info:
            await OutlineReviserSkill().execute(
                {"input": _make_input(), "agent": agent, "prompt_loader": PromptLoader()}
            )
        assert "集数必须保持" in str(exc_info.value)

    async def test_patch_style_single_episode_output_is_rejected(self) -> None:
        """输出契约：patch（只输出变更集）过得了 Schema 但被集数不变量拒绝。"""
        patch = EpisodeOutlineSet.model_validate(
            {
                "episodes": [
                    {**_load("outline_revision_valid")["episodes"][2], "episode_number": 1}
                ],
                "arc_summary": _load("outline_revision_valid")["arc_summary"],
            }
        )
        llm = SequenceFakeLLM([patch, patch, patch])
        agent = BaseAgent(name="planner", llm=llm)

        with pytest.raises(OutlineRevisionValidationError) as exc_info:
            await OutlineReviserSkill().execute(
                {"input": _make_input(), "agent": agent, "prompt_loader": PromptLoader()}
            )
        assert "集数必须保持" in str(exc_info.value)
