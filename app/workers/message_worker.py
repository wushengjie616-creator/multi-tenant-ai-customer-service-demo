"""入站消息消费 worker。

第 2 步：消费 im.inbound -> mock-llm 生成回复 -> 落库（含 im.outbound outbox）-> relay 发布。
"""

import asyncio
import hashlib
import json
import uuid

from app.core import nats as nats_core
from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis import redis_client
from app.schemas.event import EventEnvelope
from app.services import conversation_service
from app.services import dead_letter_service
from app.services import handoff_service
from app.services import llm_usage_context, llm_usage_service
from app.services.assistant_service import generate_reply
from app.services.context_service import conversation_context
from app.services.outbox_relay import relay_loop
from app.core.tracing import configure_tracing, event_span

log = get_logger(__name__)
PROCESSING_LEASE_SECONDS = 120
MAX_DELIVERIES = 5


async def process(msg) -> str:
    envelope = json.loads(msg.data)
    with event_span("nats.process im.inbound", envelope):
        return await _process_envelope(envelope)


async def _process_envelope(envelope: dict) -> str:
    payload = envelope.get("payload", {})
    tenant_id = payload["tenant_id"]
    message_id = payload["message_id"]
    conversation_id = payload["conversation_id"]
    content = payload.get("content", "")
    trace_id = envelope.get("trace_id") or str(uuid.uuid4())

    tenant_uuid = uuid.UUID(tenant_id)
    conversation_uuid = uuid.UUID(conversation_id)

    async with async_session() as session:
        if not await conversation_service.mark_processing(
            session,
            tenant_uuid,
            message_id,
            lease_seconds=PROCESSING_LEASE_SECONDS,
        ):
            existing = await conversation_service.get_inbound(
                session, tenant_uuid, message_id
            )
            if existing is not None and existing.status == "completed":
                return "completed"
            return "busy"

        if await handoff_service.get_active(session, tenant_uuid, conversation_uuid):
            await conversation_service.complete_without_reply(session, tenant_uuid, message_id)
            await conversation_context.append_many(tenant_id, conversation_id, [{
                "message_id": message_id, "role": "user", "content": content,
            }])
            return "routed_to_agent"

        usage_token = llm_usage_context.begin()
        try:
            reply_text = await generate_reply(
                session, payload, context_store=conversation_context
            )
        finally:
            usage_calls = llm_usage_context.finish(usage_token)
        reply_key = hashlib.sha256(
            f"{tenant_id}:{message_id}".encode("utf-8")
        ).hexdigest()[:48]
        reply_message_id = f"reply-{reply_key}"

        await llm_usage_service.record_calls(
            session, tenant_id=tenant_uuid, conversation_id=conversation_uuid,
            message_id=message_id, calls=usage_calls,
        )

        await conversation_service.save_reply(
            session,
            tenant_id=tenant_uuid,
            conversation_id=conversation_uuid,
            reply_message_id=reply_message_id,
            content=reply_text,
            complete_message_id=message_id,
            user_id=payload.get("user_id"),
            trace_id=trace_id,
        )
        await conversation_context.append_many(
            tenant_id,
            conversation_id,
            [
                {
                    "message_id": message_id,
                    "role": "user",
                    "content": content,
                },
                {
                    "message_id": reply_message_id,
                    "role": "assistant",
                    "content": reply_text,
                },
            ],
        )
    return "processed"


async def _dead_letter(js, msg, error: Exception) -> None:
    """最后一次投递失败时发布不含原始正文的死信事件。"""
    try:
        original = json.loads(msg.data)
    except (TypeError, json.JSONDecodeError):
        original = {}
    async with async_session() as session:
        await dead_letter_service.record(session, original=original, error=error)
    envelope = EventEnvelope(
        trace_id=original.get("trace_id") or str(uuid.uuid4()),
        tenant_id=original.get("tenant_id") or "unknown",
        type="deadletter.im.inbound",
        payload={
            "original_event_id": original.get("event_id"),
            "original_subject": nats_core.INBOUND_SUBJECT,
            "error_type": type(error).__name__,
            "error": str(error)[:500],
        },
    )
    await nats_core.publish(js, "deadletter.im.inbound", envelope)


async def main() -> None:
    configure_tracing()
    nc = await nats_core.connect()
    js = nc.jetstream()
    await nats_core.ensure_stream(js)

    # 发布本进程写入的 im.outbound outbox 事件
    relay_task = asyncio.create_task(relay_loop(js, async_session))

    async def handler(msg):
        try:
            result = await process(msg)
            if result == "busy":
                await msg.nak(delay=PROCESSING_LEASE_SECONDS)
            else:
                await msg.ack()
        except Exception as exc:  # noqa: BLE001
            log.exception("worker process failed")
            delivered = getattr(getattr(msg, "metadata", None), "num_delivered", 1)
            if delivered >= MAX_DELIVERIES:
                await _dead_letter(js, msg, exc)
                await msg.ack()
            else:
                await msg.nak(delay=PROCESSING_LEASE_SECONDS)

    await nats_core.subscribe(
        js,
        nats_core.INBOUND_SUBJECT,
        durable="im-inbound-worker",
        cb=handler,
    )
    log.info("message worker subscribed to %s", nats_core.INBOUND_SUBJECT)
    try:
        await asyncio.Future()
    finally:
        relay_task.cancel()
        await nc.close()
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
