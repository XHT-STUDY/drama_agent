"""WorkflowEvent Pydantic Schema — SSE 事件格式定义。

事件流经 DB 持久化和 Redis 实时推送时使用统一格式。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WorkflowEventSchema(BaseModel):
    """SSE 事件的标准格式。

    与 ORM 模型分离，确保 API 输出稳定。
    """

    model_config = {"extra": "forbid"}

    event_id: str = Field(..., description="事件 UUID")
    run_id: str = Field(..., description="所属 Run UUID")
    sequence: int = Field(..., description="事件在 Run 内的递增序号")
    event_type: str = Field(
        ...,
        description="事件类型：run.created / node.started / node.completed / run.completed / run.failed 等",
    )
    stage: str = Field(default="", description="当前阶段标识")
    progress: float = Field(default=0.0, ge=0.0, le=1.0, description="进度 0~1")
    message: str = Field(default="", description="人类可读的状态消息")
    artifact_id: str | None = Field(default=None, description="关联 Artifact ID")
    payload: dict[str, Any] | None = Field(default=None, description="额外负载")
    timestamp: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z",
        description="事件时间戳（ISO 8601 UTC）",
    )

    @classmethod
    def from_orm(cls, event: Any) -> WorkflowEventSchema:
        """从 ORM WorkflowEvent 模型构造 Schema。"""
        payload = event.payload or {}
        return cls(
            event_id=str(event.id),
            run_id=str(event.run_id),
            sequence=event.sequence,
            event_type=event.type,
            stage=payload.get("stage", ""),
            progress=payload.get("progress", 0.0),
            message=payload.get("message", ""),
            artifact_id=payload.get("artifact_id"),
            payload=payload,
            timestamp=event.created_at.isoformat(),
        )

    def to_sse(self) -> str:
        """转为 SSE text/event-stream 格式。"""
        import json

        data = self.model_dump()
        return f"id: {self.event_id}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
