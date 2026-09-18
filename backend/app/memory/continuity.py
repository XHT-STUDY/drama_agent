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
import re
from typing import Any

from app.domain.continuity import (
    CharacterKnowledge,
    CharacterState,
    ContinuityState,
    EpisodeSummary,
    FactRecord,
    FuturePlan,
    PropRecord,
    RelationshipChange,
    StateBasis,
    StoryLoop,
    TimelineEvent,
)
from app.domain.revision import ContinuityViolation, ContinuityWarning
from app.domain.script import ScriptDraft
from app.domain.story_bible import StoryBible
from app.domain.summary import EpisodeDelta

logger = logging.getLogger(__name__)


class ContinuityManager:
    """跨集连续性状态管理器。

    管理从 StoryBible 到逐集更新后的完整连续性状态。
    所有更新方法返回新 ContinuityState(不可变语义)。
    """

    # ---- 初始状态创建 ----

    @staticmethod
    def create_initial_state(story_bible: StoryBible) -> ContinuityState:
        """从 StoryBible 创建初始 ContinuityState(v1 兼容入口)。

        自动填充:
        - locked_facts: 从 StoryBible.locked_facts 直接复制
        - open_loops: 从 StoryBible.open_loops 创建 StoryLoop 列表
        - character_states: 为每个角色创建初始 CharacterState
        - through_episode: 设为 0(尚未完成任何一集)

        Args:
            story_bible: 已生成的 StoryBible

        Returns:
            初始化后的 ContinuityState
        """
        # 从 StoryBible 提取 locked_facts
        locked_facts: list[str] = list(story_bible.locked_facts)

        # 从 StoryBible.open_loops 创建 StoryLoop(初始均为 open)
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

    @staticmethod
    def create_initial_state_v2(
        story_bible: StoryBible,
        *,
        basis: StateBasis,
    ) -> ContinuityState:
        """从 StoryBible 创建 v2 初始状态(绑定确切工作集)。

        角色/伏笔初始化规则与 v1 相同(bible 伏笔 loop_001… 稳定 ID),
        envelope 记录 basis;locked_facts 保持文本列表
        (与 facts(正文事实)分账——设定事实不冒充已发生事件)。
        """
        v1 = ContinuityManager.create_initial_state(story_bible)
        return ContinuityState(
            content_schema_version="2.0",
            basis=basis,
            through_episode=0,
            episode_summaries=[],
            open_loops=[loop.model_copy() for loop in v1.open_loops],
            resolved_loops=[],
            locked_facts=list(v1.locked_facts),
            character_states={
                cid: cs.model_copy() for cid, cs in v1.character_states.items()
            },
        )

    # ---- v2 typed delta reducer(M-03) ----

    @staticmethod
    def apply_episode_delta(
        state: ContinuityState,
        delta: EpisodeDelta,
        *,
        source_script_artifact_id: str,
        summary_artifact_id: str | None = None,
        assigned_ids: dict[str, str] | None = None,
    ) -> ContinuityState:
        """应用一集 typed delta,返回新状态(纯函数,不改入参)。

        服务端规则(MEMORY_DESIGN §7.4):
        - 新事实/伏笔/事件/道具缺省 ID 时按稳定规则分配
          (fact_{ep:02d}_{idx:02d} 等,不让各集都生成 loop_001);
          分配结果写入 assigned_ids(可选,供服务端校验/审计);
        - 事实不可变:首见记录来源,后续同 ID 不覆盖;
        - 角色知识只按 learned=True 增加,不删除;
        - 伏笔允许 open→resolved→open(重开);
        - 未来计划在 reveal_episode 当集(或之后)标记 revealed,
          且仅当该集正文确实经过本 reducer 应用。

        输出重新经 Pydantic 完整校验(不走 model_copy(update=...) 绕过)。
        引用合法性(角色存在、事实存在、伏笔 open)由调用方
        StoryStateService 在 reduce 前校验;本方法只做状态变换。
        """
        episode = delta.episode_number
        assigned = assigned_ids if assigned_ids is not None else {}

        facts: dict[str, FactRecord] = {k: v.model_copy() for k, v in state.facts.items()}
        new_fact_ids: list[str | None] = []
        for idx, fact in enumerate(delta.facts):
            if fact.fact_id:
                if fact.fact_id not in facts:
                    raise ValueError(f"facts 引用了不存在的事实 {fact.fact_id}")
                new_fact_ids.append(fact.fact_id)  # 既有事实不可变
                continue
            new_fid = f"fact_{episode:02d}_{idx + 1:02d}"
            while new_fid in facts:
                new_fid = new_fid + "x"
            assigned[fact.text] = new_fid
            new_fact_ids.append(new_fid)
            facts[new_fid] = FactRecord(
                text=fact.text,
                source_episode=episode,
                source_scene=fact.source_scene,
                source_artifact_id=source_script_artifact_id,
            )

        # 角色知识
        character_states = {
            cid: cs.model_copy(deep=True) for cid, cs in state.character_states.items()
        }
        for knowledge in delta.knowledge:
            if not knowledge.learned:
                continue
            kfid: str
            if knowledge.new_fact_index is not None:
                if not (0 <= knowledge.new_fact_index < len(new_fact_ids)):
                    raise ValueError(
                        f"knowledge.new_fact_index {knowledge.new_fact_index} "
                        "越界"
                    )
                kfid = str(new_fact_ids[knowledge.new_fact_index])
            else:
                kfid = knowledge.fact_id or ""
            if kfid and kfid not in facts and kfid not in new_fact_ids:
                raise ValueError(f"knowledge 引用了不存在的事实 {kfid}")
            cs = character_states.get(knowledge.character_id)
            if cs is None:
                raise ValueError(
                    f"knowledge 引用了未知角色 {knowledge.character_id}"
                )
            if not any(k.fact_id == kfid for k in cs.known_facts):
                cs.known_facts.append(
                    CharacterKnowledge(
                        fact_id=kfid,
                        learned_episode=episode,
                        source_scene=knowledge.source_scene,
                        source_artifact_id=source_script_artifact_id,
                    )
                )
                cs.last_updated_episode = episode

        # 伏笔
        open_loops = [loop.model_copy() for loop in state.open_loops]
        resolved_loops = [loop.model_copy() for loop in state.resolved_loops]
        resolved_now = set(delta.loops_resolved)
        for intro in delta.loops_introduced:
            existing = next(
                (lp for lp in open_loops + resolved_loops
                 if lp.loop_id == intro.loop_id),
                None,
            )
            if intro.loop_id and existing is not None:
                # 重开:resolved → open
                if existing.status == "resolved":
                    resolved_loops = [
                        lp for lp in resolved_loops if lp.loop_id != intro.loop_id
                    ]
                    open_loops.append(
                        existing.model_copy(
                            update={
                                "status": "open",
                                "resolved_episode": None,
                                "description": intro.description,
                                "source_artifact_id": source_script_artifact_id,
                            }
                        )
                    )
                continue
            lid: str
            if intro.loop_id:
                lid = intro.loop_id
            else:
                n = len([lp for lp in open_loops + resolved_loops
                         if lp.introduced_episode == episode]) + 1
                lid = f"loop_{episode:02d}_{n:02d}"
                while any(lp.loop_id == lid for lp in open_loops + resolved_loops):
                    n += 1
                    lid = f"loop_{episode:02d}_{n:02d}"
                assigned[intro.description] = lid
            open_loops.append(
                StoryLoop(
                    loop_id=lid,
                    description=intro.description,
                    introduced_episode=episode,
                    status="open",
                    source_artifact_id=source_script_artifact_id,
                )
            )
        if resolved_now:
            still_open: list[StoryLoop] = []
            for loop in open_loops:
                if loop.loop_id in resolved_now:
                    resolved_loops.append(
                        loop.model_copy(
                            update={
                                "status": "resolved",
                                "resolved_episode": episode,
                            }
                        )
                    )
                else:
                    still_open.append(loop)
            open_loops = still_open

        # 道具
        props = {k: v.model_copy() for k, v in state.props.items()}
        for idx, prop in enumerate(delta.props):
            if prop.prop_id:
                pid = prop.prop_id
            else:
                pid = f"prop_{episode:02d}_{idx + 1:02d}"
                while pid in props:
                    pid += "x"
                assigned[f"prop:{pid}"] = pid
            props[pid] = PropRecord(
                holder_character_id=prop.holder_character_id,
                from_character_id=prop.from_character_id,
                source_episode=episode,
                source_scene=prop.source_scene,
                source_artifact_id=source_script_artifact_id,
            )

        # 关系与时间线
        relationships = list(state.relationship_changes) + [
            RelationshipChange(
                from_character_id=r.from_character_id,
                to_character_id=r.to_character_id,
                episode_number=episode,
                before=r.before,
                after=r.after,
            )
            for r in delta.relationships
        ]
        timeline = list(state.timeline_events) + [
            TimelineEvent(
                event_id=(t.event_id or f"tl_{episode:02d}_{t.order_in_episode:03d}"),
                episode_number=episode,
                order_in_episode=t.order_in_episode,
                description=t.description,
                source_artifact_id=source_script_artifact_id,
            )
            for t in delta.timeline_events
        ]

        # 未来计划:新增 + 按集数揭示
        future_plans: list[FuturePlan] = []
        for existing_plan in state.future_plans:
            revealed = existing_plan.revealed or episode >= existing_plan.reveal_episode
            future_plans.append(
                existing_plan.model_copy(update={"revealed": revealed})
            )
        for plan in delta.future_plans:
            if not any(p.text == plan.text for p in future_plans):
                future_plans.append(
                    FuturePlan(
                        text=plan.text,
                        reveal_episode=plan.reveal_episode,
                        revealed=episode >= plan.reveal_episode,
                        source_episode=episode,
                    )
                )

        summary = EpisodeSummary(
            episode_number=episode,
            summary=delta.summary,
            key_events=delta.key_events,
            ending_state=delta.ending_state,
        )
        basis = state.basis.model_copy(deep=True) if state.basis else None
        if basis is not None:
            basis.script_artifact_ids[str(episode)] = source_script_artifact_id
            if summary_artifact_id:
                basis.episode_summary_artifact_ids[str(episode)] = summary_artifact_id

        # 全量重建 + Pydantic 完整校验(W3-02:禁止 model_copy 绕过验证)
        return ContinuityState(
            content_schema_version="2.0",
            basis=basis,
            through_episode=episode,
            episode_summaries=list(state.episode_summaries) + [summary],
            open_loops=open_loops,
            resolved_loops=resolved_loops,
            locked_facts=list(state.locked_facts),
            character_states=character_states,
            relationship_changes=relationships,
            timeline_events=timeline,
            facts=facts,
            props=props,
            future_plans=future_plans,
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

    # ---- 连续性规则检查（F-03） ----

    @staticmethod
    def run_rule_checks(
        *,
        episode_number: int,
        script_draft: ScriptDraft,
        original_script_draft: ScriptDraft | None,
        episode_outline: dict[str, Any],
        story_bible: dict[str, Any],
        locked_facts: list[str],
    ) -> tuple[list[ContinuityViolation], list[ContinuityWarning], list[str]]:
        """运行确定性规则检查（F-03 规则优先原则）。

        三类规则:
        1. locked_facts_preserved —— 仅当锁定事实在原稿中出现时，要求其
           在新稿中仍然保留（防止修订误删既有事实，容忍轻微措辞改变）;
        2. required_events_present —— 本集大纲 key_events 必须体现在新稿中;
        3. required_characters_present —— 本集大纲 required_characters
           必须在新稿场景中出场。

        Args:
            episode_number: 被检查的集号
            script_draft: 修订后的新稿
            original_script_draft: 修订前的原稿（None 时跳过锁定事实回归检查）
            episode_outline: 本集大纲 dict
            story_bible: StoryBible dict（角色 ID→名称）
            locked_facts: 本次修订必须遵守的锁定事实

        Returns:
            (violations, warnings, checks_run)
            violations 均为 source="rule"；任何规则违规都阻断（检查失败）。
        """
        violations: list[ContinuityViolation] = []
        warnings: list[ContinuityWarning] = []
        checks_run: list[str] = []

        revised_text = script_draft.plain_text
        original_text = original_script_draft.plain_text if original_script_draft else ""

        # 1. 锁定事实回归检查
        checks_run.append("locked_facts_preserved")
        if original_script_draft is not None:
            for fact in locked_facts:
                if not fact:
                    continue
                in_original, _ = fact_preserved_in_text(fact, original_text)
                if not in_original:
                    # 原稿就没有该事实 → 本集修订不涉及，规则不判缺失
                    continue
                in_revised, coverage = fact_preserved_in_text(fact, revised_text)
                if not in_revised:
                    violations.append(
                        ContinuityViolation(
                            kind="locked_fact_missing",
                            target=fact,
                            expected="原稿中已有的锁定事实应在修订稿中保留",
                            actual=f"修订稿中未找到该事实（内容字符覆盖率 {coverage:.0%}）",
                            evidence=_script_excerpt_for(revised_text, fact),
                            source="rule",
                        )
                    )

        # 2. 大纲关键事件检查
        checks_run.append("required_events_present")
        key_events = episode_outline.get("key_events") or []
        for event in key_events:
            if not event:
                continue
            in_revised, coverage = fact_preserved_in_text(str(event), revised_text)
            if not in_revised:
                violations.append(
                    ContinuityViolation(
                        kind="required_event_missing",
                        target=str(event),
                        expected="本集大纲声明的关键事件应体现在修订稿中",
                        actual=f"修订稿中未找到该事件（内容字符覆盖率 {coverage:.0%}）",
                        evidence=_script_excerpt_for(revised_text, str(event)),
                        source="rule",
                    )
                )

        # 3. 大纲必需角色检查
        checks_run.append("required_characters_present")
        scene_characters: set[str] = {
            c for scene in script_draft.scenes for c in scene.characters
        }
        required_ids = episode_outline.get("required_characters") or []
        for char_id in required_ids:
            if not char_id:
                continue
            name = character_name_by_id(story_bible, str(char_id))
            if name is None:
                warnings.append(
                    ContinuityWarning(
                        kind="required_character_missing",
                        target=str(char_id),
                        message=(
                            f"无法将角色 ID {char_id} 映射为姓名，"
                            "跳过确定性出场检查（由语义检查兜底）"
                        ),
                        source="rule",
                    )
                )
                continue
            if name not in scene_characters:
                violations.append(
                    ContinuityViolation(
                        kind="required_character_missing",
                        target=str(char_id),
                        expected=f"大纲要求的角色 {name}（{char_id}）应在修订稿中出场",
                        actual=f"修订稿场景中未出现角色「{name}」",
                        evidence="场景出场角色: " + ("、".join(sorted(scene_characters)) or "（无）"),
                        source="rule",
                    )
                )

        if violations:
            logger.warning(
                "第 %d 集规则检查发现 %d 个违规（含 %d 个锁定事实回归）",
                episode_number, len(violations),
                sum(1 for v in violations if v.kind == "locked_fact_missing"),
            )
        return violations, warnings, checks_run
# 连续性规则检查（F-03，确定性纯逻辑）
# ========================================================================
#
# 规则检查只做"确定能判"的判断，优先于语义检查（规则优先原则）:
# - 锁定事实回归: 原稿中存在的事实，新稿不得移除（容忍轻微措辞改变）;
# - 大纲关键事件: 本集大纲声明的 key_events 必须体现在新稿中;
# - 大纲必需角色: 本集大纲声明的 required_characters 必须出场。
# 反转 / 状态变化 / 伏笔一致性等需要语义判断的部分，由独立 Skill 检查。

# 内容字符覆盖率阈值（低于视为事实丢失）。
# 0.5 意味着允许接近一半的措辞调整，避免"轻微措辞改变被误判为事实丢失"。
_CONTENT_CHAR_MIN_COVERAGE = 0.5

# 中文停用词（不参与匹配的内容字符提取）。
# 只收录纯虚词 / 代词——避免误伤常见复合词（如「能」在「超能力」「能力」中，
# 「要」在「重要」「要求」中，「有」在「有效」「所有」中），
# 否则会把轻微措辞改变误判为事实丢失。
_CHECK_STOPWORDS = frozenset(
    {
        "是", "的", "了", "在", "和", "与", "及", "或", "把", "被", "让",
        "对", "从", "向", "而", "但", "却", "并", "也", "都", "就", "才",
        "只", "还", "又", "再", "这", "那", "你", "我", "他", "她", "它",
        "不", "没", "个", "之", "其", "所", "着", "过",
    }
)


def normalize_check_text(text: str) -> str:
    """去除空白与标点，仅保留汉字/字母/数字，用于连续性模糊匹配。

    轻微措辞改变（加入标点、调整虚词）不影响匹配。

    Args:
        text: 原文

    Returns:
        归一化后的匹配文本
    """
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)


