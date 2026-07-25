"""ContinuityManager 单元测试 (C-06).

测试范围:
- 从 StoryBible 创建初始 ContinuityState
- 剧集后更新（摘要/伏笔/角色/时间线）
- locked_facts 只增不减
- 连续性上下文文本生成
- 伏笔追踪摘要
"""

from __future__ import annotations

import pytest

from app.domain.continuity import (
    ContinuityState,
    EpisodeSummary,
    RelationshipChange,
    StoryLoop,
    TimelineEvent,
)
from app.domain.story_bible import CharacterProfile, StoryBible
from app.memory.continuity import ContinuityManager

# ========================================================================
# Fixtures
# ========================================================================


def _make_story_bible() -> StoryBible:
    """创建测试用 StoryBible（足球少年主题）。"""
    return StoryBible(
        title="足球少年",
        logline="一个被青训队抛弃的足球少年逆袭",
        genre="都市/逆袭",
        tone=["热血", "励志"],
        world_setting="中国都市足球青训圈",
        protagonist=CharacterProfile(
            character_id="char_lin_feng",
            name="林峰",
            role="主角",
            visible_goal="成为职业足球运动员",
            hidden_need="被父亲认可",
            traits=["坚韧", "天赋异禀"],
            strengths=["速度", "盘带技术"],
            flaws=["过于自信", "不信任队友"],
        ),
        antagonist=CharacterProfile(
            character_id="char_chen_coach",
            name="陈教练",
            role="反派",
            visible_goal="维护自己的权威",
            hidden_need="掩盖自己过去的失败",
            traits=["严厉", "固执"],
            strengths=["战术经验丰富"],
            flaws=["不承认错误", "记仇"],
        ),
        supporting_characters=[
            CharacterProfile(
                character_id="char_wang_li",
                name="王丽",
                role="配角",
                visible_goal="帮助林峰重返球场",
                traits=["善良", "机智"],
                strengths=["沟通能力"],
                flaws=["过于理想化"],
            ),
        ],
        main_conflict="天赋被埋没后重新证明自己",
        stakes="失去足球生涯和家人的信任",
        locked_facts=[
            "林峰的父亲是前国脚，因伤退役",
            "陈教练曾因战术错误导致球队降级",
            "林峰右膝有旧伤",
        ],
        open_loops=[
            "林峰父亲是否支持他重返球场",
            "陈教练过去的失败如何被揭露",
        ],
    )


@pytest.fixture
def story_bible() -> StoryBible:
    return _make_story_bible()


# ========================================================================
# 初始状态创建
# ========================================================================


class TestCreateInitialState:
    """从 StoryBible 创建初始 ContinuityState。"""

    def test_creates_from_story_bible(self, story_bible: StoryBible) -> None:
        """从 StoryBible 正确创建初始状态。"""
        state = ContinuityManager.create_initial_state(story_bible)

        assert state.through_episode == 0
        assert len(state.episode_summaries) == 0
        assert len(state.timeline_events) == 0
        assert len(state.relationship_changes) == 0

    def test_copies_locked_facts(self, story_bible: StoryBible) -> None:
        """locked_facts 从 StoryBible 直接复制。"""
        state = ContinuityManager.create_initial_state(story_bible)

        assert len(state.locked_facts) == 3
        assert "林峰的父亲是前国脚，因伤退役" in state.locked_facts
        assert "陈教练曾因战术错误导致球队降级" in state.locked_facts
        assert "林峰右膝有旧伤" in state.locked_facts

    def test_creates_story_loops_from_story_bible(self, story_bible: StoryBible) -> None:
        """StoryBible.open_loops 转换为 StoryLoop 列表。"""
        state = ContinuityManager.create_initial_state(story_bible)

        assert len(state.open_loops) == 2
        assert len(state.resolved_loops) == 0

        # 验证 loop 属性
        first_loop = state.open_loops[0]
        assert first_loop.loop_id == "loop_001"
        assert first_loop.status == "open"
        assert first_loop.introduced_episode == 0  # StoryBible 阶段
        assert "林峰父亲" in first_loop.description

    def test_creates_character_states(self, story_bible: StoryBible) -> None:
        """为所有角色创建初始 CharacterState。"""
        state = ContinuityManager.create_initial_state(story_bible)

        assert len(state.character_states) == 3  # 主角 + 反派 + 配角
        assert "char_lin_feng" in state.character_states
        assert "char_chen_coach" in state.character_states
        assert "char_wang_li" in state.character_states

        lin_feng = state.character_states["char_lin_feng"]
        assert lin_feng.emotional_state == "初始状态"
        assert lin_feng.current_goal == "成为职业足球运动员"
        assert lin_feng.last_updated_episode == 0

    def test_resolved_loops_empty_initially(self, story_bible: StoryBible) -> None:
        """初始时 resolved_loops 为空。"""
        state = ContinuityManager.create_initial_state(story_bible)
        assert len(state.resolved_loops) == 0


