"""P2 消息处理租约与 Outbox 有界重试行为。"""

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.database import async_session
from app.models import Message, OutboxEvent
from app.schemas.message import InboundMessage
from app.services import conversation_service, outbox_relay
from app.workers import message_worker


async def test_stale_processing_message_can_be_reclaimed(make_user, make_conversation):
    """worker 在 processing 后崩溃，租约过期后重投必须能重新取得处理权。"""
    user = await make_user()
    conversation_id = await make_conversation(user["tenant_id"], user["user_id"])
    inbound = InboundMessage(
        message_id="lease-recovery-1",
        tenant_id=user["tenant_id"],
        user_id=user["user_id"],
        conversation_id=conversation_id,
        content="hello",
    )
    async with async_session() as session:
        await conversation_service.ingest_message(session, inbound)
        first = await conversation_service.mark_processing(
            session,
            user["tenant_id"],
            inbound.message_id,
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
            lease_seconds=60,
        )
    assert first is True

    async with async_session() as session:
        before_expiry = await conversation_service.mark_processing(
            session,
            user["tenant_id"],
            inbound.message_id,
            now=datetime(2026, 1, 1, 0, 0, 30, tzinfo=timezone.utc),
            lease_seconds=60,
        )
        after_expiry = await conversation_service.mark_processing(
            session,
            user["tenant_id"],
            inbound.message_id,
            now=datetime(2026, 1, 1, 0, 1, 1, tzinfo=timezone.utc),
            lease_seconds=60,
        )
    assert before_expiry is False
    assert after_expiry is True


async def test_outbox_reaches_failed_terminal_state_after_max_attempts(
    monkeypatch, make_user
):
    """持续发布失败不能无限 pending；达到上限后必须进入 failed。"""
    user = await make_user()
    async with async_session() as session:
        event = OutboxEvent(
            event_id="outbox-terminal-1",
            trace_id="trace-terminal-1",
            tenant_id=user["tenant_id"],
            subject="im.inbound",
            payload={"type": "im.inbound"},
        )
        session.add(event)
        await session.commit()
        event_pk = event.id

    async def fail_publish(*args, **kwargs):
        raise RuntimeError("nats unavailable")

    monkeypatch.setattr(outbox_relay, "publish_bytes", fail_publish)
    for attempt in range(outbox_relay.MAX_ATTEMPTS):
        await outbox_relay.relay_once(
            object(),
            async_session,
            now=datetime(2030, 1, 1, tzinfo=timezone.utc)
            + timedelta(minutes=attempt),
            retry_delays=(0, 0, 0, 0),
        )

    async with async_session() as session:
        row = await session.scalar(select(OutboxEvent).where(OutboxEvent.id == event_pk))
    assert row.status == "failed"
    assert row.attempts == outbox_relay.MAX_ATTEMPTS
    assert "nats unavailable" in row.last_error


async def test_message_cursor_returns_only_later_messages(make_user, make_conversation):
    user = await make_user()
    conversation_id = await make_conversation(user["tenant_id"], user["user_id"])
    async with async_session() as session:
        for number in range(3):
            await conversation_service.ingest_message(
                session,
                InboundMessage(
                    message_id=f"cursor-{number}",
                    tenant_id=user["tenant_id"],
                    user_id=user["user_id"],
                    conversation_id=conversation_id,
                    content=f"message {number}",
                ),
            )
        rows = await conversation_service.list_messages_after(
            session,
            user["tenant_id"],
            conversation_id,
            "cursor-0",
        )
    assert [row["message_id"] for row in rows] == ["cursor-1", "cursor-2"]


async def test_worker_processes_inbound_and_persists_one_reply(make_user, make_conversation, monkeypatch):
    user = await make_user()
    conversation_id = await make_conversation(user["tenant_id"], user["user_id"])
    inbound = InboundMessage(
        message_id="worker-message-1", tenant_id=user["tenant_id"],
        user_id=user["user_id"], conversation_id=conversation_id, content="你好",
    )
    async with async_session() as session:
        await conversation_service.ingest_message(session, inbound)
        outbox = await session.scalar(select(OutboxEvent).where(OutboxEvent.event_id.is_not(None)))

    async def reply(*args, **kwargs):
        return "已收到"

    monkeypatch.setattr(message_worker, "generate_reply", reply)

    class FakeMessage:
        data = json.dumps(outbox.payload).encode()

    assert await message_worker.process(FakeMessage()) == "processed"
    assert await message_worker.process(FakeMessage()) == "completed"
    async with async_session() as session:
        rows = list((await session.scalars(select(Message).where(
            Message.tenant_id == user["tenant_id"],
            Message.conversation_id == conversation_id,
        ).order_by(Message.created_at, Message.role.desc()))).all())
    assert [(row.role, row.status, row.content) for row in rows] == [
        ("user", "completed", "你好"),
        ("assistant", "completed", "已收到"),
    ]
