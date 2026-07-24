"""DramaAgent 事件系统。

提供：
- WorkflowEvent Pydantic Schema
- EventPublisher（DB 持久化 + Redis 实时通知）
- SSE 流端点（heartbeat + Last-Event-ID 补发）

Redis 仅做实时通知——事件持久化在 PostgreSQL，
Redis 故障不影响数据完整性（DEV_PLAN §6.3）。
"""

from app.events.publisher import EventPublisher
from app.events.schemas import WorkflowEventSchema

__all__ = [
    "EventPublisher",
    "WorkflowEventSchema",
]