def extract_content_chars(text: str) -> str:
    """提取用于匹配的内容字符（去除停用词后的字符序列）。

    中文无现成分词，这里按字符粒度过滤停用词——对"轻微措辞改变"
    足够宽容（换词不换义的虚词不参与覆盖度计算）。

    Args:
        text: 原文

    Returns:
        保序的内容字符序列
    """
    normalized = normalize_check_text(text)
    return "".join(ch for ch in normalized if ch not in _CHECK_STOPWORDS)


def fact_preserved_in_text(fact: str, text: str) -> tuple[bool, float]:
    """判断事实是否保留在剧本文本中（容忍轻微措辞改变）。

    判定规则（由宽松到严格）:
    1. 归一化后整段子串命中 → (True, 1.0)；
    2. 内容字符覆盖率 >= 阈值 → (True, coverage)；
    3. 否则 → (False, coverage)。

    内容字符覆盖率 = 文本中出现的"事实内容字符" / 事实全部内容字符。
    本函数只判断"是否仍出现"，不判断"是否被语义反转"——
    反转 / 矛盾由语义检查（独立 Skill）发现。

    Args:
        fact: 待检查的事实 / 事件描述
        text: 剧本文本（plain_text）

    Returns:
        (是否保留, 内容字符覆盖率)
    """
    fact_normalized = normalize_check_text(fact)
    if not fact_normalized:
        # 空事实无可判 → 视为保留（由语义层兜底），避免误判
        return True, 1.0

    text_normalized = normalize_check_text(text)
    if fact_normalized in text_normalized:
        return True, 1.0

    fact_chars = extract_content_chars(fact)
    if not fact_chars:
        # 全部由停用词构成的事实无法确定性判定 → 视为保留
        return True, 1.0

    text_chars = set(text_normalized)
    hit = sum(1 for ch in fact_chars if ch in text_chars)
    coverage = hit / len(fact_chars)
    return coverage >= _CONTENT_CHAR_MIN_COVERAGE, coverage


