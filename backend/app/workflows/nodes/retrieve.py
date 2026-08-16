"""retrieve 节点 — 知识库检索 (D-05).

从归一化需求 Artifact 构建查询，按创作阶段（StoryBible / 大纲 / 剧本写作）
各检索一次知识库，把分阶段 rag_context 文本写入 ctx（stage 键），
并保持合并文本向后兼容（ctx["rag_context"]）；每阶段持久化一个
RetrievalTrace Artifact（query / chunk IDs / scores / filters / corpus_version，
不含全文）供追溯。

降级保障（设计决策 6）：
- 检索失败（Embedder 网络异常 / 语料未摄取 / DB 不可用）→ 该阶段回退空字符串，
  主流程不中断；
- ctx 注入 NullRetriever 时同样返回空结果，"删除 RAG 后主流程仍可运行"。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langgraph.config import get_config

from app.core.config import load_settings
from app.db.repositories.knowledge import KnowledgeRepository
from app.domain.requirement import NormalizedRequirement
from app.domain.retrieval import RetrievalResult
from app.events.publisher import EventPublisher
from app.rag.embedder import Embedder, load_embedder
from app.rag.retriever import Retriever
from app.workflows.state import CreationState

logger = logging.getLogger(__name__)

# 三阶段检索顺序（键名与 ctx 中 stage 键一致）
_CREATION_STAGES = ("story_bible", "outline", "writer")


def _ctx() -> dict[str, Any]:
    return get_config()["configurable"]


async def _build_query(
    db: Any,
    artifact_svc: Any,
    state: CreationState,
    fallback: str,
) -> tuple[str, str | None, int]:
    """从归一化需求 Artifact 构建检索 query。

    Returns:
        (query, requirement_artifact_id, requirement_version)；
        无需求 Artifact 时回退到用户原始输入（fallback）。
    """
    req_artifact_id = state.get("requirement_artifact_id")
    if not req_artifact_id:
        return fallback, None, 1

    req_artifact = await artifact_svc.get_version(db, uuid.UUID(req_artifact_id))
    req = NormalizedRequirement.model_validate(req_artifact.content)
    parts = [
        req.genre,
        *req.tone,
        req.title,
        req.logline,
        req.protagonist_seed,
        req.conflict_seed,
        *req.must_have,
        *req.must_avoid,
    ]
    query = " ".join(str(p).strip() for p in parts if str(p).strip())
    return query, req_artifact_id, req_artifact.version


def _format_stage_context(stage: str, result: RetrievalResult) -> str:
    """把检索结果格式化为 Prompt 可用的知识库参考文本。

    每片段一行引用头（短 ID + 来源标题 + 分类），随后是正文。
    模板已含 "## 知识库参考" 标题，此处不重复。
    """
    if not result.chunks:
        return ""
    lines: list[str] = []
    for chunk in result.chunks:
        lines.append(f"[{chunk.id}] 来源: 《{chunk.title}》({chunk.category})")
        lines.append(chunk.content.strip())
        lines.append("")
    return "\n".join(lines).strip()


async def _persist_trace(
    db: Any,
    artifact_svc: Any,
    *,
    project_id: uuid.UUID,
    stage: str,
    result: RetrievalResult,
    requirement_artifact_id: str | None,
    requirement_version: int,
) -> None:
    """持久化一个 RetrievalTrace Artifact（记录检索了什么、命中了什么）。"""
    source_artifact_ids = None
    if requirement_artifact_id:
        source_artifact_ids = [
            {
                "artifact_id": requirement_artifact_id,
                "version": requirement_version,
                "relation": "derived_from",
            }
        ]
    trace = result.to_trace()
    await artifact_svc.create_validated_artifact(
        db,
        project_id=project_id,
        artifact_type="retrieval_trace",
        content={**trace.model_dump(), "stage": stage},
        source_artifact_ids=source_artifact_ids,
        # 三阶段 trace 共享同一 source（需求 Artifact）——必须用 dedup_extra 区分，
        # 否则 input_hash 相同会被幂等去重成同一条记录
        dedup_extra=stage,
    )


async def retrieve_node(state: CreationState) -> dict[str, Any]:
    """按创作阶段真实检索知识库，并把分阶段上下文写入 ctx。"""
    ctx = _ctx()
    db = ctx["db"]
    publisher: EventPublisher = ctx["event_publisher"]
    artifact_svc = ctx["artifact_service"]
    run_id = uuid.UUID(state["run_id"])
    project_id = uuid.UUID(state["project_id"])
    progress = ctx.get("progress_callback", lambda *a: None)

    if "retrieve" in state.get("completed_nodes", []):
        return {}

    await publisher.publish(
        db, run_id=run_id, event_type="node.started",
        payload={"node": "retrieve", "progress": 0.0},
        autocommit=True,
    )
    progress("retrieve", "started", 0.0)

    # 检索失败整体降级为空上下文，保证主流程不中断
    stage_texts: dict[str, str] = {}
    stage_hits: dict[str, int] = {}
    stage_chunk_ids: dict[str, list[str]] = {}
    owned_embedder: Embedder | None = None
    try:
        settings = load_settings()
        query, req_artifact_id, req_version = await _build_query(
            db, artifact_svc, state, ctx.get("user_input", "")
        )

        # 支持 DI 注入 retriever（如 NullRetriever 降级）；否则按 db 自建
        retriever = ctx.get("retriever")
        if retriever is None:
            embedder = load_embedder(settings)
            owned_embedder = embedder
            retriever = Retriever(KnowledgeRepository(db), embedder)

        for stage in _CREATION_STAGES:
            try:
                result = await retriever.retrieve_for_stage(
                    stage, query, top_k=settings.rag_top_k,
                )
                stage_texts[stage] = _format_stage_context(stage, result)
                stage_hits[stage] = len(result.chunks)
                # G-02: 记录本阶段命中的 chunk UUID，供 ContextBuilder 回填
                # ContextManifest.rag_chunk_ids（RetrievalResult.chunks[i].chunk_id）。
                stage_chunk_ids[stage] = [str(c.chunk_id) for c in result.chunks]
                if result.chunks:
                    await _persist_trace(
                        db, artifact_svc,
                        project_id=project_id, stage=stage, result=result,
                        requirement_artifact_id=req_artifact_id,
                        requirement_version=req_version,
                    )
            except Exception:
                logger.exception("阶段 %s 知识库检索失败，降级为空上下文", stage)
                stage_texts[stage] = ""
                stage_hits[stage] = 0
    except Exception:
        logger.exception("知识库检索初始化失败，降级为空上下文")
    finally:
        if owned_embedder is not None:
            await owned_embedder.close()

    # 写入分阶段键（story_bible_rag / outline_rag / writer_rag）
    # 与合并键（rag_context，向后兼容）；以及分阶段 chunk IDs（G-02）
    for stage, text in stage_texts.items():
        ctx[f"{stage}_rag"] = text
        ctx[f"{stage}_rag_chunk_ids"] = stage_chunk_ids.get(stage, [])
    ctx["rag_context"] = "\n\n".join(t for t in stage_texts.values() if t)

    total_hits = sum(stage_hits.values())
    await publisher.publish(
        db, run_id=run_id, event_type="node.completed",
        payload={"node": "retrieve", "doc_count": total_hits, "progress": 1.0},
        autocommit=True,
    )
    progress("retrieve", "completed", 1.0)

    return {"completed_nodes": state.get("completed_nodes", []) + ["retrieve"]}
