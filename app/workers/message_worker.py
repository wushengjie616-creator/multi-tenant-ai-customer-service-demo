"""入站消息消费 worker。

第 2 步：消费 im.inbound -> mock-llm 生成回复 -> 落库（含 im.outbound outbox）-> relay 发布。
"""

import asyncio
import hashlib
import json
import uuid

from app.core import nats as nats_core
from app.core.config import settings
from app.core.database import async_session
from app.core.logging import get_logger
from app.core.metrics import MESSAGE_PROCESSING
from prometheus_client import start_http_server
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


async def process(msg, *, stream_publisher=None) -> str:
    envelope = json.loads(msg.data)
    with event_span("nats.process im.inbound", envelope):
        return await _process_envelope(envelope, stream_publisher=stream_publisher)


async def _process_envelope(envelope: dict, *, stream_publisher=None) -> str:
    payload = envelope.get("payload", {})
    tenant_id = payload["tenant_id"]
    message_id = payload["message_id"]
    conversation_id = payload["conversation_id"]
    content = payload.get("content", "")
    trace_id = envelope.get("trace_id") or str(uuid.uuid4())
    reply_key = hashlib.sha256(
        f"{tenant_id}:{message_id}".encode("utf-8")
    ).hexdigest()[:48]
    reply_message_id = f"reply-{reply_key}"

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
        streamed = False
        stream_sequence = 0

        async def publish_delta(delta: str) -> None:
            nonlocal streamed, stream_sequence
            if stream_publisher is None:
                return
            if not streamed:
                start = EventEnvelope(
                    trace_id=trace_id,
                    tenant_id=tenant_id,
                    type="reply.start",
                    payload={
                        "message_id": reply_message_id,
                        "conversation_id": conversation_id,
                        "in_reply_to": message_id,
                    },
                )
                await stream_publisher(start)
                streamed = True
            chunk = EventEnvelope(
                trace_id=trace_id,
                tenant_id=tenant_id,
                type="reply.chunk",
                payload={
                    "message_id": reply_message_id,
                    "conversation_id": conversation_id,
                    "in_reply_to": message_id,
                    "sequence": stream_sequence,
                    "delta": delta,
                },
            )
            stream_sequence += 1
            await stream_publisher(chunk)

        try:
            reply_text = await generate_reply(
                session,
                payload,
                context_store=conversation_context,
                on_token=publish_delta if stream_publisher is not None else None,
            )
        finally:
            usage_calls = llm_usage_context.finish(usage_token)

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
            streamed=streamed,
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


def _conversation_key(msg) -> str:
    try:
        payload = json.loads(msg.data).get("payload", {})
        return f"{payload.get('tenant_id', 'unknown')}:{payload.get('conversation_id', 'unknown')}"
    except (TypeError, json.JSONDecodeError):
        return "invalid"


async def _handle_message(js, msg, *, processor=process) -> None:
    started = asyncio.get_running_loop().time()
    outcome = "failed"
    try:
        result = await processor(msg)
        outcome = result
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
            outcome = "dead_lettered"
        else:
            await msg.nak(delay=PROCESSING_LEASE_SECONDS)
            outcome = "retry"
    finally:
        MESSAGE_PROCESSING.labels(outcome).observe(
            asyncio.get_running_loop().time() - started
        )


async def process_batch(js, messages, *, concurrency: int, processor=process) -> None:
    """Process different conversations concurrently while preserving per-chat order."""
    groups: dict[str, list] = {}
    for msg in messages:
        groups.setdefault(_conversation_key(msg), []).append(msg)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run_group(group) -> None:
        async with semaphore:
            for msg in group:
                await _handle_message(js, msg, processor=processor)

    await asyncio.gather(*(run_group(group) for group in groups.values()))


async def main() -> None:
    configure_tracing()
    start_http_server(settings.worker_metrics_port)
    nc = await nats_core.connect()
    js = nc.jetstream()
    await nats_core.ensure_stream(js)

    # 发布本进程写入的 im.outbound outbox 事件
    relay_task = asyncio.create_task(relay_loop(js, async_session))

    subscription = await nats_core.pull_subscribe(
        js, nats_core.INBOUND_SUBJECT, durable="im-inbound-worker"
    )

    async def publish_live_stream(event: EventEnvelope) -> None:
        await nc.publish(
            nats_core.OUTBOUND_STREAM_SUBJECT,
            event.model_dump_json().encode("utf-8"),
        )

    async def process_with_stream(msg) -> str:
        return await process(msg, stream_publisher=publish_live_stream)
    log.info(
        "message worker pull consumer ready subject=%s batch=%s concurrency=%s",
        nats_core.INBOUND_SUBJECT,
        settings.worker_fetch_batch,
        settings.worker_concurrency,
    )
    try:
        from nats.errors import TimeoutError as NatsTimeoutError

        while True:
            try:
                messages = await subscription.fetch(
                    batch=settings.worker_fetch_batch, timeout=1
                )
            except NatsTimeoutError:
                continue
            await process_batch(
                js,
                messages,
                concurrency=settings.worker_concurrency,
                processor=process_with_stream,
            )
    finally:
        relay_task.cancel()
        await nc.close()
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
