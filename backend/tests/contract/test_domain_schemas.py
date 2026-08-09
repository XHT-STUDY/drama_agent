"""A-03 Contract 测试 — 领域 Schema、枚举与 Golden Fixtures 的验证。

覆盖 DEV_PLAN.md §12 A-03 全部验收条件：
- 10 集大纲的编号/数量验证有效
- 0..100 分数边界有效
- Evaluation 权重之和测试等于 1
- 非法额外字段会被拒绝
- Golden fixtures 可序列化再反序列化
"""


import pytest
from pydantic import BaseModel, ValidationError

from app.domain.continuity import (
    CharacterState,
    ContinuityState,
    EpisodeSummary,
    StoryLoop,
)
from app.domain.enums import (
    DEFAULT_EVALUATION_WEIGHTS,
    ArtifactStatus,
    ArtifactType,
    EvaluationDimension,
    ProjectStatus,
)
from app.domain.evaluation import (
    EvaluationIssue,
    EvaluationReport,
    compute_need_revision,
    compute_overall_score,
)
from app.domain.outline import EpisodeOutlineSet
from app.domain.requirement import NormalizedRequirement
from app.domain.revision import RevisionPlan
from app.domain.script import DialogueLine, Scene, ScriptDraft
from app.domain.story_bible import CharacterProfile, StoryBible

from .conftest import load_fixture

# ========== 枚举测试 ==========


class TestEnums:
    """枚举值的完整性和一致性。"""

    def test_project_status_values(self) -> None:
        """ProjectStatus 包含全部 7 种状态。"""
        values = {s.value for s in ProjectStatus}
        assert values == {"draft", "planning", "writing", "evaluating", "revising", "completed", "archived"}

    def test_artifact_type_values(self) -> None:
        """ArtifactType 包含 MVP 所需的全部 11 种类型（F-05 增 continuity_check）。"""
        values = {t.value for t in ArtifactType}
        assert len(values) == 11
        assert "story_bible" in values
        assert "script_draft" in values
        assert "evaluation_report" in values
        assert "revision_plan" in values
        assert "continuity_check" in values

    def test_artifact_status_values(self) -> None:
        """ArtifactStatus 只允许 draft/valid/invalid 三种状态。"""
        values = {s.value for s in ArtifactStatus}
        assert values == {"draft", "valid", "invalid"}

    def test_evaluation_dimension_count(self) -> None:
        """评估维度必须有且仅有 9 个。"""
        dims = list(EvaluationDimension)
        assert len(dims) == 9

    def test_default_weights_sum_to_one(self) -> None:
        """默认评估权重之和严格等于 1.0（§5.8 表格）。"""
        total = sum(DEFAULT_EVALUATION_WEIGHTS.values())
        assert total == pytest.approx(1.0), f"权重之和应为 1.0，实际为 {total}"

    def test_default_weights_cover_all_dimensions(self) -> None:
        """默认权重必须覆盖全部九个维度。"""
        weight_keys = set(DEFAULT_EVALUATION_WEIGHTS.keys())
        all_dims = set(EvaluationDimension)
        assert weight_keys == all_dims, f"缺少: {all_dims - weight_keys}, 多余: {weight_keys - all_dims}"


# ========== NormalizedRequirement 测试 ==========


class TestNormalizedRequirement:
    """NormalizedRequirement 的构建与校验。"""

    def test_valid_from_golden(self) -> None:
        """Golden fixture 应构建成功。"""
        data = load_fixture("requirement_valid.json")
        req = NormalizedRequirement.model_validate(data)
        assert req.title == "足球少年之逆袭人生"
        assert req.target_episode_count == 10

    def test_extra_field_rejected(self) -> None:
        """非法额外字段应被 extra=forbid 拒绝。"""
        data = load_fixture("requirement_valid.json")
        data["unknown_field"] = "should fail"
        with pytest.raises(ValidationError):
            NormalizedRequirement.model_validate(data)

    def test_empty_protagonist_rejected(self) -> None:
        """主角设定为空时应被 min_length 拒绝。"""
        data = load_fixture("requirement_valid.json")
        data["protagonist_seed"] = ""
        with pytest.raises(ValidationError):
            NormalizedRequirement.model_validate(data)

    def test_list_fields_return_empty_never_null(self) -> None:
        """列表字段显式返回空列表。"""
        data = load_fixture("requirement_valid.json")
        req = NormalizedRequirement.model_validate(data)
        assert isinstance(req.tone, list)
        assert isinstance(req.must_have, list)
        assert isinstance(req.assumptions, list)


# ========== StoryBible 测试 ==========


