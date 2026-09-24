import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_trace_id
from app.models import Conversation, Handoff, Message, OutboxEvent
from app.schemas.event import EventEnvelope, EventType
from app.utils.masking import mask_value


def should_handoff(*, explicit_request: bool, dissatisfaction_count: int) -> bool:
    return explicit_request or dissatisfaction_count >= 2


_DISSATISFACTION_PHRASES = (
    "不满意", "没用", "还是不对", "答非所问", "没有解决", "说了等于没说",
)
_SATISFACTION_PHRASES = ("解决了", "明白了", "谢谢", "可以了")


def is_dissatisfaction(text: str) -> bool:
    normalized = "".join(text.lower().split())
    return any(phrase in normalized for phrase in _DISSATISFACTION_PHRASES)


async def record_feedback(session: AsyncSession, conversation_id, text: str) -> int:
    if is_dissatisfaction(text):
        count = await session.scalar(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(dissatisfaction_count=Conversation.dissatisfaction_count + 1)
            .returning(Conversation.dissatisfaction_count)
        )
        return int(count or 0)
    if any(phrase in text for phrase in _SATISFACTION_PHRASES):
        await session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(dissatisfaction_count=0)
        )
    return 0


def serialize(row: Handoff) -> dict:
    return {
        "id": str(row.id), "tenant_id": str(row.tenant_id), "user_id": str(row.user_id),
        "conversation_id": str(row.conversation_id), "status": row.status,
        "summary": row.summary, "reason": row.reason, "context": row.context,
        "created_at": row.created_at, "accepted_at": row.accepted_at,
        "close_requested_at": row.close_requested_at,
        "close_deadline": row.close_deadline, "ended_at": row.ended_at,
    }


async def create_handoff(session: AsyncSession, *, tenant_id, user_id, conversation_id, summary: str, reason: str, context: dict) -> Handoff:
    existing = await session.scalar(select(Handoff).where(Handoff.tenant_id == tenant_id, Handoff.conversation_id == conversation_id, Handoff.active_key == "active"))
    if existing is not None:
        return existing
    handoff = Handoff(tenant_id=tenant_id, user_id=user_id, conversation_id=conversation_id, status="pending", summary=mask_value(summary), reason=reason, context=mask_value(context))
    session.add(handoff); await session.flush()
    conversation = await session.get(Conversation, conversation_id)
    if conversation is not None:
        conversation.dissatisfaction_count = 0
    envelope = EventEnvelope(trace_id=get_trace_id() or f"handoff-{handoff.id}", tenant_id=str(tenant_id), type=EventType.IM_OUTBOUND, payload={"message_id": f"handoff-{handoff.id}", "tenant_id": str(tenant_id), "user_id": str(user_id), "conversation_id": str(conversation_id), "content": "已为你发起人工转接。", "handoff_id": str(handoff.id), "handoff_status": handoff.status, "summary": handoff.summary, "context": handoff.context})
    session.add(OutboxEvent(event_id=envelope.event_id, trace_id=envelope.trace_id, tenant_id=tenant_id, subject=EventType.IM_OUTBOUND, payload=envelope.model_dump(mode="json")))
    await session.commit(); await session.refresh(handoff)
    return handoff


async def get_active(session: AsyncSession, tenant_id, conversation_id) -> Handoff | None:
    await close_expired(session, tenant_id=tenant_id, conversation_id=conversation_id)
    return await session.scalar(select(Handoff).where(
        Handoff.tenant_id == tenant_id,
        Handoff.conversation_id == conversation_id,
        Handoff.active_key == "active",
        or_(Handoff.reason != "offline_message", Handoff.status != "pending"),
    ))


async def list_for_tenant(session: AsyncSession, tenant_id) -> list[Handoff]:
    await close_expired(session, tenant_id=tenant_id)
    rows = await session.scalars(
        select(Handoff).where(Handoff.tenant_id == tenant_id).order_by(Handoff.created_at.desc())
    )
    return list(rows)


