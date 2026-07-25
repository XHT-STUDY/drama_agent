"""ContinuityManager — 跨集连续性状态管理 (C-06).

职责（见 DEV_PLAN C-06）：
- 从 StoryBible 创建初始 ContinuityState
- 每集完成后更新人物状态、伏笔、时间线
- locked_facts 只增不减（除非新版 StoryBible 显式修改）
- 为 ContextBuilder 提供当前连续性快照

模块边界：
- 纯领域逻辑操作，不访问数据库、不调用 LLM
"""

from __future__ import annotations

import logging
from typing import Any

from app.domain.continuity import (
    CharacterState,
    ContinuityState,
    EpisodeSummary,
    RelationshipChange,
    StoryLoop,
    TimelineEvent,
)
from app.domain.story_bible import StoryBible

logger = logging.getLogger(__name__)


class ContinuityManager:
    """跨集连续性状态管理器。

    管理从 StoryBible 到逐集更新后的完整连续性状态。
    所有更新方法返回新 ContinuityState（不可变语义）。
    """

    # ---- 初始状态创建 ----

    @staticmethod
    def create_initial_state(story_bible: StoryBible) -> ContinuityState:
        """从 StoryBible 创建初始 ContinuityState。

        自动填充:
        - locked_facts: 从 StoryBible.locked_facts 直接复制
        - open_loops: 从 StoryBible.open_loops 创建 StoryLoop 列表
        - character_states: 为每个角色创建初始 CharacterState
        - through_episode: 设为 0（尚未完成任何一集）

        Args:
            story_bible: 已生成的 StoryBible

        Returns:
            初始化后的 ContinuityState
        """
        # 从 StoryBible 提取 locked_facts
        locked_facts: list[str] = list(story_bible.locked_facts)

        # 从 StoryBible.open_loops 创建 StoryLoop（初始均为 open）
        open_loops: list[StoryLoop] = []
        for idx, loop_desc in enumerate(story_bible.open_loops):
            loop = StoryLoop(
                loop_id=f"loop_{idx + 1:03d}",
                description=loop_desc,
                introduced_episode=0,  # 0 表示在 StoryBible 中就已引入
                status="open",
            )
            open_loops.append(loop)

        # 为每个角色创建初始 CharacterState
        character_states: dict[str, CharacterState] = {}
        for char in [story_bible.protagonist, story_bible.antagonist]:
            character_states[char.character_id] = CharacterState(
                character_id=char.character_id,
                physical_state="正常",
                emotional_state="初始状态",
                current_goal=char.visible_goal,
                known_information=[],
                last_updated_episode=0,
            )
        for char in story_bible.supporting_characters:
            character_states[char.character_id] = CharacterState(
                character_id=char.character_id,
                physical_state="正常",
                emotional_state="初始状态",
                current_goal=char.visible_goal,
                known_information=[],
                last_updated_episode=0,
            )

        return ContinuityState(
            through_episode=0,
            locked_facts=locked_facts,
            open_loops=open_loops,
            episode_summaries=[],
            character_states=character_states,
        )

    # ---- 剧集后更新 ----

    @staticmethod
    def update_after_episode(
        state: ContinuityState,
        summary: EpisodeSummary,
        *,
        new_loops: list[StoryLoop] | None = None,
        resolved_loop_ids: list[str] | None = None,
        character_updates: dict[str, dict[str, Any]] | None = None,
        timeline_events: list[TimelineEvent] | None = None,
        relationship_changes: list[RelationshipChange] | None = None,
    ) -> ContinuityState:
        """剧集完成后更新连续性状态。

        所有更新返回新的 ContinuityState 实例，不修改原对象。

        Args:
            state: 当前连续性状态
            summary: 本集 EpisodeSummary
            new_loops: 本集新引入的 StoryLoop
            resolved_loop_ids: 本集回收的 loop_id 列表
            character_updates: 本集角色状态更新，key=character_id, value=更新字段
            timeline_events: 本集新增的时间线事件
            relationship_changes: 本集新增的关系变化

        Returns:
            更新后的 ContinuityState（新实例）
        """
        episode = summary.episode_number

        # 1. 追加剧集摘要
        new_summaries = list(state.episode_summaries) + [summary]

        # 2. 更新伏笔状态
        resolved_ids = set(resolved_loop_ids or [])
        updated_open: list[StoryLoop] = []
        updated_resolved: list[StoryLoop] = list(state.resolved_loops)

        for loop in state.open_loops:
            if loop.loop_id in resolved_ids:
                updated_resolved.append(
                    loop.model_copy(
                        update={"status": "resolved", "resolved_episode": episode}
                    )
                )
            else:
                updated_open.append(loop.model_copy())

        # 追加新引入的伏笔
        if new_loops:
            updated_open.extend(new_loops)

        # 3. 更新角色状态
        new_char_states = {
            cid: cs.model_copy() for cid, cs in state.character_states.items()
        }
        if character_updates:
            for char_id, updates in character_updates.items():
                if char_id in new_char_states:
                    updates_with_episode = {
                        **updates,
                        "last_updated_episode": episode,
                    }
                    new_char_states[char_id] = new_char_states[char_id].model_copy(
                        update=updates_with_episode
                    )

        # 4. 追加时间线事件
        new_timeline = list(state.timeline_events) + (timeline_events or [])

        # 5. 追加关系变化
        new_relationships = list(state.relationship_changes) + (relationship_changes or [])

        return ContinuityState(
            through_episode=episode,
            episode_summaries=new_summaries,
            open_loops=updated_open,
            resolved_loops=updated_resolved,
            locked_facts=list(state.locked_facts),
            character_states=new_char_states,
            relationship_changes=new_relationships,
            timeline_events=new_timeline,
        )

    # ---- locked facts 管理 ----

    @staticmethod
    def add_locked_facts(
        state: ContinuityState,
        new_facts: list[str],
    ) -> ContinuityState:
        """追加 locked_facts（只增不减原则）。

        仅在修订后新版 StoryBible 显式添加新事实时调用。
        去重防止重复添加。

        Args:
            state: 当前连续性状态
            new_facts: 待追加的锁定事实

        Returns:
            更新后的 ContinuityState
        """
        existing = set(state.locked_facts)
        unique_new = [f for f in new_facts if f not in existing]

        if not unique_new:
            return state

        return state.model_copy(
            update={"locked_facts": list(state.locked_facts) + unique_new}
        )

    @staticmethod
    def replace_locked_facts(
        state: ContinuityState,
        new_facts: list[str],
    ) -> ContinuityState:
        """完全替换 locked_facts（仅新版 StoryBible 触发）。

        仅在新版 StoryBible 存在且 locked_facts 与当前不同时调用。

        Args:
            state: 当前连续性状态
            new_facts: 新版 StoryBible 的 locked_facts

        Returns:
            更新后的 ContinuityState
        """
        return state.model_copy(update={"locked_facts": list(new_facts)})

    # ---- 连续性上下文生成 ----

    @staticmethod
    def get_context_for_episode(
        state: ContinuityState,
        episode: int,
    ) -> str:
        """为指定集生成连续性上下文文本。

        格式化为可在 Prompt 中使用的文本块，包含：
        - 前集摘要（仅已完成集）
        - 当前开放伏笔
        - 锁定事实
        - 角色当前状态

        Args:
            state: 当前连续性状态
            episode: 正在撰写的集号

        Returns:
            连续性上下文字符串
        """
        parts: list[str] = []

        # 前集摘要（仅 episode - 1 及之前的已完成集）
        prev_summaries = [
            s for s in state.episode_summaries if s.episode_number < episode
        ]
        if prev_summaries:
            parts.append("## 前集摘要")
            for s in sorted(prev_summaries, key=lambda x: x.episode_number):
                parts.append(f"### 第 {s.episode_number} 集")
                parts.append(s.summary)
                if s.key_events:
                    parts.append("**关键事件**: " + "；".join(s.key_events))
                parts.append("")

        # 当前开放伏笔
        open_loops = [loop for loop in state.open_loops if loop.status == "open"]
        if open_loops:
            parts.append("## 未闭合伏笔")
            for loop in open_loops:
                intro_ep = loop.introduced_episode
                intro_label = f"第 {intro_ep} 集" if intro_ep > 0 else "StoryBible"
                parts.append(f"- **[{loop.loop_id}]** {loop.description}（{intro_label}引入）")
            parts.append("")

        # 锁定事实
        if state.locked_facts:
            parts.append("## 锁定事实（不可修改）")
            for fact in state.locked_facts:
                parts.append(f"- {fact}")
            parts.append("")

        # 角色当前状态
        if state.character_states:
            parts.append("## 角色当前状态")
            for char_id, cs in state.character_states.items():
                parts.append(f"- **{char_id}**: {cs.emotional_state or '未知'}")
                if cs.current_goal:
                    parts.append(f"  当前目标: {cs.current_goal}")
                if cs.physical_state:
                    parts.append(f"  身体状态: {cs.physical_state}")
            parts.append("")

        return "\n".join(parts)

    # ---- 伏笔追踪 ----

    @staticmethod
    def get_loop_summary(state: ContinuityState) -> dict[str, Any]:
        """获取伏笔追踪摘要。

        Returns:
            {"total": int, "open": int, "resolved": int, "open_loops": [...], "resolved_loops": [...]}
        """
        return {
            "total": len(state.open_loops) + len(state.resolved_loops),
            "open": len(state.open_loops),
            "resolved": len(state.resolved_loops),
            "open_loops": [
                {
                    "loop_id": loop.loop_id,
                    "description": loop.description,
                    "introduced": loop.introduced_episode,
                }
                for loop in state.open_loops
            ],
            "resolved_loops": [
                {
                    "loop_id": loop.loop_id,
                    "description": loop.description,
                    "introduced": loop.introduced_episode,
                    "resolved": loop.resolved_episode,
                }
                for loop in state.resolved_loops
            ],
        }
