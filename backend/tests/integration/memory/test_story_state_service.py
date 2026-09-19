"""StoryStateService 集成测试(M-03,W3-02)。

前置条件:Docker PostgreSQL 就绪(make up),内容感知 FakeLLM 驱动。

覆盖:
- 单集/多集派生:EpisodeSummary + ContinuityState Artifact 与链接落库,
  知识分账/来源引用/工作集绑定;
- 幂等:相同输入重试零模型调用;源稿变化产生新派生;
- 派生失败保留正文(校验失败抛出,正文 checksum 不变),恢复只补派生;
- 跨项目来源拒绝;正文缺失 fail closed(STORY_STATE_GAP);
- 并发/重复插入由 input_hash 幂等兜底(0013 唯一索引)。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.application.story_state_service import (
    StoryStateService,
    StoryWorkset,
)
from app.core.errors import StoryStateGapError
from app.db.models.artifact import Artifact
from app.domain.script import ScriptDraft
from app.llm.fake import FakeLLM

_OUTLINE_ID = "00000000-0000-0000-0000-0000000000f1"


def _script_content(n: int) -> dict[str, Any]:
    """第 n 集剧本(合法 ScriptDraft,至少两场)。"""
    return {
        "title": f"第{n}集",
        "episode_number": n,
        "opening_hook": f"第{n}集开头钩子",
        "scenes": [
            {
                "scene_number": i,
                "location": f"场景{n}-{i}",
                "int_ext": "内",
                "time_of_day": "日",
                "characters": ["林砚"],
                "action": f"第{n}集第{i}场动作",
                "dialogue": [
                    {
                        "line_type": "dialogue",
                        "speaker": "林砚",
                        "text": f"第{n}集第{i}场对白",
                    }
                ],
            }
            for i in (1, 2)
        ],
        "ending_hook": f"第{n}集结尾钩子",
        "plain_text": f"第{n}集正文",
        "word_count": 100,
        "dialogue_ratio": 0.4,
        "referenced_outline_artifact_id": _OUTLINE_ID,
    }


class _DeltaFakeLLM(FakeLLM):
    """内容感知 typed delta 桩:按集返回固定 delta(秘密第1集,第2集得知)。"""

    def __init__(self) -> None:
        super().__init__(seed=42)
        self.delta_calls = 0

    async def generate_structured(
        self, schema: type[Any], messages: list[dict[str, str]], **kwargs: Any
    ) -> Any:
        from app.llm.models import LLMCallResult

        text = messages[-1]["content"]
        # 从模板标题行提取集号("第 {{ episode_number }} 集剧本")
        import re

        m = re.search(r"第 (\d+) 集剧本", text)
        episode = int(m.group(1)) if m else 1
        self.delta_calls += 1
        delta = self._delta_for(episode)
        return LLMCallResult(content="ok", parsed=delta)

    def _delta_for(self, episode: int) -> Any:
        from app.domain.summary import (
            EpisodeDelta,
            FactDelta,
            KnowledgeDelta,
            LoopIntroductionDelta,
            PropDelta,
            RelationshipDelta,
            TimelineDelta,
        )

        kwargs: dict[str, Any] = {"episode_number": episode, "summary": f"第{episode}集摘要"}
        if episode == 1:
            kwargs["facts"] = [FactDelta(text="旧宅夹层藏有契约", source_scene=1)]
            kwargs["loops_introduced"] = [
                LoopIntroductionDelta(description="契约为何被藏")
            ]
            kwargs["props"] = [
                PropDelta(
                    holder_character_id="char_lin",
                    from_character_id=None,
                    source_scene=1,
                )
            ]
            kwargs["timeline_events"] = [
                TimelineDelta(order_in_episode=1, description="发现契约")
            ]
        elif episode == 2:
            kwargs["knowledge"] = [
                KnowledgeDelta(
                    character_id="char_lin",
                    new_fact_index=0 if False else None,
                    fact_id="fact_01_01",
                    learned=True,
                    source_scene=1,
                )
            ]
            kwargs["relationships"] = [
                RelationshipDelta(
                    from_character_id="char_lin",
                    to_character_id="char_zhao",
                    before="疏远",
                    after="试探",
                )
            ]
        elif episode == 8:
            kwargs["loops_resolved"] = ["loop_01_01"]
        return EpisodeDelta(**kwargs)


async def _seed_project_with_bible(
    db: Any, artifact_service: ArtifactService
) -> tuple[uuid.UUID, Any, Any]:
    from tests.integration.memory.test_summary import _insert_messages  # noqa: F401

    project_id = uuid.uuid4()
    from app.db.models.project import Project

    db.add(Project(id=project_id, title="M-03 测试项目", status="draft"))
    await db.flush()
    bible_content = {
        "title": "雨夜遗契",
        "genre": "都市悬疑",
        "logline": "遗契翻案",
        "world_setting": "现代都市",
        "main_conflict": "夺回股权",
        "stakes": "家族存亡",
        "protagonist": {
            "character_id": "char_lin",
            "name": "林砚",
            "role": "主角",
            "visible_goal": "查明真相",
        },
        "antagonist": {
            "character_id": "char_zhao",
            "name": "赵启明",
            "role": "反派",
            "visible_goal": "维持局面",
        },
        "supporting_characters": [
            {
                "character_id": "char_su",
                "name": "苏晚",
                "role": "配角",
                "visible_goal": "协助主角",
            }
        ],
        "locked_facts": ["母亲签名的契约真实存在"],
        "open_loops": [],
    }
    sb = await artifact_service.create_validated_artifact(
        db, project_id=project_id, artifact_type="story_bible",
        content=bible_content,
    )
    outline = await artifact_service.create_validated_artifact(
        db, project_id=project_id, artifact_type="episode_outline_set",
        content=_outline_content(),
    )
    return project_id, sb, outline


def _outline_content() -> dict[str, Any]:
    return {
        "outline_count": 8,
        "arc_summary": "翻案之路",
        "episodes": [
            {
                "episode_number": n,
                "title": f"第{n}集",
                "objective": "推进",
                "opening_hook": "钩子",
                "core_conflict": "冲突",
                "key_events": [],
                "required_characters": [],
            }
            for n in range(1, 9)
        ],
    }


async def _add_script(
    db: Any,
    artifact_service: ArtifactService,
    project_id: uuid.UUID,
    n: int,
    content: dict[str, Any] | None = None,
) -> Any:
    ScriptDraft.model_validate(content or _script_content(n))
    return await artifact_service.create_validated_artifact(
        db,
        project_id=project_id,
        artifact_type="script_draft",
        episode_number=n,
        content=content or _script_content(n),
    )


@pytest.mark.integration
class TestStoryStateServiceDerivation:
    async def test_derives_evidence_and_state_through_episodes(
        self, db_session: Any
    ) -> None:
        """两集派生:作者事实与角色知识分账、来源引用、basis 绑定。"""
        artifact_service = ArtifactService()
        project_id, sb, outline = await _seed_project_with_bible(
            db_session, artifact_service
        )
        s1 = await _add_script(db_session, artifact_service, project_id, 1)
        s2 = await _add_script(db_session, artifact_service, project_id, 2)
        llm = _DeltaFakeLLM()
        service = StoryStateService(BaseAgent(name="summarizer", llm=llm))

        workset = StoryWorkset(
            project_id=project_id,
            story_bible_artifact_id=sb.id,
            outline_artifact_id=outline.id,
            scripts={1: s1.id, 2: s2.id},
        )
        # 先派生到第 1 集:秘密事件是作者事实,角色仍未知(分账)
        through1 = await service.ensure_state_through(db_session, workset, 1)
        assert through1.model_calls == 1
        assert through1.state.facts["fact_01_01"].text == "旧宅夹层藏有契约"
        assert through1.state.character_states["char_lin"].known_facts == []

        outcome = await service.ensure_state_through(db_session, workset, 2)

        assert outcome.model_calls == 1  # 第 1 集复用,仅新增第 2 集模型调用
        assert set(outcome.episode_summary_artifact_ids) == {1, 2}
        state = outcome.state
        # 第 2 集得知:知识与事实分账,带来源
        knowledge = state.character_states["char_lin"].known_facts[0]
        assert knowledge.fact_id == "fact_01_01"
        assert knowledge.learned_episode == 2
        assert knowledge.source_artifact_id == str(s2.id)
        assert state.facts["fact_01_01"].source_artifact_id == str(s1.id)
        # 关系/伏笔/道具/时间线进入状态
        assert state.relationship_changes[0].after == "试探"
        assert state.open_loops[0].loop_id == "loop_01_01"
        assert state.props["prop_01_01"].holder_character_id == "char_lin"
        assert state.timeline_events[0].source_artifact_id == str(s1.id)
        # basis 绑定工作集与证据链
        assert state.basis is not None
        assert state.basis.story_bible_artifact_id == str(sb.id)
        assert state.basis.script_artifact_ids["1"] == str(s1.id)
        assert state.basis.episode_summary_artifact_ids["2"] == (
            outcome.episode_summary_artifact_ids[2]
        )

        # Artifact 落库:episode_summary×2 + continuity_state×(初态+2)
        from sqlalchemy import select

        summaries = (await db_session.execute(
            select(Artifact).where(
                Artifact.project_id == project_id,
                Artifact.type == "episode_summary",
            )
        )).scalars().all()
        states = (await db_session.execute(
            select(Artifact).where(
                Artifact.project_id == project_id,
                Artifact.type == "continuity_state",
            )
        )).scalars().all()
        assert len(summaries) == 2
        assert len(states) == 2  # through1 + through2(初态不落库)
        assert all(a.content_schema_version == "2.0" for a in summaries + states)

    async def test_retry_same_inputs_zero_model_calls(
        self, db_session: Any
    ) -> None:
        """验收:相同输入重试不重复调用模型(全部复用)。"""
        artifact_service = ArtifactService()
        project_id, sb, outline = await _seed_project_with_bible(
            db_session, artifact_service
        )
        s1 = await _add_script(db_session, artifact_service, project_id, 1)
        llm = _DeltaFakeLLM()
        service = StoryStateService(BaseAgent(name="summarizer", llm=llm))
        workset = StoryWorkset(
            project_id=project_id,
            story_bible_artifact_id=sb.id,
            outline_artifact_id=outline.id,
            scripts={1: s1.id},
        )
        first = await service.ensure_state_through(db_session, workset, 1)
        assert first.model_calls == 1
        second = await service.ensure_state_through(db_session, workset, 1)
        assert second.model_calls == 0
        assert second.reused_episodes == [1]
        assert second.state.through_episode == 1
        assert llm.delta_calls == 1  # 只调了一次模型

    async def test_source_change_produces_new_derivation(
        self, db_session: Any
    ) -> None:
        """验收:源稿变化产生新派生(Prompt 版本/前态变化同理走 input_hash)。"""
        artifact_service = ArtifactService()
        project_id, sb, outline = await _seed_project_with_bible(
            db_session, artifact_service
        )
        s1 = await _add_script(db_session, artifact_service, project_id, 1)
        llm = _DeltaFakeLLM()
        service = StoryStateService(BaseAgent(name="summarizer", llm=llm))
        workset = StoryWorkset(
            project_id=project_id,
            story_bible_artifact_id=sb.id,
            outline_artifact_id=outline.id,
            scripts={1: s1.id},
        )
        await service.ensure_state_through(db_session, workset, 1)
        assert llm.delta_calls == 1

        # 同集新版本正文 → 新派生
        revised = dict(_script_content(1))
        revised["plain_text"] = "第1集修订正文"
        s1b = await _add_script(db_session, artifact_service, project_id, 1, revised)
        workset_b = StoryWorkset(
            project_id=project_id,
            story_bible_artifact_id=sb.id,
            outline_artifact_id=outline.id,
            scripts={1: s1b.id},
        )
        outcome_b = await service.ensure_state_through(db_session, workset_b, 1)
        assert outcome_b.model_calls == 1
        assert llm.delta_calls == 2

    async def test_derivation_failure_keeps_script(
        self, db_session: Any
    ) -> None:
        """验收:派生失败(模型输出引用未知角色)保留正文,checksum 不变;
        修复后恢复只补派生。"""
        artifact_service = ArtifactService()
        project_id, sb, outline = await _seed_project_with_bible(
            db_session, artifact_service
        )
        s1 = await _add_script(db_session, artifact_service, project_id, 1)
        before_checksum = s1.checksum

        from app.domain.summary import EpisodeDelta, KnowledgeDelta

        bad_llm = _DeltaFakeLLM()

        async def bad_delta(*a: Any, **kw: Any) -> Any:
            return EpisodeDelta(
                episode_number=1,
                summary="坏输出",
                knowledge=[
                    KnowledgeDelta(
                        character_id="char_ghost",  # 不存在的角色
                        fact_id="fact_01_01",
                        learned=True,
                        source_scene=1,
                    )
                ],
            )

        service = StoryStateService(BaseAgent(name="summarizer", llm=bad_llm))
        service._skill.execute_delta = bad_delta  # type: ignore[method-assign]
        workset = StoryWorkset(
            project_id=project_id,
            story_bible_artifact_id=sb.id,
            outline_artifact_id=outline.id,
            scripts={1: s1.id},
        )
        with pytest.raises(ValueError, match="未知角色"):
            await service.ensure_state_through(db_session, workset, 1)

        # 正文未动
        from sqlalchemy import select

        scripts = (await db_session.execute(
            select(Artifact).where(
                Artifact.project_id == project_id,
                Artifact.type == "script_draft",
            )
        )).scalars().all()
        assert len(scripts) == 1 and scripts[0].checksum == before_checksum
        # 无 episode_summary 落库
        summaries = (await db_session.execute(
            select(Artifact).where(
                Artifact.project_id == project_id,
                Artifact.type == "episode_summary",
            )
        )).scalars().all()
        assert summaries == []

        # 恢复:换回合法输出,只补派生
        good_llm = _DeltaFakeLLM()
        good_service = StoryStateService(
            BaseAgent(name="summarizer", llm=good_llm)
        )
        outcome = await good_service.ensure_state_through(db_session, workset, 1)
        assert outcome.model_calls == 1
        assert outcome.state.through_episode == 1

    async def test_missing_script_fails_closed(self, db_session: Any) -> None:
        """正文缺失 → STORY_STATE_GAP,不调用模型。"""
        artifact_service = ArtifactService()
        project_id, sb, outline = await _seed_project_with_bible(
            db_session, artifact_service
        )
        llm = _DeltaFakeLLM()
        service = StoryStateService(BaseAgent(name="summarizer", llm=llm))
        workset = StoryWorkset(
            project_id=project_id,
            story_bible_artifact_id=sb.id,
            outline_artifact_id=outline.id,
            scripts={1: uuid.uuid4()},  # 不存在
        )
        with pytest.raises(StoryStateGapError, match="Artifact 不存在"):
            await service.ensure_state_through(db_session, workset, 1)
        assert llm.delta_calls == 0

    async def test_cross_project_source_rejected(self, db_session: Any) -> None:
        """验收:项目 A 的来源不能出现在项目 B 状态中。"""
        artifact_service = ArtifactService()
        project_a, sb_a, outline_a = await _seed_project_with_bible(
            db_session, artifact_service
        )
        project_b, sb_b, _outline_b = await _seed_project_with_bible(
            db_session, artifact_service
        )
        s1 = await _add_script(db_session, artifact_service, project_a, 1)
        llm = _DeltaFakeLLM()
        service = StoryStateService(BaseAgent(name="summarizer", llm=llm))
        # 项目 B 的工作集引用项目 A 的剧本 → 拒绝
        workset = StoryWorkset(
            project_id=project_b,
            story_bible_artifact_id=sb_b.id,
            outline_artifact_id=outline_a.id,
            scripts={1: s1.id},
        )
        with pytest.raises(StoryStateGapError, match="不属于项目"):
            await service.ensure_state_through(db_session, workset, 1)

        # 正常路径:项目 A 派生,项目 B 状态不含 A 的证据
        workset_a = StoryWorkset(
            project_id=project_a,
            story_bible_artifact_id=sb_a.id,
            outline_artifact_id=outline_a.id,
            scripts={1: s1.id},
        )
        outcome = await service.ensure_state_through(db_session, workset_a, 1)
        assert outcome.state.basis is not None
        assert str(s1.id) in outcome.state.basis.script_artifact_ids.values()

    async def test_prop_and_relationship_enter_state_validated(
        self, db_session: Any
    ) -> None:
        """关系/道具/知识全部经 Pydantic 完整校验后进入状态。"""
        artifact_service = ArtifactService()
        project_id, sb, outline = await _seed_project_with_bible(
            db_session, artifact_service
        )
        s1 = await _add_script(db_session, artifact_service, project_id, 1)
        s2 = await _add_script(db_session, artifact_service, project_id, 2)
        llm = _DeltaFakeLLM()
        service = StoryStateService(BaseAgent(name="summarizer", llm=llm))
        workset = StoryWorkset(
            project_id=project_id,
            story_bible_artifact_id=sb.id,
            outline_artifact_id=outline.id,
            scripts={1: s1.id, 2: s2.id},
        )
        outcome = await service.ensure_state_through(db_session, workset, 2)
        # 持久化的 continuity_state 能用领域模型完整重读
        from app.domain.continuity import ContinuityState as ContinuityStateModel

        head_id = uuid.UUID(outcome.state_artifact_id)
        artifact = await artifact_service.get_version(db_session, head_id)
        reloaded = ContinuityStateModel.model_validate(artifact.content)
        assert reloaded.content_schema_version == "2.0"
        assert reloaded.props["prop_01_01"].holder_character_id == "char_lin"
        assert len(reloaded.character_states["char_lin"].known_facts) == 1


@pytest.mark.integration
class TestStoryStateDedupIndex:
    async def test_concurrent_duplicate_input_hits_unique_or_dedup(
        self, db_session: Any
    ) -> None:
        """同输入重复保存:应用层 input_hash 去重(0013 索引兜底并发)。"""
        from sqlalchemy import select

        artifact_service = ArtifactService()
        project_id, sb, outline = await _seed_project_with_bible(
            db_session, artifact_service
        )
        s1 = await _add_script(db_session, artifact_service, project_id, 1)
        llm = _DeltaFakeLLM()
        service = StoryStateService(BaseAgent(name="summarizer", llm=llm))
        workset = StoryWorkset(
            project_id=project_id,
            story_bible_artifact_id=sb.id,
            outline_artifact_id=outline.id,
            scripts={1: s1.id},
        )
        await service.ensure_state_through(db_session, workset, 1)
        await service.ensure_state_through(db_session, workset, 1)

        summaries = (await db_session.execute(
            select(Artifact).where(
                Artifact.project_id == project_id,
                Artifact.type == "episode_summary",
            )
        )).scalars().all()
        assert len(summaries) == 1
        states = (await db_session.execute(
            select(Artifact).where(
                Artifact.project_id == project_id,
                Artifact.type == "continuity_state",
                Artifact.episode_number == 1,
            )
        )).scalars().all()
        assert len(states) == 1