# ========================================================================
# 剧集后更新
# ========================================================================


class TestUpdateAfterEpisode:
    """剧集完成后更新连续性状态。"""

    @pytest.fixture
    def initial_state(self, story_bible: StoryBible) -> ContinuityState:
        return ContinuityManager.create_initial_state(story_bible)

    def test_updates_through_episode(self, initial_state: ContinuityState) -> None:
        """更新后 through_episode 应递进。"""
        summary = EpisodeSummary(
            episode_number=1,
            summary="第 1 集摘要",
            key_events=["林峰被开除", "偶遇恩师"],
            ending_state="林峰重新燃起希望",
        )
        new_state = ContinuityManager.update_after_episode(initial_state, summary)

        assert new_state.through_episode == 1
        assert len(new_state.episode_summaries) == 1
        assert new_state.episode_summaries[0].summary == "第 1 集摘要"

    def test_appends_multiple_summaries(self, initial_state: ContinuityState) -> None:
        """多集摘要按顺序追加。"""
        s1 = EpisodeSummary(episode_number=1, summary="S1", key_events=[], ending_state="")
        s2 = EpisodeSummary(episode_number=2, summary="S2", key_events=[], ending_state="")

        state = ContinuityManager.update_after_episode(initial_state, s1)
        state = ContinuityManager.update_after_episode(state, s2)

        assert state.through_episode == 2
        assert len(state.episode_summaries) == 2
        assert [s.summary for s in state.episode_summaries] == ["S1", "S2"]

    def test_resolves_loops(self, initial_state: ContinuityState) -> None:
        """标记伏笔为已回收。"""
        summary = EpisodeSummary(episode_number=1, summary="S1", key_events=[], ending_state="")

        new_state = ContinuityManager.update_after_episode(
            initial_state, summary,
            resolved_loop_ids=["loop_001"],
        )

        # loop_001 应移到 resolved
        assert len(new_state.open_loops) == 1  # 只剩 loop_002
        assert len(new_state.resolved_loops) == 1
        assert new_state.resolved_loops[0].loop_id == "loop_001"
        assert new_state.resolved_loops[0].status == "resolved"
        assert new_state.resolved_loops[0].resolved_episode == 1

    def test_adds_new_loops(self, initial_state: ContinuityState) -> None:
        """追加新引入的伏笔。"""
        summary = EpisodeSummary(episode_number=1, summary="S1", key_events=[], ending_state="")
        new_loop = StoryLoop(
            loop_id="loop_010",
            description="新对手出现",
            introduced_episode=1,
            status="open",
        )

        new_state = ContinuityManager.update_after_episode(
            initial_state, summary,
            new_loops=[new_loop],
        )

        assert len(new_state.open_loops) == 3  # 原 2 个 + 新 1 个
        assert any(loop.loop_id == "loop_010" for loop in new_state.open_loops)

    def test_updates_character_states(self, initial_state: ContinuityState) -> None:
        """更新角色状态字段。"""
        summary = EpisodeSummary(episode_number=1, summary="S1", key_events=[], ending_state="")

        new_state = ContinuityManager.update_after_episode(
            initial_state, summary,
            character_updates={
                "char_lin_feng": {
                    "emotional_state": "愤怒",
                    "current_goal": "找到新球队",
                },
            },
        )

        lin_feng = new_state.character_states["char_lin_feng"]
        assert lin_feng.emotional_state == "愤怒"
        assert lin_feng.current_goal == "找到新球队"
        assert lin_feng.last_updated_episode == 1

        # 未更新的角色保持不变
        coach = new_state.character_states["char_chen_coach"]
        assert coach.emotional_state == "初始状态"
        assert coach.last_updated_episode == 0

    def test_adds_timeline_events(self, initial_state: ContinuityState) -> None:
        """追加时间线事件。"""
        summary = EpisodeSummary(episode_number=1, summary="S1", key_events=[], ending_state="")
        events = [
            TimelineEvent(
                event_id="tl_1_001",
                episode_number=1,
                order_in_episode=1,
                description="林峰在训练中表现出色",
            ),
            TimelineEvent(
                event_id="tl_1_002",
                episode_number=1,
                order_in_episode=2,
                description="陈教练宣布开除决定",
            ),
        ]

        new_state = ContinuityManager.update_after_episode(
            initial_state, summary,
            timeline_events=events,
        )

        assert len(new_state.timeline_events) == 2
        assert new_state.timeline_events[0].event_id == "tl_1_001"
        assert new_state.timeline_events[1].event_id == "tl_1_002"

    def test_adds_relationship_changes(self, initial_state: ContinuityState) -> None:
        """追加关系变化记录。"""
        summary = EpisodeSummary(episode_number=1, summary="S1", key_events=[], ending_state="")
        changes = [
            RelationshipChange(
                from_character_id="char_lin_feng",
                to_character_id="char_chen_coach",
                episode_number=1,
                before="师徒",
                after="敌对",
            ),
        ]

        new_state = ContinuityManager.update_after_episode(
            initial_state, summary,
            relationship_changes=changes,
        )

        assert len(new_state.relationship_changes) == 1
        assert new_state.relationship_changes[0].after == "敌对"