async def reply(session: AsyncSession, *, tenant_id, handoff_id, content: str) -> Handoff | None:
    handoff = await session.scalar(select(Handoff).where(
        Handoff.id == handoff_id, Handoff.tenant_id == tenant_id
    ))
    if handoff is None:
        return None
    message_id = f"agent-{uuid.uuid4()}"
    trace_id = get_trace_id() or message_id
    session.add(Message(
        message_id=message_id, tenant_id=tenant_id,
        conversation_id=handoff.conversation_id, role="assistant",
        content=content, status="completed", trace_id=trace_id,
    ))
    envelope = EventEnvelope(
        trace_id=trace_id, tenant_id=str(tenant_id), type=EventType.IM_OUTBOUND,
        payload={"message_id": message_id, "tenant_id": str(tenant_id),
                 "user_id": str(handoff.user_id), "conversation_id": str(handoff.conversation_id),
                 "content": content, "source": "human_agent", "handoff_id": str(handoff.id)},
    )
    session.add(OutboxEvent(event_id=envelope.event_id, trace_id=trace_id,
                            tenant_id=tenant_id, subject=EventType.IM_OUTBOUND,
                            payload=envelope.model_dump(mode="json")))
    if handoff.active_key is None:
        return None
    handoff.status = "in_progress"
    handoff.accepted_at = handoff.accepted_at or datetime.now(timezone.utc)
    handoff.close_requested_at = None
    handoff.close_deadline = None
    await session.commit()
    await session.refresh(handoff)
    return handoff


async def accept(session: AsyncSession, *, tenant_id, handoff_id) -> Handoff | None:
    handoff = await session.scalar(select(Handoff).where(
        Handoff.id == handoff_id, Handoff.tenant_id == tenant_id, Handoff.active_key == "active"
    ))
    if handoff is None:
        return None
    handoff.status = "in_progress"
    handoff.accepted_at = handoff.accepted_at or datetime.now(timezone.utc)
    handoff.close_requested_at = None
    handoff.close_deadline = None
    conversation = await session.get(Conversation, handoff.conversation_id)
    if conversation is not None:
        conversation.dissatisfaction_count = 0
    await session.commit(); await session.refresh(handoff)
    return handoff


async def request_close(session: AsyncSession, *, tenant_id, handoff_id) -> Handoff | None:
    handoff = await session.scalar(select(Handoff).where(
        Handoff.id == handoff_id, Handoff.tenant_id == tenant_id, Handoff.active_key == "active"
    ))
    if handoff is None:
        return None
    now = datetime.now(timezone.utc)
    handoff.status = "awaiting_confirmation"
    handoff.close_requested_at = now
    handoff.close_deadline = now + timedelta(minutes=10)
    await session.commit(); await session.refresh(handoff)
    return handoff


async def respond_to_close(session: AsyncSession, *, tenant_id, user_id, handoff_id, confirm: bool) -> Handoff | None:
    handoff = await session.scalar(select(Handoff).where(
        Handoff.id == handoff_id, Handoff.tenant_id == tenant_id,
        Handoff.user_id == user_id, Handoff.active_key == "active",
        Handoff.status == "awaiting_confirmation",
    ))
    if handoff is None:
        return None
    if confirm:
        _end(handoff)
    else:
        handoff.status = "in_progress"
        handoff.close_requested_at = None
        handoff.close_deadline = None
    await session.commit(); await session.refresh(handoff)
    return handoff


def _end(handoff: Handoff, now: datetime | None = None) -> None:
    handoff.status = "ended"
    handoff.active_key = None
    handoff.ended_at = now or datetime.now(timezone.utc)
    handoff.close_deadline = None


async def close_expired(session: AsyncSession, *, tenant_id=None, conversation_id=None) -> int:
    now = datetime.now(timezone.utc)
    query = select(Handoff).where(
        Handoff.status == "awaiting_confirmation", Handoff.active_key == "active",
        Handoff.close_deadline.is_not(None), Handoff.close_deadline <= now,
    ).with_for_update(skip_locked=True)
    if tenant_id is not None:
        query = query.where(Handoff.tenant_id == tenant_id)
    if conversation_id is not None:
        query = query.where(Handoff.conversation_id == conversation_id)
    rows = list((await session.scalars(query)).all())
    for row in rows:
        _end(row, now)
    if rows:
        await session.commit()
    return len(rows)


async def add_customer_message(session: AsyncSession, *, handoff: Handoff, content: str) -> Message:
    """保存离线留言；不创建 im.inbound 事件，因此不会触发 AI worker。"""
    message = Message(
        message_id=f"handoff-note-{uuid.uuid4()}", tenant_id=handoff.tenant_id,
        conversation_id=handoff.conversation_id, role="user", content=content,
        status="completed", trace_id=get_trace_id() or f"handoff-note-{handoff.id}",
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message
