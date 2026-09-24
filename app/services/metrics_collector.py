"""Low-frequency snapshots for metrics whose source of truth is NATS or SQL."""

import asyncio

from sqlalchemy import func, select

from app.core import nats as nats_core
from app.core.logging import get_logger
from app.core.metrics import (
    DEAD_LETTER_OPEN,
    HANDOFF_ACTIVE,
    LLM_TOKEN_USAGE,
    OUTBOX_STATUS,
    QUEUE_ACK_PENDING,
    QUEUE_PENDING,
)
from app.models import DeadLetter, Handoff, LLMUsage, OutboxEvent

log = get_logger(__name__)
DEFAULT_CONSUMERS = ("im-inbound-worker", "im-outbound-mockim")


async def collect_nats_metrics(js, *, consumers=DEFAULT_CONSUMERS) -> None:
    for consumer in consumers:
        try:
            info = await js.consumer_info(nats_core.STREAM_NAME, consumer)
        except Exception:
            # A consumer may legitimately not exist before its service starts.
            continue
        labels = {"stream": nats_core.STREAM_NAME, "consumer": consumer}
        QUEUE_PENDING.labels(**labels).set(info.num_pending)
        QUEUE_ACK_PENDING.labels(**labels).set(info.num_ack_pending)


async def collect_database_metrics(session_factory) -> None:
    async with session_factory() as session:
        outbox_rows = await session.execute(
            select(OutboxEvent.status, func.count(OutboxEvent.id)).group_by(OutboxEvent.status)
        )
        observed_statuses = set()
        for status, count in outbox_rows:
            observed_statuses.add(status)
            OUTBOX_STATUS.labels(status).set(count)
        for status in {"pending", "publishing", "published", "failed"} - observed_statuses:
            OUTBOX_STATUS.labels(status).set(0)

        dead_letters = await session.scalar(
            select(func.count(DeadLetter.id)).where(DeadLetter.status == "open")
        )
        active_handoffs = await session.scalar(
            select(func.count(Handoff.id)).where(Handoff.active_key == "active")
        )
        DEAD_LETTER_OPEN.set(dead_letters or 0)
        HANDOFF_ACTIVE.set(active_handoffs or 0)

        usage_rows = await session.execute(
            select(
                LLMUsage.tenant_id,
                LLMUsage.model,
                func.sum(LLMUsage.prompt_tokens),
                func.sum(LLMUsage.completion_tokens),
            ).group_by(LLMUsage.tenant_id, LLMUsage.model)
        )
        for tenant_id, model, prompt, completion in usage_rows:
            labels = {"tenant": str(tenant_id), "model": model}
            LLM_TOKEN_USAGE.labels(**labels, token_type="prompt").set(prompt or 0)
            LLM_TOKEN_USAGE.labels(**labels, token_type="completion").set(completion or 0)


async def collection_loop(js, session_factory, *, interval_seconds: float = 5.0) -> None:
    while True:
        try:
            await collect_nats_metrics(js)
            await collect_database_metrics(session_factory)
        except Exception:  # noqa: BLE001
            log.exception("runtime metric collection failed")
        await asyncio.sleep(interval_seconds)