# ========================================================================
# locked_facts 管理
# ========================================================================


class TestLockedFacts:
    """locked_facts 只增不减。"""

    @pytest.fixture
    def state(self, story_bible: StoryBible) -> ContinuityState:
        return ContinuityManager.create_initial_state(story_bible)

    def test_add_locked_facts_only_append(self, state: ContinuityState) -> None:
        """add_locked_facts 只追加不删除已有。"""
        original_count = len(state.locked_facts)
        new_state = ContinuityManager.add_locked_facts(
            state, ["新事实 A", "新事实 B"],
        )

        assert len(new_state.locked_facts) == original_count + 2
        assert "新事实 A" in new_state.locked_facts
        assert "新事实 B" in new_state.locked_facts
        # 原有事实保留
        assert "林峰右膝有旧伤" in new_state.locked_facts

    def test_add_locked_facts_dedup(self, state: ContinuityState) -> None:
        """重复事实不追加。"""
        new_state = ContinuityManager.add_locked_facts(
            state, ["林峰右膝有旧伤", "新事实"],
        )

        assert len(new_state.locked_facts) == 4  # 3 + 1（去重）
        assert "新事实" in new_state.locked_facts

    def test_replace_locked_facts(self, state: ContinuityState) -> None:
        """replace_locked_facts 完全替换（仅新版 StoryBible）。"""
        new_facts = ["新事实 X", "新事实 Y"]
        new_state = ContinuityManager.replace_locked_facts(state, new_facts)

        assert new_state.locked_facts == new_facts
        assert "林峰右膝有旧伤" not in new_state.locked_facts


# ========================================================================
# 连续性上下文文本生成
# ========================================================================


