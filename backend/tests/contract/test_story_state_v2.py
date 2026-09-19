"""剧情状态 v2 契约测试(M-03,W3-01/W3-02)。

覆盖:typed delta 校验(未知引用/重复 ID/未来计划隔离/无确认字段/
v1 兼容读取)、reducer 语义(稳定 ID 分配/知识分账/伏笔重开/来源引用),
以及 M-01 剧情数据集经生产 reducer 消费——M-03 生产实现必须达到
M-01 structured 组的同一规格。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.continuity import (
    CharacterKnowledge,
    CharacterState,
    ContinuityState,
    FactRecord,
    StateBasis,
)
from app.domain.summary import (
    EpisodeDelta,
    FactDelta,
    FuturePlanDelta,
    KnowledgeDelta,
    LoopIntroductionDelta,
    PropDelta,
    RelationshipDelta,
    TimelineDelta,
)
from app.memory.continuity import ContinuityManager

_GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "memory"
_PROMPT_ARTIFACT = "00000000-0000-0000-0000-0000000000e1"


def _state(characters: list[str], **kw: Any) -> ContinuityState:
    data: dict[str, Any] = {
        "content_schema_version": "2.0",
        "through_episode": 0,
        "character_states": {
            cid: CharacterState(character_id=cid, last_updated_episode=0)
            for cid in characters
        },
    }
    data.update(kw)
    return ContinuityState(**data)


def _delta(episode: int = 1, **kw: Any) -> EpisodeDelta:
    return EpisodeDelta(episode_number=episode, summary=f"第{episode}集摘要", **kw)


# ========================================================================
# typed delta 校验
# ========================================================================


@pytest.mark.contract
class TestEpisodeDeltaContract:
    def test_rejects_unknown_fields(self) -> None:
        """Schema extra=forbid:伪造 user_confirmed 等字段直接失败。"""
        with pytest.raises(ValidationError, match="user_confirmed"):
            EpisodeDelta(
                episode_number=1,
                summary="s",
                user_confirmed=True,  # type: ignore[call-arg]
            )

    def test_future_plan_cannot_be_fact(self) -> None:
        """未来计划不得同时写成已发生事实(模型层约束)。"""
        with pytest.raises(ValidationError, match="未来计划"):
            EpisodeDelta(
                episode_number=1,
                summary="s",
                facts=[FactDelta(text="赵启明当庭说出部分真相", source_scene=1)],
                future_plans=[
                    FuturePlanDelta(text="赵启明当庭说出部分真相", reveal_episode=8)
                ],
            )

    def test_knowledge_requires_exactly_one_ref(self) -> None:
        with pytest.raises(ValidationError, match="之一"):
            KnowledgeDelta(
                character_id="c1", learned=True, source_scene=1,
                new_fact_index=0, fact_id="f1",
            )
        with pytest.raises(ValidationError, match="之一"):
            KnowledgeDelta(character_id="c1", learned=True, source_scene=1)

    def test_v1_state_remains_readable(self) -> None:
        """v1 内容(无 v2 字段)用同一模型可读,schema 版本缺省 1.0。"""
        v1 = ContinuityState.model_validate(
            {
                "through_episode": 2,
                "locked_facts": ["x"],
                "open_loops": [],
                "character_states": {
                    "c1": {
                        "character_id": "c1",
                        "last_updated_episode": 1,
                        "known_information": ["旧文本知识"],
                    }
                },
            }
        )
        assert v1.content_schema_version == "1.0"
        assert v1.facts == {}
        assert v1.character_states["c1"].known_information == ["旧文本知识"]


# ========================================================================
# ContinuityState envelope 校验
# ========================================================================


@pytest.mark.contract
class TestContinuityStateV2Contract:
    def test_knowledge_must_reference_existing_fact(self) -> None:
        base = _state(
            ["c1"],
            through_episode=1,
            facts={
                "f1": FactRecord(
                    text="契约存在", source_episode=1, source_scene=1
                ),
            },
        )
        bad = base.model_copy(
            update={
                "through_episode": 2,
                "character_states": {
                    "c1": CharacterState(
                        character_id="c1",
                        last_updated_episode=2,
                        known_facts=[
                            CharacterKnowledge(
                                fact_id="f_missing",
                                learned_episode=2,
                                source_scene=1,
                            )
                        ],
                    )
                },
            }
        )
        with pytest.raises(ValidationError, match="不存在的事实"):
            ContinuityState.model_validate(bad.model_dump())

    def test_fact_source_cannot_exceed_through(self) -> None:
        state = _state(["c1"])
        with pytest.raises(ValidationError, match="超过 through_episode"):
            ContinuityState.model_validate({
                **state.model_dump(),
                "through_episode": 1,
                "facts": {
                    "f1": FactRecord(
                        text="未来", source_episode=3, source_scene=1
                    ).model_dump()
                },
            })

    def test_unrevealed_plan_cannot_be_marked_revealed(self) -> None:
        from app.domain.continuity import FuturePlan

        state = _state(["c1"])
        with pytest.raises(ValidationError, match="已发生"):
            ContinuityState.model_validate({
                **state.model_dump(),
                "through_episode": 2,
                "future_plans": [
                    FuturePlan(
                        text="p", reveal_episode=8, revealed=True, source_episode=1
                    ).model_dump()
                ],
            })


# ========================================================================
# reducer 语义
# ========================================================================


@pytest.mark.unit
class TestApplyEpisodeDelta:
    def test_author_fact_vs_character_knowledge_separate(self) -> None:
        """验收:第 1 集秘密事件,第 2 集角色得知——作者事实与角色知识分账。"""
        state = _state(["lin", "su"])
        ep1 = ContinuityManager.apply_episode_delta(
            state,
            _delta(
                1,
                facts=[FactDelta(text="旧宅夹层藏有契约", source_scene=1)],
            ),
            source_script_artifact_id=_PROMPT_ARTIFACT,
        )
        # 作者已知,角色未知
        assert len(ep1.facts) == 1
        assert all(not cs.known_facts for cs in ep1.character_states.values())

        fact_id = next(iter(ep1.facts))
        ep2 = ContinuityManager.apply_episode_delta(
            ep1,
            _delta(
                2,
                knowledge=[
                    KnowledgeDelta(
                        character_id="lin", fact_id=fact_id,
                        learned=True, source_scene=1,
                    )
                ],
            ),
            source_script_artifact_id=_PROMPT_ARTIFACT,
        )
        assert [k.fact_id for k in ep2.character_states["lin"].known_facts] == [fact_id]
        assert ep2.character_states["su"].known_facts == []
        k = ep2.character_states["lin"].known_facts[0]
        assert k.learned_episode == 2
        assert k.source_artifact_id == _PROMPT_ARTIFACT
        # 来源引用:事实来自第 1 集
        assert ep2.facts[fact_id].source_episode == 1

    def test_stable_id_assignment_not_per_episode_reset(self) -> None:
        """服务端 ID 规则:不同集的新伏笔 ID 不冲突(不会每集都 loop_001)。"""
        state = _state(["c1"])
        ep1 = ContinuityManager.apply_episode_delta(
            state,
            _delta(1, loops_introduced=[LoopIntroductionDelta(description="A")]),
            source_script_artifact_id=_PROMPT_ARTIFACT,
        )
        ep2 = ContinuityManager.apply_episode_delta(
            ep1,
            _delta(2, loops_introduced=[LoopIntroductionDelta(description="B")]),
            source_script_artifact_id=_PROMPT_ARTIFACT,
        )
        new_ids = [ep1.open_loops[-1].loop_id, ep2.open_loops[-1].loop_id]
        assert len(set(new_ids)) == len(new_ids)
        assert new_ids[0].startswith("loop_01_") and new_ids[1].startswith("loop_02_")

    def test_loop_resolve_and_reopen(self) -> None:
        state = _state(["c1"])
        ep1 = ContinuityManager.apply_episode_delta(
            state,
            _delta(1, loops_introduced=[LoopIntroductionDelta(description="悬案")]),
            source_script_artifact_id=_PROMPT_ARTIFACT,
        )
        lid = ep1.open_loops[0].loop_id
        ep2 = ContinuityManager.apply_episode_delta(
            ep1,
            _delta(2, loops_resolved=[lid]),
            source_script_artifact_id=_PROMPT_ARTIFACT,
        )
        assert [lp.loop_id for lp in ep2.resolved_loops] == [lid]
        assert ep2.resolved_loops[0].resolved_episode == 2
        # 重开
        ep3 = ContinuityManager.apply_episode_delta(
            ep2,
            _delta(
                3,
                loops_introduced=[
                    LoopIntroductionDelta(loop_id=lid, description="悬案重开")
                ],
            ),
            source_script_artifact_id=_PROMPT_ARTIFACT,
        )
        assert ep3.open_loops[0].loop_id == lid
        assert ep3.resolved_loops == []

    def test_fact_immutable_and_reference(self) -> None:
        state = _state(["c1"])
        ep1 = ContinuityManager.apply_episode_delta(
            state,
            _delta(1, facts=[FactDelta(text="原事实", source_scene=1)]),
            source_script_artifact_id=_PROMPT_ARTIFACT,
        )
        fid = next(iter(ep1.facts))
        ep2 = ContinuityManager.apply_episode_delta(
            ep1,
            _delta(2, facts=[FactDelta(fact_id=fid, text="不可覆盖", source_scene=1)]),
            source_script_artifact_id=_PROMPT_ARTIFACT,
        )
        assert ep2.facts[fid].text == "原事实"
        with pytest.raises(ValueError, match="不存在的事实"):
            ContinuityManager.apply_episode_delta(
                ep1,
                _delta(2, facts=[FactDelta(fact_id="ghost", text="x", source_scene=1)]),
                source_script_artifact_id=_PROMPT_ARTIFACT,
            )

    def test_props_relationships_timeline_enter_state(self) -> None:
        state = _state(["a", "b"])
        ep1 = ContinuityManager.apply_episode_delta(
            state,
            _delta(
                1,
                props=[PropDelta(holder_character_id="b", from_character_id="a",
                                 source_scene=2)],
                relationships=[
                    RelationshipDelta(from_character_id="a", to_character_id="b",
                                      before="初识", after="结盟")
                ],
                timeline_events=[
                    TimelineDelta(order_in_episode=1, description="结盟")
                ],
            ),
            source_script_artifact_id=_PROMPT_ARTIFACT,
        )
        prop = next(iter(ep1.props.values()))
        assert prop.holder_character_id == "b"
        assert prop.source_episode == 1 and prop.source_scene == 2
        assert ep1.relationship_changes[0].after == "结盟"
        assert ep1.timeline_events[0].event_id == "tl_01_001"

    def test_future_plan_reveal_boundary(self) -> None:
        state = _state(["c1"])
        ep1 = ContinuityManager.apply_episode_delta(
            state,
            _delta(1, future_plans=[
                FuturePlanDelta(text="第8集揭示", reveal_episode=8)
            ]),
            source_script_artifact_id=_PROMPT_ARTIFACT,
        )
        plan = ep1.future_plans[0]
        assert not plan.revealed
        ep8 = ep1
        for episode in range(2, 9):
            ep8 = ContinuityManager.apply_episode_delta(
                ep8, _delta(episode), source_script_artifact_id=_PROMPT_ARTIFACT
            )
        assert ep8.future_plans[0].revealed is True

    def test_state_replay_deterministic(self) -> None:
        deltas = [
            _delta(1, facts=[FactDelta(text="F1", source_scene=1)]),
            _delta(2, knowledge=[
                KnowledgeDelta(character_id="c1", new_fact_index=None,
                               fact_id="fact_01_01", learned=True, source_scene=1)
            ]),
        ]
        state = _state(["c1"])
        s1 = state
        s2 = state
        for d in deltas:
            s1 = ContinuityManager.apply_episode_delta(
                s1, d, source_script_artifact_id=_PROMPT_ARTIFACT
            )
            s2 = ContinuityManager.apply_episode_delta(
                s2, d, source_script_artifact_id=_PROMPT_ARTIFACT
            )
        assert json.dumps(s1.model_dump(), sort_keys=True) == json.dumps(
            s2.model_dump(), sort_keys=True
        )


# ========================================================================
# M-01 剧情数据集 → 生产 v2 reducer(验收:M-01 数据集可消费 v2 状态)
# ========================================================================


@pytest.mark.contract
class TestGoldenStoryCasesThroughProductionReducer:
    """M-01 story_cases.json 的 typed delta 真值逐集喂给生产 reducer,
    断言知识边界/伏笔终态/来源与数据集期望一致。"""

    @staticmethod
    def _load() -> list[dict[str, Any]]:
        data = json.loads((_GOLDEN / "story_cases.json").read_text(encoding="utf-8"))
        return list(data["cases"])

    @staticmethod
    def _reduce_case(
        case: dict[str, Any],
    ) -> tuple[ContinuityState, dict[str, str], dict[str, dict[str, int]]]:
        """游走数据集:数据集 fact_id → 生产 fact_id 映射随 reduce 维护。

        Returns:
            (终态, 数据集 fact_id → 生产 fact_id,
             数据集知识真值 {character: {fact text: learned_episode}})
        """
        from app.domain.story_bible import StoryBible

        bible = case["characters"]
        sb = StoryBible.model_validate(
            {
                "title": case["title"],
                "genre": "test",
                "logline": "l",
                "world_setting": "w",
                "main_conflict": "测试冲突",
                "stakes": "测试代价",
                "protagonist": {**bible["protagonist"], "role": "主角"},
                "antagonist": {**bible["antagonist"], "role": "反派"},
                "supporting_characters": [
                    {**ch, "role": "配角"}
                    for ch in bible["supporting_characters"]
                ],
                "locked_facts": case["locked_facts"],
                "open_loops": [],
            }
        )
        state = ContinuityManager.create_initial_state_v2(
            sb,
            basis=StateBasis(
                story_bible_artifact_id=_PROMPT_ARTIFACT,
                outline_artifact_id=_PROMPT_ARTIFACT,
            ),
        )
        id_map: dict[str, str] = {}  # 数据集 fact_id → 生产 fact_id
        loop_map: dict[str, str] = {}  # 数据集 loop_id → 生产 loop_id
        assigned: dict[str, str] = {}
        truth: dict[str, dict[str, int]] = {}
        fact_text = {
            f["fact_id"]: f["text"]
            for e in case["episodes"]
            for f in e["typed_delta"].get("facts", [])
        }

        for episode in case["episodes"]:
            d = episode["typed_delta"]
            n = int(episode["episode_number"])
            new_facts = list(d.get("facts", []))
            knowledge = []
            for k in d.get("knowledge", []):
                if not k["learned"]:
                    continue
                char_truth = truth.setdefault(k["character_id"], {})
                text = fact_text[k["fact_id"]]
                char_truth[text] = min(char_truth.get(text, n), n)
                prod_id = id_map.get(k["fact_id"])
                if prod_id is None and any(
                    f["fact_id"] == k["fact_id"] for f in new_facts
                ):
                    idx = next(
                        i for i, f in enumerate(new_facts)
                        if f["fact_id"] == k["fact_id"]
                    )
                    knowledge.append(
                        KnowledgeDelta(
                            character_id=k["character_id"],
                            new_fact_index=idx,
                            learned=True,
                            source_scene=k["source_scene"],
                        )
                    )
                else:
                    assert prod_id is not None, (
                        f"{case['id']} 知识引用了未出现过的 {k['fact_id']}"
                    )
                    knowledge.append(
                        KnowledgeDelta(
                            character_id=k["character_id"],
                            fact_id=prod_id,
                            learned=True,
                            source_scene=k["source_scene"],
                        )
                    )
            delta = EpisodeDelta(
                episode_number=n,
                summary=episode["title"],
                facts=[
                    FactDelta(text=f["text"], source_scene=f["source_scene"])
                    for f in new_facts
                ],
                knowledge=knowledge,
                loops_introduced=[
                    LoopIntroductionDelta(
                        loop_id=loop_map.get(intro["loop_id"]),
                        description=intro["description"],
                    )
                    for intro in d.get("loops_introduced", [])
                ],
                loops_resolved=[
                    loop_map[lid] for lid in d.get("loops_resolved", [])
                ],
                relationships=[
                    RelationshipDelta(
                        from_character_id=r["from_character_id"],
                        to_character_id=r["to_character_id"],
                        before=r.get("before", ""),
                        after=r.get("after", ""),
                    )
                    for r in d.get("relationship_changes", [])
                ],
                timeline_events=[
                    TimelineDelta(
                        order_in_episode=t["order_in_episode"],
                        description=t["description"],
                    )
                    for t in d.get("timeline_events", [])
                ],
                props=[
                    PropDelta(
                        holder_character_id=p["holder_character_id"],
                        from_character_id=p.get("from_character_id"),
                        source_scene=p["source_scene"],
                    )
                    for p in d.get("props", [])
                ],
            )
            state = ContinuityManager.apply_episode_delta(
                state, delta,
                source_script_artifact_id=_PROMPT_ARTIFACT,
                assigned_ids=assigned,
            )
            for f in new_facts:
                prod = assigned.get(f["text"])
                if prod:
                    id_map[f["fact_id"]] = prod
            for intro in d.get("loops_introduced", []):
                prod = assigned.get(intro["description"])
                if prod:
                    loop_map[intro["loop_id"]] = prod
        return state, id_map, truth

    def test_knowledge_boundaries_match_ground_truth(self) -> None:
        for case in self._load():
            state, _, truth = self._reduce_case(case)
            for cid, expected in truth.items():
                known = {
                    state.facts[k.fact_id].text: k.learned_episode
                    for k in state.character_states[cid].known_facts
                }
                for text, episode in expected.items():
                    assert text in known, f"{case['id']}/{cid} 缺知识 {text}"
                    assert known[text] == episode, (
                        f"{case['id']}/{cid} {text} 得知集数不符"
                    )

    @staticmethod
    def _final_loop_status(case: dict[str, Any]) -> dict[str, str]:
        """重放数据集计算每个 loop_id 的终态(open/resolved)。"""
        status: dict[str, str] = {}
        for episode in sorted(case["episodes"], key=lambda e: int(e["episode_number"])):
            d = episode["typed_delta"]
            for intro in d.get("loops_introduced", []):
                status[intro["loop_id"]] = "open"
            for lid in d.get("loops_resolved", []):
                status[lid] = "resolved"
        return status

    def test_loop_status_matches_ground_truth(self) -> None:
        for case in self._load():
            state, _, _ = self._reduce_case(case)
            desc_by_id: dict[str, str] = {}
            for e in case["episodes"]:
                for intro in e["typed_delta"].get("loops_introduced", []):
                    desc_by_id[intro["loop_id"]] = intro["description"]
            final = self._final_loop_status(case)
            open_descs = {lp.description for lp in state.open_loops}
            resolved_descs = {lp.description for lp in state.resolved_loops}
            for lid, status in final.items():
                desc = desc_by_id[lid]
                where = open_descs if status == "open" else resolved_descs
                assert desc in where, (
                    f"{case['id']} 伏笔 {lid}({desc}) 终态应为 {status}"
                )

    def test_fact_sources_correct(self) -> None:
        for case in self._load():
            state, _, _ = self._reduce_case(case)
            truth = {
                f["text"]: (int(e["episode_number"]), f["source_scene"])
                for e in case["episodes"]
                for f in e["typed_delta"].get("facts", [])
            }
            for record in state.facts.values():
                assert record.text in truth, case["id"]
                assert (record.source_episode, record.source_scene) == truth[
                    record.text
                ], f"{case['id']} {record.text} 来源不符"


