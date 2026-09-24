"""NATS JetStream 连接、stream 初始化与发布/订阅工具（至少一次投递）。"""

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

STREAM_NAME = "EVENTS"
STREAM_SUBJECTS = [
    "im.inbound",
    "im.outbound",
    "reminder.due",
    "tool.retry",
    "deadletter.>",
]
INBOUND_SUBJECT = "im.inbound"
OUTBOUND_SUBJECT = "im.outbound"
# Ephemeral token deltas: deliberately excluded from JetStream persistence.
# PostgreSQL + im.outbound retain only the authoritative completed reply.
OUTBOUND_STREAM_SUBJECT = "im.outbound.stream"


async def connect():
    """建立 NATS 连接（robust reconnect）。"""
    import nats

    return await nats.connect(
        settings.nats_url,
        name="eduai",
        max_reconnect_attempts=-1,
        reconnect_time_wait=1,
    )


async def ensure_stream(js) -> None:
    """幂等创建 EVENTS stream。"""
    try:
        await js.stream_info(STREAM_NAME)
        return
    except Exception:  # noqa: BLE001 — 不存在则继续创建
        pass
    try:
        await js.add_stream(name=STREAM_NAME, subjects=STREAM_SUBJECTS)
        log.info("jetstream stream created name=%s", STREAM_NAME)
    except Exception as exc:  # noqa: BLE001
        if "already in use" in str(exc).lower() or "exists" in str(exc).lower():
            return
        raise


async def publish_bytes(
    js, subject: str, data: bytes, *, message_id: str | None = None
) -> None:
    """发布字节到 EVENTS stream（JetStream ack 即 publisher confirm）。"""
    kwargs = {"stream": STREAM_NAME}
    if message_id:
        kwargs["headers"] = {"Nats-Msg-Id": message_id}
    await js.publish(subject, data, **kwargs)


async def publish(js, subject: str, envelope) -> None:
    """发布 EventEnvelope 到指定主题（序列化为 JSON 字节）。"""
    await publish_bytes(
        js, subject, envelope.model_dump_json().encode("utf-8"),
        message_id=envelope.event_id,
    )


async def subscribe(js, subject: str, durable: str, cb) -> None:
    """创建 durable push 订阅（显式 ack，至少一次投递）。"""
    from nats.js.api import AckPolicy, ConsumerConfig

    config = ConsumerConfig(
        durable_name=durable,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=150,
        max_deliver=5,
        filter_subject=subject,
    )
    await js.subscribe(
        subject,
        durable=durable,
        cb=cb,
        stream=STREAM_NAME,
        config=config,
        manual_ack=True,
    )


async def subscribe_core(nc, subject: str, cb):
    """Subscribe without a queue group so every API instance receives live events.

    JetStream still persists the same publication for recovery/audit.  WebSocket
    reconnects recover from PostgreSQL, while NATS Core provides replica-wide
    live fan-out without competing for a shared durable consumer.
    """
    return await nc.subscribe(subject, cb=cb)


async def pull_subscribe(js, subject: str, durable: str):
    """创建 durable pull 订阅，并安全迁移旧版 push durable。"""
    from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy

    try:
        info = await js.consumer_info(STREAM_NAME, durable)
    except Exception:  # noqa: BLE001 - 不存在时由 pull_subscribe 创建
        return await js.pull_subscribe(subject, durable=durable, stream=STREAM_NAME)

    if info.config.deliver_subject:
        # Consumer type cannot be mutated in place. Continue immediately after
        # the old ACK floor so acknowledged history isn't replayed and pending
        # messages aren't skipped during an in-place upgrade.
        start_sequence = max(1, info.ack_floor.stream_seq + 1)
        await js.delete_consumer(STREAM_NAME, durable)
        config = ConsumerConfig(
            durable_name=durable,
            deliver_policy=DeliverPolicy.BY_START_SEQUENCE,
            opt_start_seq=start_sequence,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=150,
            max_deliver=5,
            filter_subject=subject,
        )
        log.info(
            "migrating push consumer to pull durable=%s start_sequence=%s",
            durable,
            start_sequence,
        )
        return await js.pull_subscribe(
            subject, durable=durable, stream=STREAM_NAME, config=config
        )
    return await js.pull_subscribe(subject, durable=durable, stream=STREAM_NAME)
