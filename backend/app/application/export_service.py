"""ExportService — 导出应用服务 (G-05)。

流程：校验项目 → 组装各 kind 的 latest valid Artifact（或显式指定版本）
→ 序列化（Markdown / DOCX）→ FileStore 原子落盘 → 持久化 ExportFile Artifact。

幂等键 = source_artifact_ids（本次导出所依据的源 Artifact 版本）
      + dedup_extra（ExportSelection 规范化 JSON）；
同一选择 + 同一批源重复导出时幂等复用已有 ExportFile，不产生新版本。

G-05 验收约束：
- 任一步失败抛异常，不生成 valid ExportFile（content 非法 → status="invalid"）；
- 文件名经清洗（不含路径分隔符 / 控制字符），客户端原始名永不入盘；
- 导出失败时清理孤儿文件，避免磁盘残留。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.artifact_service import ArtifactResponse, ArtifactService
from app.artifacts.store import ArtifactStore
from app.artifacts.versions import compute_checksum
from app.core.config import load_settings
from app.core.errors import AppError
from app.domain.enums import ArtifactType
from app.domain.export import ExportFileContent, ExportSelection
from app.observability.metrics import export_total
from app.storage.local import LocalFileStore
from app.storage.protocol import FileStore
from app.tools.exporters.docx import DocxExporter
from app.tools.exporters.markdown import (
    MarkdownExporter,
    build_export_filename,
    build_export_markdown,
    format_timestamp,
)

if TYPE_CHECKING:
    from app.application.project_service import ProjectService

# kind → ArtifactType（导出内容类型与 Artifact 类型的唯一映射）
_KIND_TO_TYPE: dict[str, str] = {
    "story_bible": ArtifactType.STORY_BIBLE,
    "outline": ArtifactType.EPISODE_OUTLINE_SET,
    "script": ArtifactType.SCRIPT_DRAFT,
    "evaluation": ArtifactType.EVALUATION_REPORT,
    "revision": ArtifactType.REVISION_PLAN,
}
# 需要按集汇总的 kind（对应 data 中的复数列表键）
_KIND_LIST_KEYS: dict[str, str] = {
    "script": "scripts",
    "evaluation": "evaluations",
    "revision": "revisions",
}


class ExportError(AppError):
    """导出过程中的业务错误（不生成 valid ExportFile）。"""

    status_code: int = 422
    code: str = "EXPORT_FAILED"


class ExportSelectionError(AppError):
    """导出选择在排队前即不合法（W1-05：请求接受时校验并拒绝）。"""

    status_code: int = 422
    code: str = "EXPORT_SELECTION_INVALID"


def _instrument_export(fn: Callable[..., Any]) -> Callable[..., Any]:
    """导出成败计数装饰器（I-02）：成功/失败分别累加 export_total{format,status}。"""

    @wraps(fn)
    async def _wrapper(*args: Any, **kwargs: Any) -> Any:
        selection = kwargs.get("selection")
        fmt = getattr(selection, "format", "unknown")
        try:
            result = await fn(*args, **kwargs)
        except Exception:
            export_total.inc(format=fmt, status="failed")
            raise
        export_total.inc(format=fmt, status="success")
        return result

    return _wrapper


class ExportService:
    """导出应用服务。

    file_store 可注入（测试用临时目录）；缺省使用 settings.export_file_root。
    """

    def __init__(self, *, file_store: FileStore | None = None) -> None:
        self._file_store = file_store
        self._artifact_svc = ArtifactService()
        self._artifact_store = ArtifactStore()
        self._markdown = MarkdownExporter()
        self._docx = DocxExporter()
        self._project_svc: ProjectService | None = None

    def _file_store_instance(self) -> FileStore:
        """懒初始化 FileStore（注入优先，否则读配置）。"""
        if self._file_store is None:
            settings = load_settings()
            self._file_store = LocalFileStore(root=settings.export_file_root)
        return self._file_store

    async def _get_project_title(self, db: AsyncSession, project_id: uuid.UUID) -> str:
        """读取项目标题；项目不存在抛 PROJECT_NOT_FOUND。"""
        if self._project_svc is None:
            from app.application.project_service import ProjectService

            self._project_svc = ProjectService()
        project = await self._project_svc.get(db, project_id)
        return project.title

    # ---- 组装 ----

    async def _fetch_explicit(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        artifact_ids: list[str],
        kind: str,
    ) -> list[ArtifactResponse]:
        """按显式 Artifact ID 取版本（G-05 验收「用户可显式选择版本」）。

        服务端校验 type / status / 项目归属，任一不合法即拒绝。
        """
        expected_type = _KIND_TO_TYPE[kind]
        items: list[ArtifactResponse] = []
        for aid in artifact_ids:
            artifact = await self._artifact_svc.get_version(db, uuid.UUID(aid))
            if (
                artifact.type != expected_type
                or artifact.status != "valid"
                or artifact.project_id != project_id
            ):
                raise ExportError(
                    detail=f"导出的 Artifact 不合法或不属于当前项目: {aid}",
                )
            items.append(artifact)
        return items

    async def _collect_latest(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        artifact_type: str,
    ) -> list[ArtifactResponse]:
        """收集指定类型每集 latest valid Artifact，按集号升序。"""
        artifacts = await self._artifact_store.list_by_project(
            db, project_id, artifact_type, offset=0, limit=1000
        )
        latest: dict[int, ArtifactResponse] = {}
        for a in artifacts:
            if a.status == "valid" and a.episode_number not in latest:
                latest[a.episode_number] = ArtifactResponse(a)
        return sorted(latest.values(), key=lambda a: a.episode_number)

    async def _assemble(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        selection: ExportSelection,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
        """组装各 kind 的内容 + 收集源 Artifact + 收集警告。

        Returns:
            (data, source_artifact_ids, warnings)
            data 结构适配 build_export_markdown。
        """
        overrides = selection.artifact_ids or {}
        data: dict[str, Any] = {
            "story_bible": None,
            "outline": None,
            "scripts": [],
            "evaluations": [],
            "revisions": [],
        }
        sources: list[dict[str, Any]] = []
        warnings: list[str] = []

        def _register(items: list[ArtifactResponse]) -> None:
            for item in items:
                sources.append(
                    {
                        "artifact_id": str(item.id),
                        "version": item.version,
                        "relation": "derived_from",
                    }
                )

        for kind in selection.kinds:
            artifact_type = _KIND_TO_TYPE[kind]
            explicit = overrides.get(kind)
            if kind in _KIND_LIST_KEYS:
                if explicit:
                    items = await self._fetch_explicit(db, project_id, explicit, kind)
                else:
                    items = await self._collect_latest(db, project_id, artifact_type)
                data[_KIND_LIST_KEYS[kind]] = [i.content for i in items]
                if not items:
                    warnings.append(f"{kind} 无可用有效内容")
                _register(items)
            else:
                if explicit:
                    items = await self._fetch_explicit(db, project_id, explicit, kind)
                    item = items[0] if items else None
                else:
                    item_orm = await self._artifact_store.get_latest(
                        db, project_id, artifact_type, 1
                    )
                    item = ArtifactResponse(item_orm) if item_orm is not None else None
                data[kind] = item.content if item is not None else None
                if item is None:
                    warnings.append(f"{kind} 无可用有效内容")
                if item is not None:
                    _register([item])

        return data, sources, warnings

    # ---- 选择冻结（W1-05） ----

    async def resolve_selection(
        self,
        db: AsyncSession,
        *,
        project_id: uuid.UUID,
        kinds: list[str],
        artifact_ids: dict[str, list[str]] | None,
    ) -> tuple[dict[str, list[str]], list[str]]:
        """在创建 Run 之前把导出选择规范化为逐 kind 的显式 Artifact ID。

        作者导出哪一稿就固定哪一稿（W1-05）：排队后产生的新版本、暂停
        期间的聊天改稿都不能改变本次导出内容。

        - 显式模式（artifact_ids 提供）：每个 kind 必须有非空 ID 列表
          （缺项/空数组 422）；逐 ID 校验 type/status/项目归属；剧本类
          kind 同一集选多个版本 422；评估报告绑定剧本与所选剧本版本
          不一致时默认剔除并告警（不把错配报告混进交付）。
        - 缺省模式（旧客户端）：接受时解析各 kind 的 latest valid 并
          冻结——不能等 Worker 运行时重新选稿；无内容的 kind 冻结为
          空列表（Worker 导出时如实提示该 kind 无内容）。

        Returns:
            (resolved, warnings)：resolved = kind → 显式 Artifact ID 列表
        """
        resolved: dict[str, list[str]] = {}
        warnings: list[str] = []

        explicit_mode = artifact_ids is not None
        if explicit_mode and set(artifact_ids or {}) - set(kinds):
            unknown = sorted(set(artifact_ids or {}) - set(kinds))
            raise ExportSelectionError(
                detail=f"artifact_ids 含未选择的内容类型: {unknown}",
            )

        # 先解析剧本（评估绑定校验需要所选剧本集合）
        ordered_kinds = [k for k in ("script",) if k in kinds] + [
            k for k in kinds if k != "script"
        ]
        script_artifacts: dict[str, ArtifactResponse] = {}
        for kind in ordered_kinds:
            ids: list[str]
            if explicit_mode:
                ids = list((artifact_ids or {}).get(kind) or [])
                if not ids:
                    raise ExportSelectionError(
                        detail=(
                            f"显式指定导出内容时，{kind} 的 Artifact ID 列表"
                            "不能为空或缺失"
                        ),
                    )
            else:
                if kind in _KIND_LIST_KEYS:
                    items = await self._collect_latest(
                        db, project_id, _KIND_TO_TYPE[kind]
                    )
                else:
                    item_orm = await self._artifact_store.get_latest(
                        db, project_id, _KIND_TO_TYPE[kind], 1
                    )
                    items = [ArtifactResponse(item_orm)] if item_orm else []
                ids = [str(i.id) for i in items]
                # 无内容的 kind 冻结为空列表：Worker 导出时该 kind 会
                # 记录"无可用有效内容"警告（权威告警在导出文件里，避免
                # 接受时与导出时重复）

            try:
                artifacts = await self._fetch_explicit(db, project_id, ids, kind)
            except ExportError as exc:
                # 选择不合法（类型/状态/归属）在排队前以选择错误拒绝
                raise ExportSelectionError(detail=str(exc.detail) if exc.detail else str(exc)) from exc
            if kind == "script":
                episodes: dict[int, str] = {}
                for a in artifacts:
                    if a.episode_number in episodes:
                        raise ExportSelectionError(
                            detail=(
                                f"第 {a.episode_number} 集选择了多个剧本版本"
                                f"（{episodes[a.episode_number]} / {a.id}），"
                                "一次导出每集只能有一份正文"
                            ),
                        )
                    episodes[a.episode_number] = str(a.id)
                    script_artifacts[str(a.id)] = a

            if kind == "evaluation" and "script" in kinds:
                kept: list[str] = []
                for a in artifacts:
                    bound = str(
                        (a.content or {}).get("script_artifact_id") or ""
                    )
                    if bound and script_artifacts and bound not in script_artifacts:
                        warnings.append(
                            f"评估报告 {a.id} 绑定的剧本版本不在本次导出的剧本"
                            "集合中，已默认剔除（可在版本页核对后重新导出）"
                        )
                        continue
                    kept.append(str(a.id))
                artifacts = [
                    a for a in artifacts if str(a.id) in kept
                ]
                ids = kept

            resolved[kind] = [str(a.id) for a in artifacts]

        return resolved, warnings

    # ---- 主入口 ----

    @_instrument_export
    async def export_project(
        self,
        db: AsyncSession,
        *,
        project_id: uuid.UUID,
        selection: ExportSelection,
        now: datetime | None = None,
    ) -> ArtifactResponse:
        """导出项目内容并持久化为 ExportFile Artifact。

        Args:
            db: 数据库会话
            project_id: 目标项目
            selection: 导出选择（kinds / format / 显式版本）
            now: 导出时间（测试注入保证确定性）；缺省用当前 UTC 时间

        Returns:
            ExportFile Artifact 响应；任一步失败抛异常不返回。

        Raises:
            ExportError: 组装 / 序列化失败，不生成 valid ExportFile
        """
        # 1. 项目存在校验 + 项目标题（文件名 / 文档抬头用）
        project_title = await self._get_project_title(db, project_id)

        # 2. 组装内容 + 源 Artifact
        data, sources, warnings = await self._assemble(db, project_id, selection)

        # 3. 序列化
        now = now or datetime.now(UTC)
        exported_at = now.isoformat()
        try:
            markdown = build_export_markdown(
                project_title=project_title,
                exported_at=exported_at,
                data=data,
                kinds=selection.kinds,
            )
            if selection.format == "markdown":
                content_bytes = markdown.encode("utf-8")
                suffix = ".md"
            else:
                result = await self._docx.execute(
                    markdown=markdown, header_text=project_title
                )
                content_bytes = result["data"]
                suffix = ".docx"
        except Exception as exc:  # noqa: BLE001 — 序列化失败统一包装
            raise ExportError(detail=f"导出序列化失败: {exc}") from exc

        # 4. FileStore 原子落盘（先临时文件再 os.replace，见 LocalFileStore.save）
        store = self._file_store_instance()
        storage_key = await store.save(content_bytes, suffix=suffix)

        # 5. 构建 ExportFile content 并持久化
        filename = build_export_filename(
            project_title, selection.kinds, selection.format, format_timestamp(now)
        )
        content = ExportFileContent(
            storage_key=storage_key,
            format=selection.format,
            filename=filename,
            size_bytes=len(content_bytes),
            sha256=hashlib.sha256(content_bytes).hexdigest(),
            source_artifact_ids=sources,
            warnings=warnings,
        ).model_dump()

        artifact = await self._artifact_svc.create_validated_artifact(
            db,
            project_id=project_id,
            artifact_type=ArtifactType.EXPORT_FILE,
            content=content,
            source_artifact_ids=sources,
            dedup_extra=self._selection_key(selection),
        )

        # 幂等命中（同选择 + 同源已导出过）：清理本次写出的孤儿文件
        if artifact.checksum != compute_checksum(content):
            with contextlib.suppress(Exception):  # noqa: BLE001 — 清理失败不影响返回
                await store.delete(storage_key)

        return artifact

    @staticmethod
    def _selection_key(selection: ExportSelection) -> str:
        """ExportSelection 规范化 JSON（作为 dedup_extra 的确定性幂等因子）。"""
        return json.dumps(
            selection.model_dump(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
