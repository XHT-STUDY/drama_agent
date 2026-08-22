"""ImportFileWorkflow — 上传文件导入分类工作流 (G-04)。

流程：parse（读 FileStore + G-03 Parser 解析）→ classify（规则先行 + LLM 兜底）
→ 持久化 import_classification Artifact → route（确定性路由决策）。

State 只存轻量字段（run/project/upload/classification ID + route），
大文本（文件字节 / 解析文本）不存 State，节点内临时读取。

路由语义（runs.py action="import"）:
- route=create / evaluate → 导入分类完成，Run completed；
- route=hold（reference）→ 仅归档分类，不自动入库创作管线；
- route=needs_user_input（unknown）→ 置 needs_user_input，Run 停在 needs_review。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, TypedDict

from langgraph.config import get_config
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.core.config import load_settings
from app.db.repositories.uploads import UploadRepository
from app.domain.import_file import ImportClassificationInput
from app.events.publisher import EventPublisher
from app.prompts.loader import PromptLoader
from app.skills.import_classifier import ImportClassifierSkill
from app.storage.local import LocalFileStore
from app.tools.file_parser import FileParserTool
from app.tools.script_text import full_script_to_script_draft
from app.workflows.checkpoint import node_failure, raise_if_cancelled
from app.workflows.router import route_import

logger = logging.getLogger(__name__)


class ImportState(TypedDict, total=False):
    """导入分类工作流状态。"""

    run_id: str
    project_id: str
    action: str
    upload_id: str | None
    classification_artifact_id: str | None
    script_artifact_id: str | None
    content_type: str | None
    route: str | None
    needs_user_input: bool
    status: str
    error_node: str | None
    error_code: str | None
    error_detail: str | None
    completed_nodes: list[str]
    prompt_versions: dict[str, str]


def _get_node_context() -> dict[str, Any]:
    """从 LangGraph 运行时获取 configurable 上下文。"""
    return get_config()["configurable"]


async def import_file_node(state: ImportState) -> dict[str, Any]:
    """执行 parse → classify → 持久化 → route。"""
    ctx = _get_node_context()
    db = ctx["db"]
    agent: BaseAgent = ctx["agent"]
    prompt_loader: PromptLoader = ctx["prompt_loader"]
    artifact_svc: ArtifactService = ctx["artifact_service"]
    publisher: EventPublisher = ctx["event_publisher"]
    project_id = uuid.UUID(state["project_id"])
    run_id = uuid.UUID(state["run_id"])
    upload_id = ctx.get("upload_id") or state.get("upload_id")

    # 协作式取消守卫（I-01）
    raise_if_cancelled(state["run_id"])

    # 失败短路（I-01）：上游节点已失败则跳过本节点，保持失败状态不变
    if state.get("status") == "failed":
        return {}

    if "import_file" in state.get("completed_nodes", []):
        logger.info("节点已跳过（已完成）: import_file")
        return {}

    await publisher.publish(
        db, run_id=run_id, event_type="node.started",
        payload={"node": "import_file", "progress": 0.0},
        autocommit=True,
    )

    try:
        if not upload_id:
            raise ValueError("缺少 upload_id，无法执行导入分类")

        settings = load_settings()

        # 1. 读取上传记录（归属校验）
        repo = UploadRepository(db)
        upload = await repo.get_for_project(project_id, uuid.UUID(upload_id))
        if upload is None:
            raise FileNotFoundError(f"上传记录不存在: {upload_id}")

        # 2. 读文件 + 解析（G-03 Parser：编码探测 / DOCX 段落表格）
        store = LocalFileStore(root=settings.upload_file_root)
        data = await store.open(upload.path)
        parsed = await FileParserTool(
            upload_max_bytes=settings.upload_max_bytes
        ).execute(filename=upload.original_name, data=data)

        # 3. 分类（规则先行 + LLM 兜底）
        skill = ImportClassifierSkill()
        classification = await skill.execute({
            "input": ImportClassificationInput(
                filename=upload.original_name,
                text=parsed.text,
                upload_id=str(upload.id),
            ),
            "agent": agent,
            "prompt_loader": prompt_loader,
        })

        # 4. 持久化 import_classification Artifact（幂等：同 upload 去重）
        prompt_version = prompt_loader.get("import_classifier").version
        artifact = await artifact_svc.create_validated_artifact(
            db,
            project_id=project_id,
            artifact_type="import_classification",
            content=classification.model_dump(),
            prompt_version=prompt_version,
            dedup_extra=f"upload:{upload_id}",
        )

        # 5. 确定性路由
        route = route_import(classification.content_type)

        # 6. full_script → 持久化 script_draft（G-06：完整剧本能进入评估流程）。
        # 确定性转换 full_script_to_script_draft 构造最小合法 ScriptDraft；
        # 转换失败（结构不足）仅记录警告，不阻断分类（评估时会因无脚本跳过）。
        script_artifact_id: str | None = None
        if classification.content_type == "full_script":
            title = (upload.original_name or "").rsplit(".", 1)[0] or "导入剧本"
            script_content = full_script_to_script_draft(
                parsed.text, title=title, episode_number=1
            )
            if script_content is None:
                logger.warning(
                    "完整剧本无法构造合法 ScriptDraft，跳过入库: upload=%s", upload_id
                )
            else:
                script_artifact = await artifact_svc.create_validated_artifact(
                    db,
                    project_id=project_id,
                    artifact_type="script_draft",
                    episode_number=1,
                    content=script_content,
                    source_artifact_ids=[
                        {
                            "artifact_id": str(artifact.id),
                            "version": artifact.version,
                            "relation": "derived_from",
                        }
                    ],
                    dedup_extra=f"upload:{upload_id}",
                )
                script_artifact_id = str(script_artifact.id)

        logger.info(
            "导入分类完成: upload=%s type=%s route=%s artifact=%s script=%s",
            upload_id, classification.content_type, route, artifact.id,
            script_artifact_id,
        )

        await publisher.publish(
            db, run_id=run_id, event_type="node.completed",
            payload={
                "node": "import_file",
                "artifact_id": str(artifact.id),
                "artifact_type": "import_classification",
                "content_type": classification.content_type,
                "route": route,
                "script_artifact_id": script_artifact_id,
                "progress": 1.0,
            },
            autocommit=True,
        )

        return {
            "classification_artifact_id": str(artifact.id),
            "script_artifact_id": script_artifact_id,
            "content_type": classification.content_type,
            "route": route,
            "needs_user_input": route == "needs_user_input",
            "completed_nodes": state.get("completed_nodes", []) + ["import_file"],
            "prompt_versions": {
                **state.get("prompt_versions", {}),
                "import_classifier": prompt_version,
            },
        }

    except Exception as e:
        logger.exception("导入分类失败: run=%s", run_id)
        await publisher.publish(
            db, run_id=run_id, event_type="node.failed",
            payload={"node": "import_file", "error": str(e)},
            autocommit=True,
        )
        return node_failure("import_file", e)


def build_import_workflow(
    *, checkpointer: Any | None = None
) -> CompiledStateGraph[ImportState, Any, Any, Any]:
    """构建 ImportFileWorkflow 的 LangGraph 状态图。"""
    builder = StateGraph(ImportState)
    builder.add_node("import_file", import_file_node)
    builder.set_entry_point("import_file")
    builder.add_edge("import_file", END)
    return builder.compile(checkpointer=checkpointer)