def character_name_by_id(story_bible: dict[str, Any], character_id: str) -> str | None:
    """通过 StoryBible 将角色 ID 映射为角色名。

    Args:
        story_bible: StoryBible 的 dict 表示
        character_id: 角色 ID（如 char_protagonist_001）

    Returns:
        角色名；找不到映射时返回 None。
    """
    profiles: list[dict[str, Any]] = []
    for key in ("protagonist", "antagonist"):
        ch = story_bible.get(key)
        if ch and isinstance(ch, dict):
            profiles.append(ch)
    supporting = story_bible.get("supporting_characters") or []
    profiles.extend(ch for ch in supporting if isinstance(ch, dict))

    for ch in profiles:
        if ch.get("character_id") == character_id:
            name = ch.get("name")
            if name:
                return str(name)
    return None


def _script_excerpt_for(text: str, target: str) -> str:
    """从剧本文本中提取与目标相关的简短片段作为违规证据。

    优先取覆盖目标内容字符的行；无命中时回退到首行前 80 字。

    Args:
        text: 剧本文本
        target: 目标事实 / 事件

    Returns:
        用于证据的文本片段（限长）
    """
    fact_chars = set(extract_content_chars(target))
    best = ""
    best_hit = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        hit = sum(1 for ch in fact_chars if ch in line)
        if hit > best_hit:
            best = line
            best_hit = hit
            if fact_chars and hit >= len(fact_chars):
                break
    if not best:
        best = text.strip()[:80]
    return best[:120]
