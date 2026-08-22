"""OutlineReviserSkill — 大纲修订技能（J-07）。

职责:
- 接收 OutlineRevisionInput（旧大纲、StoryBible、用户约束、source ID），
  一次输出修订后的完整 EpisodeOutlineSet（输出 Schema 决定不接受 patch）;
- 服务端不变量校验（collect_invariant_errors）：集数不变、集号连续、
  required_characters 可追溯、locked_facts 未被反转——失败带反馈重试，
  用尽后抛 OutlineRevisionValidationError（含全部可诊断错误）;
- 不写数据库——Artifact 持久化与版本落库由调用方（J-08 工作流）负责。
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any, cast
from uuid import UUID

from app.agents.base import BaseAgent
from app.domain.outline import EpisodeOutlineSet
from app.domain.outline_revision import (
    OutlineRevisionInput,
    collect_invariant_errors,
)
from app.prompts.loader import PromptLoader
from app.skills.protocol import Skill, SkillMetadata

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2  # 结构/不变量校验失败的最大重试次数


class OutlineRevisionValidationError(Exception):
    """大纲修订后校验失败——输出不满足服务端不变量。"""


class OutlineReviserSkill(Skill):
    """按用户约束输出修订后的完整 EpisodeOutlineSet。"""

    metadata = SkillMetadata(
        name="outline_reviser",
        version="1.0",
        description="按用户约束修订分集大纲，输出完整 EpisodeOutlineSet（不接受 patch）",
    )

    async def execute(self, context: dict[str, Any]) -> EpisodeOutlineSet:
        """执行大纲修订。

        context 必需键:
            input: OutlineRevisionInput
            agent: BaseAgent
            prompt_loader: PromptLoader

        Returns:
            通过服务端不变量的完整 EpisodeOutlineSet。

        Raises:
            OutlineRevisionValidationError: 不变量校验失败（重试已用尽）。
            RuntimeError: LLM 调用失败（重试已用尽）。
        """
        revision_input: OutlineRevisionInput = context["input"]
        if not isinstance(revision_input, OutlineRevisionInput):
            revision_input = OutlineRevisionInput.model_validate(revision_input)
        agent: BaseAgent = context["agent"]
        prompt_loader: PromptLoader = context["prompt_loader"]

        tpl = prompt_loader.get("outline_reviser")
        episode_count = str(len(revision_input.old_outline.episodes))
        locked_facts = [str(f) for f in (revision_input.story_bible.get("locked_facts") or [])]
        constraints = (
            "\n".join(f"{i}. {c}" for i, c in enumerate(revision_input.user_constraints, 1))
            or "(无显式约束，请在保持不变量的前提下最小化修订)"
        )
        rendered = tpl.render(
            episode_count=episode_count,
            old_outline=_json.dumps(
                revision_input.old_outline.model_dump(mode="json"), ensure_ascii=False, indent=2
            ),
            story_bible=_json.dumps(
                revision_input.story_bible, ensure_ascii=False, indent=2
            ),
            user_constraints=constraints,
            locked_facts="\n".join(f"- {f}" for f in locked_facts) or "(无)",
            source_outline_artifact_id=str(revision_input.source_outline_artifact_id),
        )

        messages: list[dict[str, str]] = [{"role": "user", "content": rendered}]
        last_error_detail = ""
        result_set: EpisodeOutlineSet | None = None

        for attempt in range(1, _MAX_RETRIES + 2):
            result = await agent.generate_structured(
                EpisodeOutlineSet,
                messages,
                prompt_name="outline_reviser",
                temperature=0.5,
            )
            if result.error_code or result.parsed is None:
                last_error_detail = f"code={result.error_code} detail={result.error_detail}"
                logger.warning(
                    "OutlineReviser LLM 调用失败 (attempt %d/%d): %s",
                    attempt, _MAX_RETRIES + 1, last_error_detail,
                )
                if attempt <= _MAX_RETRIES:
                    messages.append({
                        "role": "system",
                        "content": (
                            f"前一次生成失败: {result.error_detail}。"
                            "请输出合法的完整 EpisodeOutlineSet JSON。"
                        ),
                    })
                    continue
                raise RuntimeError(
                    f"OutlineReviser LLM 调用失败（已重试 {_MAX_RETRIES} 次）: {last_error_detail}"
                )

            candidate = cast(EpisodeOutlineSet, result.parsed)
            invariant_errors = collect_invariant_errors(
                old_outline=revision_input.old_outline,
                new_outline=candidate,
                story_bible=revision_input.story_bible,
            )
            if invariant_errors:
                last_error_detail = "\n".join(invariant_errors)
                logger.warning(
                    "OutlineReviser 不变量校验失败 (attempt %d/%d):\n%s",
                    attempt, _MAX_RETRIES + 1, last_error_detail,
                )
                if attempt <= _MAX_RETRIES:
                    messages.append({
                        "role": "system",
                        "content": (
                            "前一次输出违反以下修订不变量:\n"
                            + "\n".join(f"  - {e}" for e in invariant_errors)
                            + "\n请修正后重新输出完整的 EpisodeOutlineSet JSON。"
                        ),
                    })
                    continue
                raise OutlineRevisionValidationError(
                    f"大纲修订不变量校验失败（已重试 {_MAX_RETRIES} 次）:\n"
                    + "\n".join(f"  - {e}" for e in invariant_errors)
                )

            result_set = candidate
            break

        assert result_set is not None, "不变量校验通过后必有合法大纲"
        return result_set


def build_outline_revision_input(
    *,
    old_outline: EpisodeOutlineSet,
    story_bible: dict[str, Any],
    user_constraints: list[str],
    source_outline_artifact_id: UUID | str,
) -> OutlineRevisionInput:
    """构造 OutlineRevisionInput 的便捷工厂（source ID 接受 str/UUID）。"""
    return OutlineRevisionInput(
        old_outline=old_outline,
        story_bible=story_bible,
        user_constraints=user_constraints,
        source_outline_artifact_id=(
            source_outline_artifact_id
            if isinstance(source_outline_artifact_id, UUID)
            else UUID(str(source_outline_artifact_id))
        ),
    )