class TestGetContextForEpisode:
    """为指定集生成连续性上下文文本。"""

    @pytest.fixture
    def state_with_2_eps(self, story_bible: StoryBible) -> ContinuityState:
        """创建已通过 2 集的 ContinuityState。"""
        state = ContinuityManager.create_initial_state(story_bible)

        s1 = EpisodeSummary(
            episode_number=1,
            summary="林峰被球队开除，偶遇前国脚父亲的好友赵指导。",
            key_events=["林峰被开除", "偶遇赵指导"],
            ending_state="林峰决心重新开始",
        )
        state = ContinuityManager.update_after_episode(state, s1)

        s2 = EpisodeSummary(
            episode_number=2,
            summary="林峰加入业余球队，在首场训练赛中崭露头角。",
            key_events=["林峰加入新球队", "首场训练赛"],
            ending_state="林峰初步获得队友认可",
        )
        state = ContinuityManager.update_after_episode(state, s2)

        return state

    def test_generates_context_for_episode_3(self, state_with_2_eps: ContinuityState) -> None:
        """生成第 3 集时读取前两集摘要。"""
        context = ContinuityManager.get_context_for_episode(state_with_2_eps, 3)

        # 应包含前 2 集摘要
        assert "前集摘要" in context
        assert "第 1 集" in context
        assert "第 2 集" in context
        assert "林峰被球队开除" in context
        assert "林峰加入业余球队" in context

        # 应包含关键事件
        assert "林峰被开除" in context
        assert "林峰加入新球队" in context

    def test_excludes_own_episode(self, state_with_2_eps: ContinuityState) -> None:
        """生成第 2 集时只读第 1 集摘要。"""
        context = ContinuityManager.get_context_for_episode(state_with_2_eps, 2)

        assert "第 1 集" in context
        assert "第 2 集" not in context  # 不包含自己

    def test_includes_open_loops(self, state_with_2_eps: ContinuityState) -> None:
        """上下文包含未闭合伏笔。"""
        context = ContinuityManager.get_context_for_episode(state_with_2_eps, 1)

        assert "未闭合伏笔" in context
        assert "loop_001" in context
        assert "loop_002" in context

    def test_includes_locked_facts(self, state_with_2_eps: ContinuityState) -> None:
        """上下文包含锁定事实。"""
        context = ContinuityManager.get_context_for_episode(state_with_2_eps, 1)

        assert "锁定事实" in context
        assert "林峰的父亲是前国脚" in context

    def test_includes_character_states(self, state_with_2_eps: ContinuityState) -> None:
        """上下文包含角色当前状态。"""
        context = ContinuityManager.get_context_for_episode(state_with_2_eps, 1)

        assert "角色当前状态" in context
        assert "char_lin_feng" in context
        assert "成为职业足球运动员" in context

    def test_episode_1_no_previous_summary(self, state_with_2_eps: ContinuityState) -> None:
        """第 1 集没有前集摘要（through_episode=0 时）。"""
        # 创建一个仅有初始状态的新 state
        # 用别名避免与顶层导入冲突
        import app.domain.story_bible as _sb

        sb = _sb.StoryBible(
            title="测试",
            logline="测试",
            genre="测试",
            world_setting="测试",
            protagonist=_sb.CharacterProfile(
                character_id="p1", name="主角", role="主角", visible_goal="目标1",
            ),
            antagonist=_sb.CharacterProfile(
                character_id="a1", name="反派", role="反派", visible_goal="目标2",
            ),
            main_conflict="冲突",
            stakes="代价",
        )
        state = ContinuityManager.create_initial_state(sb)

        context = ContinuityManager.get_context_for_episode(state, 1)
        # 第 1 集不应包含"前集摘要"
        assert "前集摘要" not in context


# ========================================================================
# 伏笔追踪摘要
# ========================================================================


class TestGetLoopSummary:
    """伏笔追踪摘要。"""

    def test_returns_loop_statistics(self, story_bible: StoryBible) -> None:
        """返回开放/回收伏笔统计。"""
        state = ContinuityManager.create_initial_state(story_bible)
        result = ContinuityManager.get_loop_summary(state)

        assert result["total"] == 2
        assert result["open"] == 2
        assert result["resolved"] == 0
        assert len(result["open_loops"]) == 2
        assert len(result["resolved_loops"]) == 0

    def test_reflects_resolved_loops(self, story_bible: StoryBible) -> None:
        """回收伏笔后统计更新。"""
        state = ContinuityManager.create_initial_state(story_bible)
        summary = EpisodeSummary(episode_number=1, summary="S1", key_events=[], ending_state="")

        state = ContinuityManager.update_after_episode(
            state, summary, resolved_loop_ids=["loop_001"],
        )
        result = ContinuityManager.get_loop_summary(state)

        assert result["open"] == 1
        assert result["resolved"] == 1
        assert result["resolved_loops"][0]["loop_id"] == "loop_001"
        assert result["resolved_loops"][0]["resolved"] == 1
