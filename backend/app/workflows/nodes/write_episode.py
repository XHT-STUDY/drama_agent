"""write_episode 节点 — 按集顺序生成剧本 (C-07 / G-02 / M-04 W3-03).

M-04 契约(docs/MEMORY_IMPLEMENTATION_PLAN.md §6):
- 进入第 N 集前经 StoryStateService 加载 through=N-1 的确切前态
  (按 Run 工作集:StoryBible/大纲/既有各集剧本 Artifact);
- 已有剧本只跳过正文生成,不跳过派生证据完整性检查;
- 每集正文保存后立即派生(单集证据 + 新状态);派生失败保留正文,
  记录 derivation_pending_episode,Run 失败待 retry 只补派生;
- Writer 上下文使用 v2 结构化状态投影(作者事实/角色知识/伏笔/道具
  /时间线/锁定事实/作者计划分账),会话记忆为 M-02 有界合并;
- 一次写 5 集与分批续写(1+1+3)使用同一前态语义——前缀证据复用。
"""

from __future__ import annotations

import dataclasses
import json as _json
import logging
import uuid
from typing import Any

from langgraph.config import get_config

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.application.story_state_service import (
    DerivationOutcome,
    StoryStateService,
    StoryWorkset,
)
from app.core.config import load_settings
from app.domain.context import TaskKind
from app.domain.outline import EpisodeOutlineSet
from app.domain.script import EpisodeWriterInput
from app.domain.story_bible import StoryBible
from app.events.publisher import EventPublisher
from app.memory.context_builder import ContextBuilder
from app.memory.continuity import ContinuityManager
from app.memory.summary import latest_project_summary_text
from app.prompts.loader import PromptLoader
from app.skills.episode_writer import EpisodeWriterSkill
from app.workflows.checkpoint import node_failure, raise_if_cancelled
from app.workflows.state import CreationState

logger = logging.getLogger(__name__)
_MVP_DEFAULT_SCRIPT_COUNT = 3  # 默认值，实际从 workflow config 读取


def _ctx() -> dict[str, Any]:
    return get_config()["configurable"]