class TestStoryBible:
    """StoryBible 的构建与角色约束。"""

    def test_valid_from_golden(self) -> None:
        """Golden fixture 应构建成功。"""
        data = load_fixture("story_bible_valid.json")
        sb = StoryBible.model_validate(data)
        assert sb.title == "足球少年之逆袭人生"
        assert sb.protagonist.name == "林峰"
        assert sb.antagonist.name == "陈浩"

    def test_protagonist_equals_antagonist_rejected(self) -> None:
        """主角和反派为同一角色时校验失败。"""
        data = load_fixture("story_bible_invalid.json")
        # 该 fixture 中 protagonist 和 antagonist 都是 char_001
        with pytest.raises(ValidationError):
            StoryBible.model_validate(data)

    def test_extra_field_rejected(self) -> None:
        """非法额外字段应被拒绝。"""
        data = load_fixture("story_bible_valid.json")
        data["unknown"] = "nope"
        with pytest.raises(ValidationError):
            StoryBible.model_validate(data)


# ========== EpisodeOutlineSet 测试 ==========


class TestEpisodeOutlineSet:
    """EpisodeOutlineSet 的集数、编号和内容校验。"""

    def test_valid_10_episodes_from_golden(self) -> None:
        """Golden fixture 10 集大纲应构建成功。"""
        data = load_fixture("outline_set_valid.json")
        outline_set = EpisodeOutlineSet.model_validate(data)
        assert len(outline_set.episodes) == 10
        numbers = [ep.episode_number for ep in outline_set.episodes]
        assert numbers == list(range(1, 11))

    def test_9_episodes_rejected(self) -> None:
        """只有 9 集时校验失败。"""
        data = load_fixture("outline_set_valid.json")
        data["episodes"] = data["episodes"][:9]
        with pytest.raises(ValidationError, match="10"):
            EpisodeOutlineSet.model_validate(data)

    def test_11_episodes_rejected(self) -> None:
        """11 集时校验失败。"""
        data = load_fixture("outline_set_valid.json")
        # 复制第 10 集并改变编号
        extra = dict(data["episodes"][9])
        extra["episode_number"] = 11
        data["episodes"] = data["episodes"] + [extra]
        with pytest.raises(ValidationError, match="10"):
            EpisodeOutlineSet.model_validate(data)

    def test_duplicate_episode_numbers_rejected(self) -> None:
        """重复集号校验失败。"""
        data = load_fixture("outline_set_valid.json")
        episodes = data["episodes"][:9]
        episodes.append(dict(episodes[0]))  # 复制第 1 集
        data["episodes"] = episodes
        with pytest.raises(ValidationError, match="重复"):
            EpisodeOutlineSet.model_validate(data)

    def test_missing_episode_number_rejected(self) -> None:
        """缺失中间集号校验失败。"""
        data = load_fixture("outline_set_valid.json")
        # 将第 5 集改为编号 12
        data["episodes"][4]["episode_number"] = 12
        data["episodes"] = data["episodes"][:10]
        with pytest.raises(ValidationError, match="缺失"):
            EpisodeOutlineSet.model_validate(data)

    def test_key_events_min_count(self) -> None:
        """每集 key_events 少于 2 个应被校验拒绝。"""
        data = load_fixture("outline_set_valid.json")
        data["episodes"][0]["key_events"] = ["单一事件"]
        with pytest.raises(ValidationError, match="key_events"):
            EpisodeOutlineSet.model_validate(data)

    def test_validate_sequence_checks_bridges(self) -> None:
        """validate_sequence() 检查缺失的 next_bridge。"""
        data = load_fixture("outline_set_valid.json")
        outline_set = EpisodeOutlineSet.model_validate(data)
        notes = outline_set.validate_sequence()
        # Golden fixture 中有完整的 next_bridge
        assert isinstance(notes, list)
        # 人为清空一个 next_bridge 来验证检测
        data["episodes"][0]["next_bridge"] = ""
        modified = EpisodeOutlineSet.model_validate(data)
        modified_notes = modified.validate_sequence()
        assert len(modified_notes) > 0

    def test_extra_field_rejected(self) -> None:
        """非法额外字段应被拒绝。"""
        data = load_fixture("outline_set_valid.json")
        data["bonus"] = "surprise"
        with pytest.raises(ValidationError):
            EpisodeOutlineSet.model_validate(data)


# ========== ScriptDraft 测试 ==========


