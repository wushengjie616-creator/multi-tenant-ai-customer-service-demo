"""消息结构（FR1-02 入站最小字段）。"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InboundMessage(BaseModel):
    """统一入站消息最小字段（architecture §3）。"""

    message_id: str
    tenant_id: str
    user_id: str
    conversation_id: str
    content: str
    timestamp: datetime = Field(default_factory=_utcnow)
