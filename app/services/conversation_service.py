"""会话 / 消息编排：入站摄取（去重 + 事务型 outbox）、回复落库、历史查询。"""

import uuid
from uuid import uuid4

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, ForbiddenError
from app.core.logging import get_logger, trace_id_var
from app.models import Conversation, Message, OutboxEvent
from app.schemas.event import EventEnvelope, EventType
from app.schemas.message import InboundMessage
from app.core.tracing import current_traceparent

log = get_logger(__name__)


def _to_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        raise AppError(f"{field} 不是合法 UUID: {value}", code="BAD_REQUEST", status_code=400)


async def conversation_belongs_to_tenant(
    session: AsyncSession, tenant_id, conversation_id
) -> bool:
    """会话存在且属于该租户时返回 True（租户隔离的前置校验）。"""
    row = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.tenant_id == tenant_id
        )
    )
    return row is not None


async def conversation_belongs_to_user(
    session: AsyncSession, tenant_id, conversation_id, user_id
) -> bool:
    """普通用户资源归属校验：会话必须同时属于 tenant 和 user。"""
    row = await session.scalar(
        select(Conversation.id).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == tenant_id,
            Conversation.user_id == user_id,
        )
    )
    return row is not None


async def get_inbound(session: AsyncSession, tenant_id, message_id: str) -> Message | None:
    return await session.scalar(
        select(Message).where(
            Message.tenant_id == tenant_id, Message.message_id == message_id
        )
    )


