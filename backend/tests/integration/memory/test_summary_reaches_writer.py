"""G-02 Exit Gate：多轮会话摘要进入 write_episode 创作上下文。

前置条件：Docker PostgreSQL 就绪（make up），FakeLLM 驱动。

验证路径（真实调用链）：
1. 多轮对话（MessageService.append 超阈值）→ ConversationSummary Artifact 落库；
2. write_episode 节点用 ContextBuilder.build_for("writer", ...) 组装上下文，
   previous_summary_continuity 段 = 会话摘要 + ContinuityManager 连续性；
3. 断言捕获到的 EpisodeWriterInput.assembled_context 含摘要文本——
   证明「旧会话优先摘要」真正到达创作 Prompt。
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
from app.application.conversation_service import MessageService
from app.application.run_service import RunService
from app.domain.conversation import MessageCreate
from app.domain.script import EpisodeWriterInput, ScriptDraft
from app.events.publisher import EventPublisher
from app.llm.fake import FakeLLM
from app.memory.short_term import InMemoryShortTermStore
from app.memory.summary import ConversationSummaryManager
from app.prompts.loader import PromptLoader
from app.skills.episode_writer import EpisodeWriterSkill
from app.workflows.nodes.write_episode import write_episodes_node
from app.workflows.state import CreationState

_GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"

_SUMMARY_TEXT = "用户与助手确认了主角设定与题材方向，期待逆袭爽点。"


def _load_golden(name: str) -> dict[str, Any]:
    with open(_GOLDEN_DIR / f"{name}.json", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "expected_output" in data:
        return cast(dict[str, Any], data["expected_output"])
    return cast(dict[str, Any], data)


async def _seed_artifacts(
    db: Any,
    artifact_service: ArtifactService,
    project_id: uuid.UUID,
) -> tuple[Any, Any]:
    """写入 StoryBible 与分集大纲 Artifact（golden fixture）。"""
    sb = await artifact_service.create_validated_artifact(
        db,
        project_id=project_id,
        artifact_type="story_bible",
        content=_load_golden("story_bible_football"),
    )
    outline = await artifact_service.create_validated_artifact(
        db,
        project_id=project_id,
        artifact_type="episode_outline_set",
        content=_load_golden("outline_set_valid"),
    )
    return sb, outline


@pytest.mark.integration
class TestSummaryReachesWriter:
    """多轮会话摘要 → write_episode 上下文（G-02 Exit Gate）。"""

    @pytest.mark.asyncio
    async def test_conversation_summary_reaches_writer_context(
        self,
        db_session: Any,
        test_project: uuid.UUID,
        test_conversation: uuid.UUID,
        fake_llm: FakeLLM,
        agent: BaseAgent,
        prompt_loader: PromptLoader,
        artifact_service: ArtifactService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # 0) 注册 write_episode golden fixture（skill 需走通 LLM 调用）
        fake_llm.register(
            "write_episode",
            ScriptDraft.model_validate(_load_golden("script_draft_valid")),
        )

        # 1) 多轮对话超阈值 → 生成会话摘要（真实 MessageService 链路）
        manager = ConversationSummaryManager(
            agent, prompt_loader, artifact_service, threshold=3, window=1
        )
        svc = MessageService(
            short_term_store=InMemoryShortTermStore(keep_count=12),
            summary_manager=manager,
        )
        for seq, content in enumerate(
            ["我们想做一个足球少年逆袭的短剧。",
             "主角最好是被青训队淘汰的那种。",
             "希望结尾有爽点打脸情节。"],
            start=1,
        ):
            resp = await svc.append(
                db_session, test_conversation,
                MessageCreate(role="user", content=content),
            )
            assert resp.sequence == seq
        await db_session.flush()

        # 摘要已落库且属于本项目
        summaries = await artifact_service.list_by_project(
            db_session, test_project,
            artifact_type="conversation_summary", offset=0, limit=100,
        )
        assert summaries["total"] == 1
        assert summaries["items"][0]["content"]["summary"] == _SUMMARY_TEXT

        # 2) 准备 run + 创作素材
        run_svc = RunService()
        run = await run_svc.create_run(
            db_session, project_id=test_project, action="create_script"
        )
        sb_artifact, outline_artifact = await _seed_artifacts(
            db_session, artifact_service, test_project
        )
        await db_session.flush()

        # 3) 捕获 skill 收到的 EpisodeWriterInput（验证上下文内容）
        captured: list[EpisodeWriterInput] = []
        orig_execute = EpisodeWriterSkill.execute

        async def _spy(self: Any, context: dict[str, Any]) -> ScriptDraft:
            captured.append(context["input"])
            return await orig_execute(self, context)

        monkeypatch.setattr(EpisodeWriterSkill, "execute", _spy)

        # 4) 单独运行 write_episodes 节点（只写 1 集，足够验证）
        state: CreationState = {
            "run_id": str(run.id),
            "project_id": str(test_project),
            "action": "create_script",
            "story_bible_artifact_id": str(sb_artifact.id),
            "outline_set_artifact_id": str(outline_artifact.id),
            "script_artifact_ids": {},
            "continuity_state_text": "",
            "current_episode": 1,
            "status": "running",
            "needs_user_input": False,
            "completed_nodes": [],
            "input_hashes": {},
            "prompt_versions": {},
        }
        ctx: dict[str, Any] = {
            "configurable": {
                "db": db_session,
                "agent": agent,
                "prompt_loader": prompt_loader,
                "artifact_service": artifact_service,
                "run_service": run_svc,
                "event_publisher": EventPublisher(),
                "user_input": "写一个足球少年逆袭的短剧。",
                "script_count": 1,
                "rag_context": "",
                "progress_callback": lambda *a: None,
            },
        }

        graph = StateGraph(CreationState)
        graph.add_node("write_episodes", write_episodes_node)
        graph.add_edge(START, "write_episodes")
        graph.add_edge("write_episodes", END)
        await graph.compile().ainvoke(state, ctx)  # type: ignore[call-overload]

        # 5) Exit Gate 断言：摘要文本进入 write_episode 的组装上下文
        assert captured, "write_episode 节点应调用 skill"
        assembled = captured[0].assembled_context
        assert assembled, "write_episode 应注入 assembled_context"
        assert _SUMMARY_TEXT in assembled, (
            "会话摘要未进入 write_episode 上下文——「旧会话优先摘要」未生效"
        )
        # 组装上下文同时包含本集大纲（current_target）与连续性头
        assert "## 当前任务目标" in assembled
        assert "## 连续性状态" in assembled

        # 剧本仍正常产出（skill 走通，未被上下文改动破坏）
        script_ids = await artifact_service.list_by_project(
            db_session, test_project, artifact_type="script_draft", offset=0, limit=100,
        )
        assert script_ids["total"] == 1