async def write_episodes_node(state: CreationState) -> dict[str, Any]:
    """按 1..N 顺序撰写各集剧本草稿,并逐集推进剧情证据链。"""
    ctx = _ctx()
    db = ctx["db"]
    agent: BaseAgent = ctx["agent"]
    prompt_loader: PromptLoader = ctx["prompt_loader"]
    artifact_svc: ArtifactService = ctx["artifact_service"]
    publisher: EventPublisher = ctx["event_publisher"]
    project_id = uuid.UUID(state["project_id"])
    run_id = uuid.UUID(state["run_id"])
    progress = ctx.get("progress_callback", lambda *a: None)

    # 协作式取消守卫（I-01）
    raise_if_cancelled(state["run_id"])

    # 失败短路（I-01）：上游节点已失败则跳过本节点，保持失败状态不变
    if state.get("status") == "failed":
        return {}

    if "write_episodes" in state.get("completed_nodes", []):
        return {}

    sb_artifact = await artifact_svc.get_version(db, uuid.UUID(state["story_bible_artifact_id"]))
    story_bible = StoryBible.model_validate(sb_artifact.content)
    outline_artifact = await artifact_svc.get_version(db, uuid.UUID(state["outline_set_artifact_id"]))
    outline_set = EpisodeOutlineSet.model_validate(outline_artifact.content)
    outline_aid = outline_artifact.id

    character_names = {
        ch["character_id"]: ch["name"]
        for ch in [
            story_bible.protagonist.model_dump(),
            story_bible.antagonist.model_dump(),
            *(c.model_dump() for c in story_bible.supporting_characters),
        ]
    }

    existing_scripts: dict[str, str] = state.get("script_artifact_ids", {})
    start_ep = state.get("current_episode", 1)
    completed_scripts: dict[str, str] = {}
    derivation_pending: int | None = None

    # 从 workflow config 读取 script_count（兼容旧 config 无此字段）
    script_count = ctx.get("script_count", _MVP_DEFAULT_SCRIPT_COUNT)
    if script_count < 1:
        script_count = _MVP_DEFAULT_SCRIPT_COUNT

    # M-04:冻结本次 Run 的作品工作集(既有各集剧本 → 确切前态链)
    state_service = StoryStateService(agent)
    workset = StoryWorkset(
        project_id=project_id,
        story_bible_artifact_id=sb_artifact.id,
        outline_artifact_id=outline_artifact.id,
        scripts={
            int(n): uuid.UUID(aid) for n, aid in existing_scripts.items()
        },
    )

    logger.info(
        "开始撰写剧本: 从第 %d 集到第 %d 集 (共 %d 集)",
        start_ep, script_count, script_count - start_ep + 1,
    )

    await publisher.publish(
        db, run_id=run_id, event_type="node.started",
        payload={
            "node": "write_episodes",
            "start_episode": start_ep,
            "script_count": script_count,
            "progress": 0.40,
        },
        autocommit=True,
    )
    progress("write_episodes", "started", 0.40)

    # except 分支引用的结果字段先初始化(失败可发生在任何阶段)
    summary_ids: dict[str, str] = {}
    state_artifact_id: str | None = None

    try:
        skill = EpisodeWriterSkill()
        prompt_version = prompt_loader.get("write_episode").version
        # 1) 已有集:只补派生证据完整性,不重新生成正文(W3-03)
        pre_outcome: DerivationOutcome | None = None
        if workset.scripts:
            pre_outcome = await state_service.ensure_state_through(
                db, workset, max(workset.scripts)
            )
            summary_ids = {
                str(n): aid for n, aid in
                pre_outcome.episode_summary_artifact_ids.items()
            }
            for n in sorted(workset.scripts):
                completed_scripts[str(n)] = existing_scripts[str(n)]

        current_state: Any = None
        if pre_outcome is not None:
            current_state = pre_outcome.state
            state_artifact_id = pre_outcome.state_artifact_id
        else:
            initial = await state_service.ensure_state_through(db, workset, 0)
            current_state = initial.state

        for ep_num in range(start_ep, script_count + 1):
            if str(ep_num) in existing_scripts:
                continue

            # 多 Artifact 循环内的取消守卫：本集 Artifact 写入前检查
            raise_if_cancelled(state["run_id"])

            episode_outline = _find_ep_outline(outline_set, ep_num)
            if episode_outline is None:
                raise ValueError(f"大纲中未找到第 {ep_num} 集")

            # ---- v2 前态投影(through=ep_num-1;不含本集候选稿) ----
            continuity_text = ContinuityManager.get_context_for_episode_v2(
                current_state, ep_num, character_names=character_names
            )
            previous_summary = ""
            if ep_num > 1:
                prev = [s for s in current_state.episode_summaries
                        if s.episode_number == ep_num - 1]
                if prev:
                    previous_summary = prev[0].summary

            # ---- M-02:会话记忆有界合并(中期记忆进创作上下文) ----
            conversation_summary = await latest_project_summary_text(
                db, artifact_svc, project_id
            )
            previous_summary_continuity = "\n\n".join(
                filter(None, [conversation_summary, continuity_text])
            )

            rag_context = ctx.get("writer_rag") or ctx.get("rag_context", "")
            rag_chunk_ids = list(ctx.get("writer_rag_chunk_ids") or [])

            # ContextBuilder 按 writer 策略组装上下文：current_target（本集大纲）
            # 完整保留，超预算裁剪辅助段；放不下本集大纲则抛 ContextTooLargeError。
            builder = ContextBuilder(
                budget_tokens=load_settings().context_max_tokens
            )
            assembled_context, context_manifest = builder.build_for(
                TaskKind.WRITER,
                system_rules="",  # 静态系统规则已内嵌在模板中，不重复组装
                user_request=ctx.get("user_input", ""),
                story_bible_outline=_json.dumps(
                    story_bible.model_dump(), ensure_ascii=False
                ),
                previous_summary_continuity=previous_summary_continuity,
                rag_fragments=rag_context,
                rag_chunk_ids=rag_chunk_ids,
                current_target=_json.dumps(episode_outline, ensure_ascii=False),
            )
            logger.info(
                "第 %d 集上下文组装: task=%s used=%s cut=%s rag_chunks=%d",
                ep_num, context_manifest.task,
                context_manifest.sections_used, context_manifest.sections_cut,
                len(context_manifest.rag_chunk_ids),
            )

            ew_input = EpisodeWriterInput(
                episode_number=ep_num,
                episode_outline=episode_outline,
                story_bible=story_bible.model_dump(),
                previous_summary=previous_summary,
                continuity_state=continuity_text,
                rag_context=rag_context,
                assembled_context=assembled_context,
            )

            logger.info("正在调用 LLM 生成第 %d 集剧本…", ep_num)
            draft = await skill.execute({
                "input": ew_input, "agent": agent, "prompt_loader": prompt_loader,
                "outline_artifact_id": outline_aid,
            })
            logger.info("第 %d 集 LLM 生成完成，开始后处理…", ep_num)

            # 1. 序列化 + 持久化正文(提交点一:正文先行,派生失败不回滚)
            content = draft.model_dump(mode="json")
            artifact = await artifact_svc.create_validated_artifact(
                db, project_id=project_id, artifact_type="script_draft",
                episode_number=ep_num, content=content, prompt_version=prompt_version,
                source_artifact_ids=[
                    {
                        "artifact_id": state["outline_set_artifact_id"],
                        "version": outline_artifact.version,
                        "relation": "derived_from",
                    },
                    {
                        "artifact_id": state["story_bible_artifact_id"],
                        "version": sb_artifact.version,
                        "relation": "references",
                    },
                ],
            )
            completed_scripts[str(ep_num)] = str(artifact.id)
            workset = dataclasses.replace(
                workset, scripts={**workset.scripts, ep_num: artifact.id}
            )

            # 2. 派生证据(提交点二:失败保留正文,记录待补集号)
            try:
                outcome = await state_service.ensure_state_through(
                    db, workset, ep_num
                )
                current_state = outcome.state
                state_artifact_id = outcome.state_artifact_id
                summary_ids = {
                    str(n): aid
                    for n, aid in outcome.episode_summary_artifact_ids.items()
                }
            except Exception as deriv_error:  # noqa: BLE001 — 分账保留正文
                logger.error(
                    "第 %d 集派生失败,正文已保留(derivation_pending): %s",
                    ep_num, deriv_error,
                )
                derivation_pending = ep_num
                raise

            # 3. 发布事件 + 提交
            await publisher.publish(
                db, run_id=run_id, event_type="artifact.created",
                payload={
                    "artifact_id": str(artifact.id), "artifact_type": "script_draft",
                    "episode": ep_num, "version": artifact.version,
                    "progress": 0.40 + ep_num * 0.15,
                    "message": f"第 {ep_num} 集剧本已完成",
                },
                autocommit=True,
            )
            progress("write_episodes", f"ep_{ep_num}_done", 0.40 + ep_num * 0.15)

        continuity_text = ContinuityManager.get_context_for_episode_v2(
            current_state, script_count + 1, character_names=character_names
        )

        await publisher.publish(
            db, run_id=run_id, event_type="node.completed",
            payload={"node": "write_episodes", "episodes_written": len(completed_scripts), "progress": 0.85},
            autocommit=True,
        )
        progress("write_episodes", "completed", 0.85)

        return {
            "script_artifact_ids": completed_scripts,
            "continuity_state_text": continuity_text,
            "continuity_state_artifact_id": state_artifact_id,
            "episode_summary_artifact_ids": summary_ids,
            "story_state_semantics_version": 2,
            "completed_nodes": state.get("completed_nodes", []) + ["write_episodes"],
            "prompt_versions": {**state.get("prompt_versions", {}), "write_episode": prompt_version},
        }
    except Exception as e:
        logger.exception("剧本撰写失败")
        await publisher.publish(
            db, run_id=run_id, event_type="node.failed",
            payload={"node": "write_episodes", "error": str(e)},
            autocommit=True,
        )
        failure = node_failure("write_episodes", e)
        if derivation_pending is not None:
            failure["derivation_pending_episode"] = derivation_pending
        return {
            **failure,
            # 保留已写入的剧本，retry 时不重复调用已完成集
            "script_artifact_ids": completed_scripts,
            "episode_summary_artifact_ids": summary_ids,
            "continuity_state_artifact_id": state_artifact_id,
            "story_state_semantics_version": 2,
        }


def _find_ep_outline(outline_set: EpisodeOutlineSet, ep_num: int) -> dict[str, Any] | None:
    for ep in outline_set.episodes:
        if ep.episode_number == ep_num:
            return ep.model_dump()
    return None