async def ingest_message(session: AsyncSession, msg: InboundMessage) -> dict:
    """去重登记 -> 消息 + im.inbound outbox 同事务落库 -> 返回 ACK（不等待下游）。

    outbox 由后台 relay 发布到 JetStream，消除「DB 已写、队列未发」双写窗口（PLAN S2 §6.2）。
    """
    tenant_id = _to_uuid(msg.tenant_id, "tenant_id")
    conversation_id = _to_uuid(msg.conversation_id, "conversation_id")
    user_id = _to_uuid(msg.user_id, "user_id")

    if not await conversation_belongs_to_user(
        session, tenant_id, conversation_id, user_id
    ):
        raise ForbiddenError("会话不存在或不属于当前用户")

    existing = await get_inbound(session, tenant_id, msg.message_id)
    if existing is not None:
        return {"status": "duplicate", "message_id": msg.message_id, "state": existing.status}

    trace_id = trace_id_var.get() or str(uuid4())
    envelope = EventEnvelope(
        trace_id=trace_id,
        tenant_id=str(tenant_id),
        type=EventType.IM_INBOUND,
        traceparent=current_traceparent(),
        payload={
            "message_id": msg.message_id,
            "tenant_id": str(tenant_id),
            "user_id": msg.user_id,
            "conversation_id": str(conversation_id),
            "content": msg.content,
        },
    )
    session.add(
        Message(
            message_id=msg.message_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            role="user",
            content=msg.content,
            status="acknowledged",
            trace_id=trace_id,
        )
    )
    session.add(
        OutboxEvent(
            event_id=envelope.event_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            subject=EventType.IM_INBOUND,
            payload=envelope.model_dump(mode="json"),
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return {"status": "duplicate", "message_id": msg.message_id, "state": "unknown"}

    return {"status": "accepted", "message_id": msg.message_id}


async def mark_processing(
    session: AsyncSession,
    tenant_id,
    message_id: str,
    *,
    now: datetime | None = None,
    lease_seconds: int = 60,
) -> bool:
    """原子取得处理租约；未过期 processing 拒绝，过期后允许重投恢复。"""
    tenant_uuid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(tenant_id)
    current = now or datetime.now(timezone.utc)
    result = await session.execute(
        update(Message)
        .where(
            Message.tenant_id == tenant_uuid,
            Message.message_id == message_id,
            Message.status != "completed",
            or_(
                Message.status != "processing",
                Message.processing_lease_until.is_(None),
                Message.processing_lease_until <= current,
            ),
        )
        .values(
            status="processing",
            processing_lease_until=current + timedelta(seconds=lease_seconds),
        )
    )
    await session.commit()
    return result.rowcount == 1


async def save_reply(
    session: AsyncSession,
    *,
    tenant_id,
    conversation_id,
    reply_message_id: str,
    content: str,
    complete_message_id: str,
    user_id: str,
    trace_id: str,
) -> None:
    """落库机器人回复 + im.outbound outbox 同事务；并把对应入站置为 completed。"""
    envelope = EventEnvelope(
        trace_id=trace_id,
        tenant_id=str(tenant_id),
        type=EventType.IM_OUTBOUND,
        payload={
            "message_id": reply_message_id,
            "tenant_id": str(tenant_id),
            "user_id": user_id,
            "conversation_id": str(conversation_id),
            "content": content,
            "in_reply_to": complete_message_id,
        },
    )
    session.add(
        Message(
            message_id=reply_message_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            status="completed",
            trace_id=trace_id,
        )
    )
    session.add(
        OutboxEvent(
            event_id=envelope.event_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            subject=EventType.IM_OUTBOUND,
            payload=envelope.model_dump(mode="json"),
        )
    )
    inbound = await get_inbound(session, tenant_id, complete_message_id)
    if inbound is not None:
        inbound.status = "completed"
        inbound.processing_lease_until = None
    await refresh_conversation_summary(session, tenant_id, conversation_id)
    await session.commit()


async def refresh_conversation_summary(
    session: AsyncSession, tenant_id, conversation_id, *, context_window: int = 20
) -> str | None:
    """Persist a compact deterministic summary of messages older than the hot window."""
    rows = list((await session.scalars(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .offset(context_window)
        .limit(20)
    )).all())
    conversation = await session.scalar(select(Conversation).where(
        Conversation.id == conversation_id, Conversation.tenant_id == tenant_id
    ))
    if conversation is None:
        return None
    if not rows:
        return conversation.summary
    lines = [
        f"{'用户' if row.role == 'user' else '客服'}：{' '.join(row.content.split())[:120]}"
        for row in reversed(rows)
    ]
    conversation.summary = "\n".join(lines)[-2000:]
    return conversation.summary


async def complete_without_reply(session: AsyncSession, tenant_id, message_id: str) -> None:
    """人工已接管时确认入站处理完成，但不生成机器人消息。"""
    inbound = await get_inbound(session, tenant_id, message_id)
    if inbound is not None:
        inbound.status = "completed"
        inbound.processing_lease_until = None
    await session.commit()


async def list_messages(session: AsyncSession, tenant_id, conversation_id) -> list[dict]:
    rows = await session.scalars(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    return [_serialize(r) for r in rows]


async def list_messages_after(
    session: AsyncSession, tenant_id, conversation_id, after_message_id: str
) -> list[dict]:
    tenant_uuid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(tenant_id)
    conversation_uuid = (
        conversation_id
        if isinstance(conversation_id, uuid.UUID)
        else uuid.UUID(conversation_id)
    )
    cursor = await session.scalar(
        select(Message).where(
            Message.tenant_id == tenant_uuid,
            Message.conversation_id == conversation_uuid,
            Message.message_id == after_message_id,
        )
    )
    if cursor is None:
        return []
    rows = await session.scalars(
        select(Message)
        .where(
            Message.tenant_id == tenant_uuid,
            Message.conversation_id == conversation_uuid,
            or_(
                Message.created_at > cursor.created_at,
                (Message.created_at == cursor.created_at) & (Message.id > cursor.id),
            ),
        )
        .order_by(Message.created_at, Message.id)
    )
    return [_serialize(row) for row in rows]


def _serialize(row: Message) -> dict:
    return {
        "id": str(row.id),
        "message_id": row.message_id,
        "tenant_id": str(row.tenant_id),
        "conversation_id": str(row.conversation_id),
        "role": row.role,
        "content": row.content,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
