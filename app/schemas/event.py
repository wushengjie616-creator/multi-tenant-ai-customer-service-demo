"""统一事件封装与事件类型常量（§8.2 / §8.3）。"""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventEnvelope(BaseModel):
    """所有跨服务事件的最小封装。

    至少包含 event_id / trace_id / tenant_id / occurred_at / schema_version（§8.3）。
    """

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    trace_id: str
    tenant_id: str
    occurred_at: datetime = Field(default_factory=_utcnow)
    schema_version: str = SCHEMA_VERSION
    type: str
    traceparent: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventType:
    """事件类型常量。"""

    # 队列主题（§8.3）
    IM_INBOUND = "im.inbound"
    ASSISTANT_PROCESS = "assistant.process"
    IM_OUTBOUND = "im.outbound"
    REMINDER_DUE = "reminder.due"
    TOOL_RETRY = "tool.retry"
    DEADLETTER = "deadletter.*"

    # WebSocket 客户端事件（§8.2）
    MESSAGE_SEND = "message.send"
    MESSAGE_ACK = "message.ack"
    CONNECTION_RESUME = "connection.resume"
    CONNECTION_RESUMED = "connection.resumed"

    # WebSocket 服务端事件（§8.2）
    MESSAGE_ACCEPTED = "message.accepted"
    REPLY_START = "reply.start"
    REPLY_CHUNK = "reply.chunk"
    REPLY_END = "reply.end"
    REPLY_ERROR = "reply.error"
    REMINDER_TRIGGERED = "reminder.triggered"
    HANDOFF_STATUS = "handoff.status"