class TestScriptDraft:
    """ScriptDraft 的场景和字段校验。"""

    def test_valid_from_golden(self) -> None:
        """Golden fixture 应构建成功。"""
        data = load_fixture("script_draft_valid.json")
        script = ScriptDraft.model_validate(data)
        assert script.episode_number == 1
        assert script.word_count == 1250

    def test_one_scene_rejected(self) -> None:
        """只有 1 场戏时校验失败。"""
        data = load_fixture("script_draft_invalid.json")
        with pytest.raises(ValidationError, match="至少需要 2 场"):
            ScriptDraft.model_validate(data)

    def test_scene_numbers_consecutive(self) -> None:
        """场次编号不连续时应报错。"""
        data = load_fixture("script_draft_valid.json")
        data["scenes"][0]["scene_number"] = 5
        data["scenes"][1]["scene_number"] = 6
        with pytest.raises(ValidationError, match="场次编号"):
            ScriptDraft.model_validate(data)

    def test_extra_field_rejected(self) -> None:
        """非法额外字段应被拒绝。"""
        data = load_fixture("script_draft_valid.json")
        data["ai_generated"] = True
        with pytest.raises(ValidationError):
            ScriptDraft.model_validate(data)


# ========== EvaluationReport 测试 ==========


class TestEvaluationReport:
    """EvaluationReport 的评分边界和计算校验。"""

    def test_valid_from_golden(self) -> None:
        """Golden fixture 应构建成功。"""
        data = load_fixture("evaluation_report_valid.json")
        report = EvaluationReport.model_validate(data)
        assert report.episode_number == 1
        assert report.need_revision is True

    def test_score_out_of_range_rejected(self) -> None:
        """维度评分超过 100 应被拒绝（compliance_safety=150）。"""
        data = load_fixture("evaluation_report_invalid.json")
        with pytest.raises(ValidationError, match="超出"):
            EvaluationReport.model_validate(data)

    def test_missing_dimension_rejected(self) -> None:
        """缺少评估维度时校验失败。"""
        data = load_fixture("evaluation_report_valid.json")
        del data["dimension_scores"]["opening_hook"]
        with pytest.raises(ValidationError, match="缺少"):
            EvaluationReport.model_validate(data)

    def test_negative_score_rejected(self) -> None:
        """负分应被拒绝。"""
        data = load_fixture("evaluation_report_valid.json")
        data["dimension_scores"]["compliance_safety"] = -5
        with pytest.raises(ValidationError, match="超出"):
            EvaluationReport.model_validate(data)

    def test_compute_overall_score(self) -> None:
        """加权总分计算应与手动计算结果一致。"""
        scores = {
            EvaluationDimension.OPENING_HOOK: 80,
            EvaluationDimension.MAIN_CLARITY: 80,
            EvaluationDimension.CHARACTER_APPEAL: 80,
            EvaluationDimension.CONFLICT_INTENSITY: 80,
            EvaluationDimension.PAYOFF_DENSITY: 80,
            EvaluationDimension.ENDING_HOOK: 80,
            EvaluationDimension.PACING: 80,
            EvaluationDimension.VISUALIZABILITY: 80,
            EvaluationDimension.COMPLIANCE_SAFETY: 80,
        }
        result = compute_overall_score(scores)
        assert result == 80.0  # 全部 80 分 × 权重总和 1.0 = 80.0

    def test_compute_need_revision_low_score(self) -> None:
        """overall_score < 75 时 need_revision 为 True。"""
        assert compute_need_revision(70.0, []) is True

    def test_compute_need_revision_high_severity(self) -> None:
        """存在 severity="high" 的问题时 need_revision 为 True。"""
        issue = EvaluationIssue(
            issue_id="iss_h",
            dimension=EvaluationDimension.CONFLICT_INTENSITY,
            severity="high",
            evidence="测试证据",
            diagnosis="测试诊断",
            suggestion="测试建议",
        )
        assert compute_need_revision(80.0, [issue]) is True

    def test_compute_need_revision_compliance_low(self) -> None:
        """compliance_safety < 60 时 need_revision 为 True。"""
        dim_scores = {
            EvaluationDimension.COMPLIANCE_SAFETY: 50,
        }
        assert compute_need_revision(80.0, [], dim_scores) is True

    def test_compute_need_revision_healthy(self) -> None:
        """高分、无严重问题、合规良好时 need_revision 为 False。"""
        dim_scores = {
            EvaluationDimension.COMPLIANCE_SAFETY: 80,
        }
        assert compute_need_revision(80.0, [], dim_scores) is False


# ========== RevisionPlan 测试 ==========


class TestRevisionPlan:
    """RevisionPlan 的操作列表和参数校验。"""

    def test_valid_from_golden(self) -> None:
        """Golden fixture 应构建成功。"""
        data = load_fixture("revision_plan_valid.json")
        plan = RevisionPlan.model_validate(data)
        assert plan.episode_number == 1
        assert len(plan.operations) == 1
        assert plan.max_change_ratio == 0.35

    def test_empty_operations_rejected(self) -> None:
        """空操作列表应被拒绝。"""
        data = load_fixture("revision_plan_invalid.json")
        with pytest.raises(ValidationError, match="至少需要"):
            RevisionPlan.model_validate(data)

    def test_max_change_ratio_out_of_range(self) -> None:
        """max_change_ratio > 1.0 应被 Field 约束拒绝。"""
        data = load_fixture("revision_plan_valid.json")
        data["max_change_ratio"] = 1.5
        with pytest.raises(ValidationError):
            RevisionPlan.model_validate(data)


