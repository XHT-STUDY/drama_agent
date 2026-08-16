"""导入内容分类领域模型 (G-04)。

文件名用 import_file 而非 import（Python 关键字）。

ImportClassificationInput — 分类器输入（原始文件信息 + 解析文本）。
ImportClassification   — 分类结果（五类 ContentType + 置信度 + 理由 + 特征）。

该 Schema 同时服务于:
- ImportClassifierSkill 的 LLM 输出校验（manifest output_schema）；
- import_classification Artifact 的 content schema（_SCHEMA_MAP 注册）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import ContentType


class ImportClassificationInput(BaseModel):
    """导入内容分类输入。"""

    model_config = {"extra": "forbid"}

    filename: str = Field(..., description="原始文件名（仅用于辅助判断扩展名）")
    text: str = Field(..., description="解析后的纯文本（TXT/DOCX 已由 G-03 Parser 提取）")
    upload_id: str | None = Field(default=None, description="关联的上传记录 ID")


class ImportClassification(BaseModel):
    """导入内容分类结果。

    五类 ContentType（idea_or_notes / outline / full_script / reference / unknown），
    带置信度与依据，供 workflowers/router.py 决定路由。
    """

    model_config = {"extra": "forbid"}

    content_type: ContentType = Field(..., description="内容类别")
    confidence: float = Field(..., ge=0.0, le=1.0, description="分类置信度（0~1）")
    reason: str = Field(..., description="分类依据（中文一句话）")
    detected_features: dict[str, Any] = Field(
        default_factory=dict, description="观察到的客观特征（字符数/场景标记/对白行数等）"
    )
