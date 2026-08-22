"""ImportFileWorkflow 测试 (G-04).

测试范围:
- 规则先行命中（reference / idea_or_notes / unknown-过短 / full_script-强结构）
  → 不调 LLM，路由正确，classification Artifact 持久化
- LLM 兜底（outline 模糊文本 / unknown 长文本）→ 注册 fixture 驱动
- reference → route=hold 仅归档，不自动入库创作管线
- unknown → needs_user_input=True（交给用户确认）
- 幂等：同 upload 重复运行不产生新版本

全部使用 FakeLLM；上传文件经 LocalFileStore 落盘，节点经 FileParserTool 重新解析。
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.upload import Upload
from app.domain.import_file import ImportClassification
from app.llm.fake import FakeLLM
from app.storage.local import LocalFileStore
from app.workflows.import_file import ImportState, build_import_workflow

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"

# ---- 测试用文本 ----

_REFERENCE_TEXT = (
    "参考资料：短剧《逆袭人生》设定集。\n"
    "人物关系参考文档见 http://example.com/chars。"
)
_IDEA_TEXT = "一个被青训队抛弃的足球少年，想写一个逆袭热血短剧。"
_UNKNOWN_SHORT_TEXT = "你好"
_SCRIPT_TEXT = (
    "第1场 训练场（日）\n"
    "教练：你被开除了。\n"
    "林峰：为什么？\n"
    "教练：因为你不够强。\n"
    "\n"
    "第2场 宿舍（夜）\n"
    "林峰：我绝不放弃。\n"
    "室友：可你已经没有机会了。\n"
    "林峰：那就证明给他们看。\n"
)
# 模糊大纲：有"第X集"但场景/对白不足，规则不命中 → LLM 兜底
_OUTLINE_AMBIGUOUS_TEXT = (
    "大纲：第1集主角被青训队抛弃，第2集低谷期遇到伯乐教练，第3集组建草根球队开始逆袭。"
)
# 长文本无结构 → 规则不命中 → LLM 兜底（返回 unknown）
_LONG_RAMBLE_TEXT = "今天看了很多短剧，想起之前那个足球少年的点子。" * 10


def _load_golden(name: str) -> dict[str, Any]:
    import json
    from typing import cast
    data = json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))
    if isinstance(data, dict) and "expected_output" in data:
        return cast(dict[str, Any], data["expected_output"])
    return cast(dict[str, Any], data)


async def _seed_upload(
    db: Any,
    project_id: uuid.UUID,
    *,
    filename: str,
    text: str,
) -> uuid.UUID:
    """把文本写入 FileStore 并创建 Upload 行，返回 upload_id。"""
    from app.core.config import load_settings
    settings = load_settings()
    store = LocalFileStore(root=settings.upload_file_root)
    data = text.encode("utf-8")
    key = await store.save(data, suffix=".txt")

    upload = Upload(
        project_id=project_id,
        path=key,
        sha256=hashlib.sha256(data).hexdigest(),
        mime_type="text/plain",
        size_bytes=len(data),
        original_name=filename,
        parse_status="parsed",
        char_count=len(text),
        warnings=[],
    )
    db.add(upload)
    await db.flush()
    return upload.id


def _import_state(
    run_id: str,
    project_id: uuid.UUID,
    upload_id: uuid.UUID,
) -> ImportState:
    """构造 ImportFileWorkflow 初始状态。"""
    return {
        "run_id": run_id,
        "project_id": str(project_id),
        "action": "import",
        "upload_id": str(upload_id),
        "classification_artifact_id": None,
        "content_type": None,
        "route": None,
        "needs_user_input": False,
        "status": "running",
        "error_node": None,
        "error_detail": None,
        "completed_nodes": [],
        "prompt_versions": {},
    }


async def _start_import_run(
    workflow_config: dict[str, Any],
    project_id: uuid.UUID,
) -> Any:
    """创建 action=import 的 Run 并置为 running。"""
    db = workflow_config["configurable"]["db"]
    run_svc = workflow_config["configurable"]["run_service"]
    run = await run_svc.create_run(
        db=db, project_id=project_id, action="import"
    )
    await run_svc.transition_status(db, run.id, "running")
    return run


async def _run_import(
    workflow_config: dict[str, Any],
    run_id: str,
    project_id: uuid.UUID,
    upload_id: uuid.UUID,
) -> Any:
    """运行 ImportFileWorkflow，返回最终 State。

    config 强转为 Any：与 LangGraph ainvoke 的 RunnableConfig 参数适配
    （与既有 workflow 测试相同模式）。
    """
    workflow = build_import_workflow()
    state = _import_state(run_id, project_id, upload_id)
    return await workflow.ainvoke(state, cast(Any, workflow_config))


@pytest.mark.workflow
@pytest.mark.asyncio
class TestImportWorkflow:
    """导入分类工作流。"""

    async def test_rules_hit_reference_no_llm(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        fake_llm: FakeLLM,
    ) -> None:
        """规则命中 reference：不调 LLM，route=hold，Artifact 持久化。"""
        db: AsyncSession = workflow_config["configurable"]["db"]
        upload_id = await _seed_upload(db, test_project, filename="参考素材.txt", text=_REFERENCE_TEXT)
        run = await _start_import_run(workflow_config, test_project)

        final_state = await _run_import(workflow_config, str(run.id), test_project, upload_id)

        assert final_state["route"] == "hold"
        assert final_state["content_type"] == "reference"
        assert final_state["needs_user_input"] is False
        assert fake_llm.get_call_history() == [], "规则命中不应调用 LLM"

        artifact = await _get_classification_artifact(workflow_config, final_state)
        assert artifact.content["content_type"] == "reference"
        assert artifact.content["detected_features"]["has_reference_keywords"] is True

    async def test_rules_hit_idea_or_notes(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        fake_llm: FakeLLM,
    ) -> None:
        """规则命中 idea_or_notes：route=create。"""
        db = workflow_config["configurable"]["db"]
        upload_id = await _seed_upload(db, test_project, filename="灵感.txt", text=_IDEA_TEXT)
        run = await _start_import_run(workflow_config, test_project)

        final_state = await _run_import(workflow_config, str(run.id), test_project, upload_id)

        assert final_state["route"] == "create"
        assert final_state["content_type"] == "idea_or_notes"
        assert fake_llm.get_call_history() == []

    async def test_rules_hit_short_unknown(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
    ) -> None:
        """文本过短（<20 字符）→ unknown → needs_user_input。"""
        db = workflow_config["configurable"]["db"]
        upload_id = await _seed_upload(db, test_project, filename="x.txt", text=_UNKNOWN_SHORT_TEXT)
        run = await _start_import_run(workflow_config, test_project)

        final_state = await _run_import(workflow_config, str(run.id), test_project, upload_id)

        assert final_state["route"] == "needs_user_input"
        assert final_state["needs_user_input"] is True
        assert final_state["content_type"] == "unknown"

    async def test_rules_hit_full_script(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        fake_llm: FakeLLM,
    ) -> None:
        """强剧本结构（场景≥2 + 对白≥5）→ full_script → route=evaluate。"""
        db = workflow_config["configurable"]["db"]
        upload_id = await _seed_upload(db, test_project, filename="第一集剧本.txt", text=_SCRIPT_TEXT)
        run = await _start_import_run(workflow_config, test_project)

        final_state = await _run_import(workflow_config, str(run.id), test_project, upload_id)

        assert final_state["route"] == "evaluate"
        assert final_state["content_type"] == "full_script"
        assert fake_llm.get_call_history() == []

    async def test_llm_fallback_outline(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        fake_llm: FakeLLM,
    ) -> None:
        """规则未命中 → LLM 兜底（outline）→ route=create，Artifact 含补全特征。"""
        db = workflow_config["configurable"]["db"]
        fake_llm.register(
            "import_classifier",
            ImportClassification.model_validate(
                _load_golden("import_classification_outline")
            ),
        )
        upload_id = await _seed_upload(
            db, test_project, filename="大纲草稿.txt", text=_OUTLINE_AMBIGUOUS_TEXT
        )
        run = await _start_import_run(workflow_config, test_project)

        final_state = await _run_import(workflow_config, str(run.id), test_project, upload_id)

        assert final_state["route"] == "create"
        assert final_state["content_type"] == "outline"
        # LLM 兜底路径确实发起了 import_classifier 调用
        assert len(fake_llm.get_call_history()) == 1

        artifact = await _get_classification_artifact(workflow_config, final_state)
        # 客观特征由系统补全覆盖 golden
        assert artifact.content["detected_features"]["episode_markers"] == 3

    async def test_llm_fallback_unknown_needs_user_input(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
        fake_llm: FakeLLM,
    ) -> None:
        """规则未命中 → LLM 兜底（unknown）→ needs_user_input=True。"""
        db = workflow_config["configurable"]["db"]
        fake_llm.register(
            "import_classifier",
            ImportClassification.model_validate(
                _load_golden("import_classification_unknown")
            ),
        )
        upload_id = await _seed_upload(
            db, test_project, filename="随笔.txt", text=_LONG_RAMBLE_TEXT
        )
        run = await _start_import_run(workflow_config, test_project)

        final_state = await _run_import(workflow_config, str(run.id), test_project, upload_id)

        assert final_state["route"] == "needs_user_input"
        assert final_state["needs_user_input"] is True
        assert final_state["content_type"] == "unknown"

    async def test_idempotent_same_upload_no_duplicate_artifact(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
    ) -> None:
        """同 upload 重复运行：Artifact 幂等去重，不产生新版本。"""
        db = workflow_config["configurable"]["db"]
        upload_id = await _seed_upload(db, test_project, filename="参考素材.txt", text=_REFERENCE_TEXT)
        run1 = await _start_import_run(workflow_config, test_project)

        final1 = await _run_import(workflow_config, str(run1.id), test_project, upload_id)
        await workflow_config["configurable"]["run_service"].transition_status(
            db, run1.id, "completed"
        )
        run2 = await _start_import_run(workflow_config, test_project)
        final2 = await _run_import(workflow_config, str(run2.id), test_project, upload_id)

        assert final1["classification_artifact_id"] == final2["classification_artifact_id"]

        artifact = await _get_classification_artifact(workflow_config, final1)
        assert artifact.version == 1, "同 upload 只应有一个分类版本"

    async def test_upload_not_found_fails_run(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
    ) -> None:
        """upload_id 不存在（不属于项目）→ 节点失败并标记 status=failed。"""
        run = await _start_import_run(workflow_config, test_project)
        missing_upload_id = uuid.uuid4()

        final_state = await _run_import(workflow_config, str(run.id), test_project, missing_upload_id)

        assert final_state["status"] == "failed"
        assert final_state["error_node"] == "import_file"
        assert "上传记录不存在" in final_state["error_detail"]

    async def test_cross_project_upload_rejected(
        self,
        test_project: uuid.UUID,
        workflow_config: dict[str, Any],
    ) -> None:
        """归属校验：别的项目的 upload 对当前项目不可见 → 失败。"""
        from app.db.models.project import Project

        db = workflow_config["configurable"]["db"]
        # 另建一个项目，upload 挂到它的名下
        other_project = uuid.uuid4()
        db.add(Project(id=other_project, title="别家项目", status="draft"))
        await db.flush()
        upload_id = await _seed_upload(db, other_project, filename="别家.txt", text=_IDEA_TEXT)
        run = await _start_import_run(workflow_config, test_project)

        final_state = await _run_import(workflow_config, str(run.id), test_project, upload_id)

        assert final_state["status"] == "failed"
        assert "上传记录不存在" in final_state["error_detail"]


async def _get_classification_artifact(
    workflow_config: dict[str, Any],
    final_state: dict[str, Any],
) -> Any:
    """按 Artifact ID 读取分类结果。"""
    db = workflow_config["configurable"]["db"]
    artifact_svc = workflow_config["configurable"]["artifact_service"]
    return await artifact_svc.get_version(
        db, uuid.UUID(final_state["classification_artifact_id"])
    )