# ========== ContinuityState 测试 ==========


class TestContinuityState:
    """ContinuityState 的摘要和伏笔校验。"""

    def test_valid_from_golden(self) -> None:
        """Golden fixture 应构建成功。"""
        data = load_fixture("continuity_state_valid.json")
        state = ContinuityState.model_validate(data)
        assert state.through_episode == 3
        assert len(state.episode_summaries) == 3
        assert len(state.open_loops) == 2

    def test_summary_beyond_through_episode_rejected(self) -> None:
        """EpisodeSummary 集号超过 through_episode 时报错。"""
        data = load_fixture("continuity_state_invalid.json")
        # through_episode=3, 但 episode_summaries 中有 episode_number=5
        with pytest.raises(ValidationError, match="超过"):
            ContinuityState.model_validate(data)

    def test_duplicate_loop_ids_rejected(self) -> None:
        """重复的 loop_id 应被拒绝。"""
        data = load_fixture("continuity_state_valid.json")
        # 把 resolved_loops 的 loop 也加入 open_loops
        data["open_loops"].append(data["resolved_loops"][0])
        with pytest.raises(ValidationError, match="重复"):
            ContinuityState.model_validate(data)


# ========== Golden Fixture Round-Trip 测试 ==========


class TestGoldenRoundTrip:
    """确保每个 *_valid.json fixture 可以 反序列化 → 构建模型 → 序列化。"""

    VALID_FIXTURES: list[tuple[str, type[BaseModel]]] = [
        ("requirement_valid.json", NormalizedRequirement),
        ("story_bible_valid.json", StoryBible),
        ("outline_set_valid.json", EpisodeOutlineSet),
        ("script_draft_valid.json", ScriptDraft),
        ("evaluation_report_valid.json", EvaluationReport),
        ("revision_plan_valid.json", RevisionPlan),
        ("continuity_state_valid.json", ContinuityState),
    ]

    @pytest.mark.parametrize(("fixture_name", "model_cls"), VALID_FIXTURES)
    def test_round_trip(self, fixture_name: str, model_cls: type[BaseModel]) -> None:
        """加载 fixture → model_validate → model_dump(mode="json") → 重新加载比较。"""
        data = load_fixture(fixture_name)

        # 构建模型
        instance = model_cls.model_validate(data)

        # 序列化回 JSON 兼容的 dict
        dumped = instance.model_dump(mode="json")

        # 对 UUID 字段特殊处理：model_validate 可接受 string，
        # 但 model_dump(mode="json") 将 UUID 输出为 string，
        # 所以 dump 后再 validate 应成功且内容一致
        re_instance = model_cls.model_validate(dumped)
        re_dumped = re_instance.model_dump(mode="json")
        assert re_dumped == dumped, (
            f"{fixture_name} round-trip 失败：第二次序列化结果不一致"
        )


# ========== 子模型独立校验测试 ==========


class TestSubModels:
    """子模型的独立构建和约束测试。"""

    def test_character_profile_valid(self) -> None:
        """CharacterProfile 最小有效构建。"""
        profile = CharacterProfile(
            character_id="c1",
            name="测试角色",
            role="配角",
            visible_goal="测试目标",
        )
        assert profile.character_id == "c1"
        assert profile.traits == []  # 默认空列表

    def test_dialogue_line_valid(self) -> None:
        """DialogueLine 最小有效构建。"""
        line = DialogueLine(speaker="林峰", text="你好")
        assert line.parenthetical is None

    def test_scene_valid(self) -> None:
        """Scene 最小有效构建。"""
        scene = Scene(
            scene_number=1,
            location="训练场",
            time_of_day="日",
            action="林峰在训练。",
        )
        assert scene.characters == []

    def test_episode_summary_valid(self) -> None:
        """EpisodeSummary 最小有效构建。"""
        summary = EpisodeSummary(
            episode_number=1,
            summary="第一集摘要。",
        )
        assert summary.key_events == []

    def test_story_loop_valid(self) -> None:
        """StoryLoop 最小有效构建。"""
        loop = StoryLoop(
            loop_id="l1",
            description="一个伏笔",
            introduced_episode=1,
            status="open",
        )
        assert loop.resolved_episode is None

    def test_character_state_valid(self) -> None:
        """CharacterState 最小有效构建。"""
        state = CharacterState(
            character_id="c1",
            current_goal="赢得比赛",
            last_updated_episode=1,
        )
        assert state.physical_state is None
