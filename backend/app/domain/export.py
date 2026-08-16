"""导出领域模型 — ExportSelection 与 ExportFileContent (G-05)。

ExportFileContent 是 export_file Artifact 的 content schema，
经 artifact_service._SCHEMA_MAP 注册后，写入前会经过 Pydantic v2 校验——
"任一步失败不生成 valid ExportFile" 依赖该校验闭环
（content 非法时 status="invalid"，get_latest 只返回 valid）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.enums import ExportFormat

# 导出内容类型（与前端 ExportContentKind 一一对应）
ExportContentKind = Literal["story_bible", "outline", "script", "evaluation", "revision"]

# 内容类型 → 中文短标签（用于文件名与文档标题，镜像前端 EXPORT_KIND_LABELS）
EXPORT_KIND_LABELS: dict[str, str] = {
    "story_bible": "StoryBible",
    "outline": "大纲",
    "script": "剧本",
    "evaluation": "评估",
    "revision": "修订说明",
}


class ExportSelection(BaseModel):
    """一次导出请求的选择描述。

    作为幂等键（dedup_extra）的组成部分：同一选择 + 同一批源 Artifact
    重复导出会幂等复用已有 ExportFile，不产生新版本。

    artifact_ids 支持"用户显式选择版本"（G-05 验收）：
    缺省为各 kind 取 latest valid；显式给出 kind → Artifact ID 列表时
    只导出这些指定版本（服务端会校验类型 / status / 项目归属）。
    """

    model_config = {"extra": "forbid"}

    kinds: list[ExportContentKind] = Field(
        ..., description="要导出的内容类型列表"
    )
    format: ExportFormat = Field(..., description="导出格式: markdown / docx")
    artifact_ids: dict[str, list[str]] | None = Field(
        default=None,
        description="显式指定要导出的 Artifact ID（kind → ID 列表）；None 表示取 latest valid",
    )


class ExportFileContent(BaseModel):
    """export_file Artifact 的 content schema。

    storage_key 是服务端 FileStore 存储键（UUID 文件名，客户端原始名永不入盘）；
    source_artifact_ids 记录本次导出所依据的各 Artifact 版本
    （与 Artifact.source_artifact_ids 冗余，便于内容自含审计）。
    """

    model_config = {"extra": "forbid"}

    storage_key: str = Field(..., description="FileStore 存储键", min_length=1)
    format: ExportFormat = Field(..., description="导出格式: markdown / docx")
    filename: str = Field(
        ..., description="下载文件名（已做安全清洗，不含路径分隔符）", min_length=1
    )
    size_bytes: int = Field(..., description="文件字节数", ge=0)
    sha256: str = Field(..., description="文件内容 SHA256 校验和", min_length=64)
    source_artifact_ids: list[dict[str, Any]] = Field(
        default_factory=list, description="导出所依据的源 Artifact 列表"
    )
    warnings: list[str] = Field(
        default_factory=list, description="导出过程中的警告（如某 kind 无可用内容）"
    )
