"""Dead-letter persistence and safe replay without retaining duplicate message bodies."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.metrics import DEAD_LETTERS
from app.models import Conversation, DeadLetter, Message, OutboxEvent
from app.schemas.event import EventEnvelope, EventType


async def record(session: AsyncSession, *, original: dict, error: Exception) -> DeadLetter | None:
    payload = original.get("payload") or {}
    try:
        tenant_id = uuid.UUID(original.get("tenant_id") or payload.get("tenant_id"))
    except (ValueError, TypeError, AttributeError):
        return None
    row = DeadLetter(
        tenant_id=tenant_id,
        trace_id=str(original.get("trace_id") or ""),
        original_event_id=original.get("event_id"),
        original_subject=EventType.IM_INBOUND,
        error_type=type(error).__name__,
        error=str(error)[:500],
        metadata_json={
            "message_id": payload.get("message_id"),
            "conversation_id": payload.get("conversation_id"),
            "user_id": payload.get("user_id"),
        },
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    DEAD_LETTERS.labels(str(row.original_subject)).inc()
    return row


async def list_for_tenant(session: AsyncSession, tenant_id: uuid.UUID, limit: int) -> list[DeadLetter]:
    return list((await session.scalars(
        select(DeadLetter).where(DeadLetter.tenant_id == tenant_id)
        .order_by(DeadLetter.created_at.desc()).limit(limit)
    )).all())


async def replay(session: AsyncSession, tenant_id: uuid.UUID, dead_letter_id: uuid.UUID) -> DeadLetter:
    row = await session.scalar(select(DeadLetter).where(
        DeadLetter.id == dead_letter_id, DeadLetter.tenant_id == tenant_id,
    ))
    if row is None:
        raise NotFoundError("死信不存在")
    if row.status == "replayed":
        return row
    message_id = row.metadata_json.get("message_id")
    message = await session.scalar(select(Message).where(
        Message.tenant_id == tenant_id, Message.message_id == message_id, Message.role == "user",
    ))
    if message is None:
        raise NotFoundError("原始消息不存在，无法重放")
    conversation = await session.scalar(select(Conversation).where(
        Conversation.id == message.conversation_id, Conversation.tenant_id == tenant_id,
    ))
    if conversation is None:
        raise NotFoundError("原始会话不存在，无法重放")
    envelope = EventEnvelope(
        trace_id=row.trace_id or str(uuid.uuid4()), tenant_id=str(tenant_id), type=EventType.IM_INBOUND,
        payload={"message_id": message.message_id, "tenant_id": str(tenant_id),
                 "user_id": str(conversation.user_id), "conversation_id": str(message.conversation_id),
                 "content": message.content},
    )
    message.status = "acknowledged"
    message.processing_lease_until = None
    session.add(OutboxEvent(event_id=envelope.event_id, trace_id=envelope.trace_id,
                            tenant_id=tenant_id, subject=EventType.IM_INBOUND,
                            payload=envelope.model_dump(mode="json")))
    row.status = "replayed"
    row.replayed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(row)
    return row
