"""normalize 节点 — 需求归一化 (C-07).

将用户输入归一化为 NormalizedRequirement Artifact。
关键信息缺失时标记 needs_user_input，不阻断工作流。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langgraph.config import get_config

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.domain.requirement import RequirementInput
from app.events.publisher import EventPublisher
from app.prompts.loader import PromptLoader
from app.skills.requirement import RequirementSkill
from app.workflows.checkpoint import node_failure, raise_if_cancelled
from app.workflows.state import CreationState

logger = logging.getLogger(__name__)


def _get_node_context() -> dict[str, Any]:
    """从 LangGraph 运行时获取 configurable 上下文。"""
    return get_config()["configurable"]


async def normalize_node(state: CreationState) -> dict[str, Any]:
    """执行需求归一化。

    调用 RequirementSkill 生成 NormalizedRequirement，
    持久化为 Artifact 并按需发布事件。

    验收项：
    - 足球 Idea 生成合法结构
    - 关键信息缺失时标记 needs_user_input
    """
    ctx = _get_node_context()
    db = ctx["db"]
    agent: BaseAgent = ctx["agent"]
    prompt_loader: PromptLoader = ctx["prompt_loader"]
    artifact_svc: ArtifactService = ctx["artifact_service"]
    publisher: EventPublisher = ctx["event_publisher"]
    project_id = uuid.UUID(state["project_id"])
    run_id = uuid.UUID(state["run_id"])
    progress = ctx.get("progress_callback", lambda *a: None)

    # 协作式取消守卫（I-01）：已请求取消则中断，不创建新 Artifact
    raise_if_cancelled(state["run_id"])

    # 失败短路（I-01）：上游节点已失败则跳过本节点，保持失败状态不变
    if state.get("status") == "failed":
        return {}

    # 跳过已完成的节点（重试场景）
    if "normalize" in state.get("completed_nodes", []):
        logger.info("节点已跳过（已完成）: normalize")
        return {}

    logger.info("开始需求归一化: project=%s run=%s", project_id, run_id)

    # 发布 node.started
    await publisher.publish(
        db, run_id=run_id, event_type="node.started",
        payload={"node": "normalize", "progress": 0.0},
        autocommit=True,
    )
    progress("normalize", "started", 0.0)

    try:
        # 构造输入
        user_input = ctx.get("user_input", "")
        source_type = ctx.get("source_type", "idea")

        req_input = RequirementInput(
            user_input=user_input,
            source_type=source_type,
        )

        skill = RequirementSkill()
        result = await skill.execute({
            "input": req_input,
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        # 检查是否为 NeedsUserInput（关键信息缺失）
        from app.domain.requirement import NeedsUserInput
        if isinstance(result, NeedsUserInput):
            logger.warning("需求信息不足，需要用户补充: %s", result.missing_fields)
            await publisher.publish(
                db, run_id=run_id, event_type="node.completed",
                payload={
                    "node": "normalize",
                    "needs_user_input": True,
                    "missing_fields": result.missing_fields,
                    "progress": 1.0,
                },
                autocommit=True,
            )
            return {
                "needs_user_input": True,
                "status": "needs_user_input",
                "completed_nodes": state.get("completed_nodes", []) + ["normalize"],
            }

        # 持久化为 Artifact
        content = result.model_dump()
        artifact = await artifact_svc.create_validated_artifact(
            db,
            project_id=project_id,
            artifact_type="normalized_requirement",
            content=content,
            prompt_version=prompt_loader.get("normalize_requirement").version,
        )

        await publisher.publish(
            db, run_id=run_id, event_type="node.completed",
            payload={
                "node": "normalize",
                "artifact_id": str(artifact.id),
                "artifact_type": "normalized_requirement",
                "progress": 1.0,
            },
            autocommit=True,
        )
        progress("normalize", "completed", 1.0)

        logger.info("需求归一化完成: artifact=%s", artifact.id)

        return {
            "requirement_artifact_id": str(artifact.id),
            "completed_nodes": state.get("completed_nodes", []) + ["normalize"],
            "prompt_versions": {
                **state.get("prompt_versions", {}),
                "normalize_requirement": prompt_loader.get("normalize_requirement").version,
            },
        }

    except Exception as e:
        logger.exception("需求归一化失败")
        await publisher.publish(
            db, run_id=run_id, event_type="node.failed",
            payload={"node": "normalize", "error": str(e)},
            autocommit=True,
        )
        return node_failure("normalize", e)
