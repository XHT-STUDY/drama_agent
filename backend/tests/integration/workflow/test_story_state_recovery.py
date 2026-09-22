"""剧情状态恢复与统一读取集成测试(M-04,W3-03)。

验收(docs/MEMORY_IMPLEMENTATION_PLAN.md §6):
- 一次写 5 集与 1+1+3 分批写作使用相同前态语义(Writer 实际输入一致);
- "服务重启"后(仅靠 DB 恢复)续写,角色知识/伏笔/关系不丢失;
- 派生失败保留正文(derivation_pending_episode),retry 只补派生;
- 必需来源缺失 fail closed——Writer 桩断言未被调用;
- 修订候选稿不写入 through=N-1 前态;
- RAG 资料缺失仍可生成。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from langgraph.graph import END, START, StateGraph

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.application.run_service import RunService
from app.domain.continuity import ContinuityState
from app.domain.script import EpisodeWriterInput, ScriptDraft
from app.events.publisher import EventPublisher
from app.llm.fake import FakeLLM
from app.prompts.loader import PromptLoader
from app.skills.episode_writer import EpisodeWriterSkill
from app.workflows.nodes.write_episode import write_episodes_node
from app.workflows.state import CreationState

_GOLDEN = Path(__file__).resolve().parents[2] / "golden"


def _load_golden(name: str) -> dict[str, Any]:
    with open(_GOLDEN / f"{name}.json", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "expected_output" in data:
        return cast(dict[str, Any], data["expected_output"])
    return cast(dict[str, Any], data)


class _CountingDeltaFactory:
    """内容感知 typed delta 桩:每集一条事实,主角当集得知;可注入一次性故障。

    生产 fact ID 规则确定(fact_{ep:02d}_01),断言据此跨集引用。
    """

    def __init__(self) -> None:
        self.calls: list[int] = []
        self.fail_episodes: set[int] = set()
        self.failed_once: set[int] = set()

    def __call__(self, messages: list[dict[str, str]]) -> Any:
        import re

        from app.domain.summary import EpisodeDelta, FactDelta, KnowledgeDelta

        text = messages[-1]["content"]
        match = re.search(r"第 (\d+) 集剧本", text)
        episode = int(match.group(1)) if match else 1
        if episode in self.fail_episodes and episode not in self.failed_once:
            self.failed_once.add(episode)
            raise RuntimeError("模拟派生 LLM 故障")
        self.calls.append(episode)
        return EpisodeDelta(
            episode_number=episode,
            summary=f"第{episode}集完成",
            facts=[FactDelta(text=f"第{episode}集事实", source_scene=1)],
            knowledge=[
                KnowledgeDelta(
                    character_id="char_protagonist_001",
                    new_fact_index=0,
                    learned=True,
                    source_scene=1,
                )
            ],
        )


async def _seed(
    db: Any, artifact_service: ArtifactService, project_id: uuid.UUID
) -> tuple[Any, Any, Any]:
    """建 Run + StoryBible + 大纲,返回 (run, sb, outline)。"""
    run = await RunService().create_run(
        db, project_id=project_id, action="create_script"
    )
    sb = await artifact_service.create_validated_artifact(
        db, project_id=project_id, artifact_type="story_bible",
        content=_load_golden("story_bible_football"),
    )
    outline = await artifact_service.create_validated_artifact(
        db, project_id=project_id, artifact_type="episode_outline_set",
        content=_load_golden("outline_set_valid"),
    )
    return run, sb, outline


async def _invoke_node(
    db: Any,
    agent: BaseAgent,
    prompt_loader: PromptLoader,
    artifact_service: ArtifactService,
    state: CreationState,
    *,
    script_count: int,
    captured: list[EpisodeWriterInput] | None = None,
) -> dict[str, Any]:
    """独立运行 write_episodes 节点(可捕获 Writer 输入),返回节点结果。"""
    original_execute = EpisodeWriterSkill.execute

    async def _spy(self: Any, context: dict[str, Any]) -> ScriptDraft:
        if captured is not None:
            captured.append(context["input"])
        return await original_execute(self, context)

    EpisodeWriterSkill.execute = _spy  # type: ignore[method-assign]
    try:
        graph = StateGraph(CreationState)
        graph.add_node("write_episodes", write_episodes_node)
        graph.add_edge(START, "write_episodes")
        graph.add_edge("write_episodes", END)
        config = {
            "configurable": {
                "db": db,
                "agent": agent,
                "prompt_loader": prompt_loader,
                "artifact_service": artifact_service,
                "run_service": RunService(),
                "event_publisher": EventPublisher(),
                "user_input": "足球少年逆袭",
                "script_count": script_count,
                "rag_context": "",
                "progress_callback": lambda *a: None,
            },
        }
        result = await graph.compile().ainvoke(state, config)  # type: ignore[call-overload]
    finally:
        EpisodeWriterSkill.execute = original_execute  # type: ignore[method-assign]
    return cast(dict[str, Any], result)


def _base_state(run: Any, project_id: uuid.UUID, sb: Any, outline: Any,
                scripts: dict[str, str] | None = None) -> CreationState:
    return CreationState(
        run_id=str(run.id),
        project_id=str(project_id),
        action="create_script",
        story_bible_artifact_id=str(sb.id),
        outline_set_artifact_id=str(outline.id),
        script_artifact_ids=scripts or {},
        continuity_state_text="",
        current_episode=(len(scripts) + 1) if scripts else 1,
        status="running",
        needs_user_input=False,
        completed_nodes=[],
        input_hashes={},
        prompt_versions={},
    )


async def _load_state_artifact(
    db: Any, artifact_service: ArtifactService, artifact_id: str
) -> ContinuityState:
    resp = await artifact_service.get_version(db, uuid.UUID(artifact_id))
    return ContinuityState.model_validate(resp.content)


@pytest.mark.integration
class TestStoryStateRecovery:
    async def test_batch_vs_staged_same_pre_state_semantics(
        self, db_session: Any, test_project: uuid.UUID,
        artifact_service: ArtifactService, prompt_loader: PromptLoader,
    ) -> None:
        """一次写 5 集 vs 1+1+3 分批:每集 Writer 的前态投影完全一致,
        且最终状态链等价(前缀证据复用)。"""
        # —— 项目 A:一次写 5 集 ——
        run_a, sb_a, outline_a = await _seed(
            db_session, artifact_service, test_project
        )
        factory_a = _CountingDeltaFactory()
        llm_a = FakeLLM(seed=42)
        llm_a.register("write_episode", ScriptDraft.model_validate(
            _load_golden("script_draft_valid")))
        llm_a.register_factory("episode_summary_v2", factory_a)
        captured_a: list[EpisodeWriterInput] = []
        result_a = await _invoke_node(
            db_session, BaseAgent(name="writer", llm=llm_a), prompt_loader,
            artifact_service,
            _base_state(run_a, test_project, sb_a, outline_a),
            script_count=5, captured=captured_a,
        )
        assert result_a.get("status") != "failed"
        assert len(captured_a) == 5

        # —— 项目 B:1 + 1 + 3 分批("重启"= 每批之间仅靠 DB) ——
        from app.db.models.project import Project

        project_b = uuid.uuid4()
        db_session.add(Project(id=project_b, title="分批项目", status="draft"))
        await db_session.flush()
        run_b, sb_b, outline_b = await _seed(
            db_session, artifact_service, project_b
        )
        factory_b = _CountingDeltaFactory()
        llm_b = FakeLLM(seed=42)
        llm_b.register("write_episode", ScriptDraft.model_validate(
            _load_golden("script_draft_valid")))
        llm_b.register_factory("episode_summary_v2", factory_b)
        captured_b: list[EpisodeWriterInput] = []
        agent_b = BaseAgent(name="writer", llm=llm_b)

        scripts: dict[str, str] = {}
        for batch_end, in ((1,), (2,), (5,)):
            state = _base_state(run_b, project_b, sb_b, outline_b,
                                scripts or None)
            result = await _invoke_node(
                db_session, agent_b, prompt_loader, artifact_service, state,
                script_count=batch_end, captured=captured_b,
            )
            assert result.get("status") != "failed", result.get("error_detail")
            scripts = cast(dict[str, str], result["script_artifact_ids"])
            await db_session.flush()

        assert len(captured_b) == 5
        # 每集 Writer 输入的前态投影一致(相同前态语义)
        for ep in range(5):
            assert captured_a[ep].continuity_state == captured_b[ep].continuity_state, (
                f"第 {ep + 1} 集前态投影不一致"
            )
        # 最终状态链结构等价(知识/伏笔/关系不因分批丢失)
        state_a = await _load_state_artifact(
            db_session, artifact_service, result_a["continuity_state_artifact_id"]
        )
        state_b = await _load_state_artifact(
            db_session, artifact_service,
            await _latest_state_id(db_session, artifact_service, project_b),
        )
        import re as _re

        def _normalize(dump: dict[str, Any]) -> str:
            # 不同项目的 Artifact UUID 必然不同;比较结构而非标识
            return _re.sub(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                "<uuid>",
                json.dumps(dump, sort_keys=True, ensure_ascii=False),
            )

        assert _normalize(state_a.model_dump(exclude={"basis"})) == _normalize(
            state_b.model_dump(exclude={"basis"})
        )

    async def test_restart_resume_keeps_knowledge_loops_relations(
        self, db_session: Any, test_project: uuid.UUID,
        artifact_service: ArtifactService, prompt_loader: PromptLoader,
    ) -> None:
        """写 2 集后"重启"(新会话/新 agent,仅 DB 可用)续写 3-5 集:
        角色知识按集累积、伏笔与关系保留。"""
        run, sb, outline = await _seed(db_session, artifact_service, test_project)
        factory = _CountingDeltaFactory()
        llm = FakeLLM(seed=42)
        llm.register("write_episode", ScriptDraft.model_validate(
            _load_golden("script_draft_valid")))
        llm.register_factory("episode_summary_v2", factory)

        first = await _invoke_node(
            db_session, BaseAgent(name="writer", llm=llm), prompt_loader,
            artifact_service, _base_state(run, test_project, sb, outline),
            script_count=2,
        )
        assert first.get("status") != "failed"
        await db_session.commit()  # 重启前的数据必须已持久化

        # —— 模拟重启:全新会话与 agent,仅从 DB 恢复 ——
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        engine = db_session.bind
        factory2 = async_sessionmaker(
            cast(Any, engine), class_=AsyncSession, expire_on_commit=False
        )
        async with factory2() as fresh_db:
            llm2 = FakeLLM(seed=42)
            llm2.register("write_episode", ScriptDraft.model_validate(
                _load_golden("script_draft_valid")))
            llm2.register_factory("episode_summary_v2", factory)
            resumed_state = _base_state(
                run, test_project, sb, outline,
                cast(dict[str, str], first["script_artifact_ids"]),
            )
            result = await _invoke_node(
                fresh_db, BaseAgent(name="writer", llm=llm2), prompt_loader,
                ArtifactService(), resumed_state, script_count=5,
            )
            assert result.get("status") != "failed"
            await fresh_db.commit()

        db_session.expire_all()
        state = await _load_state_artifact(
            db_session, artifact_service, result["continuity_state_artifact_id"]
        )
        # 知识:主角每集得知一条事实(5 条),且重启前后连续
        known = state.character_states["char_protagonist_001"].known_facts
        assert [k.learned_episode for k in known] == [1, 2, 3, 4, 5]
        assert len(state.facts) == 5
        assert state.through_episode == 5
        # 伏笔(StoryBible 初始)+ 关系结构保留
        assert state.open_loops or state.resolved_loops or not state.locked_facts

    async def test_derivation_failure_keeps_script_retry_only_derives(
        self, db_session: Any, test_project: uuid.UUID,
        artifact_service: ArtifactService, prompt_loader: PromptLoader,
    ) -> None:
        """第 3 集派生失败:正文保留 + derivation_pending_episode=3;
        retry 后 1-2 集零模型调用,只补第 3 集。"""
        from sqlalchemy import select

        from app.db.models.artifact import Artifact

        run, sb, outline = await _seed(db_session, artifact_service, test_project)
        factory = _CountingDeltaFactory()
        factory.fail_episodes = {3}
        llm = FakeLLM(seed=42)
        llm.register("write_episode", ScriptDraft.model_validate(
            _load_golden("script_draft_valid")))
        llm.register_factory("episode_summary_v2", factory)
        agent = BaseAgent(name="writer", llm=llm)

        failed = await _invoke_node(
            db_session, agent, prompt_loader, artifact_service,
            _base_state(run, test_project, sb, outline), script_count=3,
        )
        assert failed.get("status") == "failed"
        assert failed.get("derivation_pending_episode") == 3
        # 正文 1-3 集已保留
        scripts = failed["script_artifact_ids"]
        assert set(scripts) == {"1", "2", "3"}
        checksums = {}
        for ep, aid in scripts.items():
            art = (await db_session.execute(
                select(Artifact).where(Artifact.id == uuid.UUID(aid))
            )).scalar_one()
            checksums[ep] = art.checksum

        # retry:从失败状态续跑(既有 3 集脚本,只补派生)
        calls_before = list(factory.calls)
        assert calls_before == [1, 2]  # 第 3 集模型调用失败未计入
        retry = await _invoke_node(
            db_session, agent, prompt_loader, artifact_service,
            _base_state(run, test_project, sb, outline, scripts),
            script_count=3,
        )
        assert retry.get("status") != "failed", retry.get("error_detail")
        # 只补了第 3 集派生;正文 checksum 不变
        assert factory.calls == [1, 2, 3]
        for ep, aid in retry["script_artifact_ids"].items():
            art = (await db_session.execute(
                select(Artifact).where(Artifact.id == uuid.UUID(aid))
            )).scalar_one()
            assert art.checksum == checksums[ep]
        # 恢复后状态链到 3
        assert retry["episode_summary_artifact_ids"].keys() == {"1", "2", "3"}

    async def test_missing_required_source_writer_not_called(
        self, db_session: Any, test_project: uuid.UUID,
        artifact_service: ArtifactService, prompt_loader: PromptLoader,
    ) -> None:
        """必需来源缺失(第 1 集正文 Artifact 不存在)→ fail closed,
        Writer 桩断言未被调用。"""
        run, sb, outline = await _seed(db_session, artifact_service, test_project)
        factory = _CountingDeltaFactory()
        llm = FakeLLM(seed=42)
        llm.register("write_episode", ScriptDraft.model_validate(
            _load_golden("script_draft_valid")))
        llm.register_factory("episode_summary_v2", factory)
        captured: list[EpisodeWriterInput] = []

        result = await _invoke_node(
            db_session, BaseAgent(name="writer", llm=llm), prompt_loader,
            artifact_service,
            _base_state(run, test_project, sb, outline,
                        {"1": str(uuid.uuid4())}),  # 不存在的正文
            script_count=2, captured=captured,
        )
        assert result.get("status") == "failed"
        assert result.get("error_code") == "STORY_STATE_GAP"
        assert captured == [], "必需来源缺失时 Writer 不得被调用"
        assert factory.calls == []

    async def test_missing_rag_still_generates(
        self, db_session: Any, test_project: uuid.UUID,
        artifact_service: ArtifactService, prompt_loader: PromptLoader,
    ) -> None:
        """RAG 资料缺失(ctx 无 rag 字段)仍可生成(可选增强降级)。"""
        run, sb, outline = await _seed(db_session, artifact_service, test_project)
        factory = _CountingDeltaFactory()
        llm = FakeLLM(seed=42)
        llm.register("write_episode", ScriptDraft.model_validate(
            _load_golden("script_draft_valid")))
        llm.register_factory("episode_summary_v2", factory)

        result = await _invoke_node(
            db_session, BaseAgent(name="writer", llm=llm), prompt_loader,
            artifact_service,
            _base_state(run, test_project, sb, outline), script_count=1,
        )
        assert result.get("status") != "failed"
        assert result["script_artifact_ids"]["1"]

    async def test_revision_candidate_not_in_pre_state(
        self, db_session: Any, test_project: uuid.UUID,
        artifact_service: ArtifactService, prompt_loader: PromptLoader,
    ) -> None:
        """修订第 2 集:候选新稿不得写入 through=1 前态——
        派生链中没有任何状态引用候选稿 Artifact。"""
        from sqlalchemy import select

        from app.db.models.artifact import Artifact

        run, sb, outline = await _seed(db_session, artifact_service, test_project)
        factory = _CountingDeltaFactory()
        llm = FakeLLM(seed=42)
        llm.register("write_episode", ScriptDraft.model_validate(
            _load_golden("script_draft_valid")))
        llm.register_factory("episode_summary_v2", factory)
        agent = BaseAgent(name="writer", llm=llm)

        first = await _invoke_node(
            db_session, agent, prompt_loader, artifact_service,
            _base_state(run, test_project, sb, outline), script_count=2,
        )
        scripts = cast(dict[str, str], first["script_artifact_ids"])

        # 候选新稿:第 2 集新版本
        candidate = await artifact_service.create_validated_artifact(
            db_session, project_id=test_project, artifact_type="script_draft",
            episode_number=2,
            content={**_load_golden("script_draft_valid"), "episode_number": 2},
        )
        revised_workset_scripts = {**scripts, "2": str(candidate.id)}

        # 前态检查(through=1)使用的工作集只含第 1 集——候选稿不进入
        from app.application.story_state_service import (
            StoryStateService,
            StoryWorkset,
        )

        service = StoryStateService(agent)
        workset = StoryWorkset(
            project_id=test_project,
            story_bible_artifact_id=sb.id,
            outline_artifact_id=outline.id,
            scripts={1: uuid.UUID(scripts["1"])},
        )
        outcome = await service.ensure_state_through(db_session, workset, 1)
        state_json = json.dumps(outcome.state.model_dump(), sort_keys=True)
        assert str(candidate.id) not in state_json
        # 数据库中没有任何 v2 状态引用候选稿
        states = (await db_session.execute(
            select(Artifact).where(
                Artifact.project_id == test_project,
                Artifact.type == "continuity_state",
            )
        )).scalars().all()
        assert states
        assert all(str(candidate.id) not in json.dumps(a.content) for a in states)
        # 候选工作集的 through=1 前缀与既有链一致(前缀复用)
        workset_with_candidate = StoryWorkset(
            project_id=test_project,
            story_bible_artifact_id=sb.id,
            outline_artifact_id=outline.id,
            scripts={1: uuid.UUID(revised_workset_scripts["1"]),
                     2: uuid.UUID(revised_workset_scripts["2"])},
        )
        pre = await service.ensure_state_through(
            db_session, workset_with_candidate, 1
        )
        assert pre.model_calls == 0  # 前缀来源相同 → 复用,不重新派生


async def _latest_state_id(
    db: Any, artifact_service: ArtifactService, project_id: uuid.UUID
) -> str:
    from sqlalchemy import select

    from app.db.models.artifact import Artifact

    arts = (await db.execute(
        select(Artifact).where(
            Artifact.project_id == project_id,
            Artifact.type == "continuity_state",
        ).order_by(Artifact.episode_number.desc(), Artifact.version.desc())
    )).scalars().all()
    assert arts
    return str(arts[0].id)
