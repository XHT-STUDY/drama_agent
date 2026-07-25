"""SummarizerSkill — 剧集摘要生成技能 (C-06).

职责:
- 从 ScriptDraft + ContinuityState 生成结构化 EpisodeSummary
- 同时提取角色状态变化、伏笔更新、时间线事件
- 供 ContinuityManager 更新连续性状态

模块边界:
- Skill 只负责组装 Prompt、调用 LLM、校验结果
- 不直接访问 ORM、不操作前端
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any, cast

from app.agents.base import BaseAgent
from app.domain.continuity import EpisodeSummary, StoryLoop, TimelineEvent
from app.domain.summary import SummaryInput, SummaryOutput
from app.prompts.loader import PromptLoader
from app.skills.protocol import Skill, SkillMetadata

logger = logging.getLogger(__name__)


class SummarizerValidationError(Exception):
    """Summarizer 输出校验失败。"""


# ========================================================================
# SummarizerSkill
# ========================================================================


class SummarizerSkill(Skill):
    """剧集摘要生成 Skill。

    接收单集 ScriptDraft 和当前连续性状态，
    调用 LLM 生成 EpisodeSummary 和连续性更新数据。
    """

    metadata = SkillMetadata(
        name="summarize_episode",
        version="1.0",
        description="从单集剧本生成结构化摘要与连续性更新数据",
    )

    # ---- 公开 API ----

    async def execute(self, context: dict[str, Any]) -> SummaryOutput:
        """执行剧集摘要生成。

        context 必需键:
            input: SummaryInput — 剧本草稿 + 连续性状态
            agent: BaseAgent — 用于调用 LLM
            prompt_loader: PromptLoader — 用于加载 Prompt 模板

        Returns:
            校验通过的 SummaryOutput（含摘要 + 连续性更新数据）

        Raises:
            SummarizerValidationError: 输出校验失败
            RuntimeError: LLM 调用失败
        """
        sm_input: SummaryInput = context["input"]
        agent: BaseAgent = context["agent"]
        prompt_loader: PromptLoader = context["prompt_loader"]

        # 1. 加载并渲染 Prompt
        try:
            tpl = prompt_loader.get("summarize_episode")
        except KeyError as e:
            logger.error("Prompt 加载失败: %s", e)
            raise

        script_json = _json.dumps(
            sm_input.script_draft, ensure_ascii=False, indent=2
        )
        continuity_json = _json.dumps(
            sm_input.continuity_state, ensure_ascii=False, indent=2
        )

        rendered = tpl.render(
            episode_number=str(sm_input.episode_number),
            script_draft=script_json,
            continuity_state=continuity_json or "{}",
        )

        # 2. 调用 LLM 生成结构化输出
        messages: list[dict[str, str]] = [
            {"role": "user", "content": rendered},
        ]

        result = await agent.generate_structured(
            SummaryOutput,
            messages,
            prompt_name="summarize_episode",
            temperature=0.5,
        )

        if result.error_code or result.parsed is None:
            logger.error(
                "LLM 摘要生成失败: code=%s detail=%s",
                result.error_code,
                result.error_detail,
            )
            raise RuntimeError(
                f"Summarizer LLM 调用失败: {result.error_code} - {result.error_detail}"
            )

        output = cast(SummaryOutput, result.parsed)

        # 3. 后校验
        self._validate_output(output, sm_input.episode_number)

        return output

    # ---- 后校验 ----

    def _validate_output(self, output: SummaryOutput, expected_episode: int) -> None:
        """校验 SummaryOutput 的基本完整性。

        检查:
        - episode_number 匹配
        - summary 不为空
        - character_changes 条目有 character_id
        - new_loops 条目有 loop_id 和 description
        """
        errors: list[str] = []

        if output.episode_number != expected_episode:
            errors.append(
                f"episode_number 不匹配: 期望 {expected_episode}, 实际 {output.episode_number}"
            )

        if not output.summary.strip():
            errors.append("summary 为空")

        for i, change in enumerate(output.character_changes):
            if not change.get("character_id", "").strip():
                errors.append(f"character_changes[{i}] 缺少 character_id")

        for i, loop in enumerate(output.new_loops):
            if not loop.get("loop_id", "").strip():
                errors.append(f"new_loops[{i}] 缺少 loop_id")
            if not loop.get("description", "").strip():
                errors.append(f"new_loops[{i}] 缺少 description")

        if errors:
            msg = "Summarizer 校验失败:\n" + "\n".join(f"  - {e}" for e in errors)
            logger.error(msg)
            raise SummarizerValidationError(msg)


# ========================================================================
# 辅助函数
# ========================================================================


def summary_output_to_episode_summary(output: SummaryOutput) -> EpisodeSummary:
    """将 Summarizer LLM 输出转换为 EpisodeSummary 领域模型。

    用于 ContinuityManager 更新 ContinuityState。

    Args:
        output: SummarizerSkill 的输出

    Returns:
        对应的 EpisodeSummary 实例
    """
    return EpisodeSummary(
        episode_number=output.episode_number,
        summary=output.summary,
        key_events=output.key_events,
        ending_state=output.ending_state,
    )


def extract_new_story_loops(
    output: SummaryOutput,
    episode: int,
) -> list[StoryLoop]:
    """从 SummaryOutput 提取新 StoryLoop 列表。

    Args:
        output: SummarizerSkill 的输出
        episode: 当前集号

    Returns:
        StoryLoop 实例列表
    """
    loops: list[StoryLoop] = []
    for loop_data in output.new_loops:
        loops.append(
            StoryLoop(
                loop_id=loop_data["loop_id"],
                description=loop_data["description"],
                introduced_episode=episode,
                status="open",
            )
        )
    return loops


def extract_timeline_events(
    output: SummaryOutput,
    episode: int,
) -> list[TimelineEvent]:
    """从 SummaryOutput 提取 TimelineEvent 列表。

    Args:
        output: SummarizerSkill 的输出
        episode: 当前集号

    Returns:
        TimelineEvent 实例列表
    """
    events: list[TimelineEvent] = []
    for i, event_data in enumerate(output.timeline_events):
        events.append(
            TimelineEvent(
                event_id=event_data.get("event_id", f"tl_{episode}_{i + 1:03d}"),
                episode_number=episode,
                order_in_episode=event_data.get("order_in_episode", i + 1),
                description=event_data.get("description", ""),
            )
        )
    return events
