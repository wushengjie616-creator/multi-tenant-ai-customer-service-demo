"""事务型 outbox relay：把 pending 事件发布到 NATS，成功后标记 published。"""

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core.logging import get_logger
from app.core.nats import publish_bytes
from app.models import OutboxEvent

log = get_logger(__name__)

CLAIM_STALE_SECONDS = 60
BATCH_SIZE = settings.outbox_batch_size
PUBLISH_CONCURRENCY = settings.outbox_publish_concurrency
MAX_ATTEMPTS = 5
RETRY_DELAYS = (30, 120, 600, 1800)


async def relay_once(
    js,
    session_factory: async_sessionmaker,
    *,
    now: datetime | None = None,
    retry_delays: tuple[int, ...] = RETRY_DELAYS,
    batch_size: int = BATCH_SIZE,
    publish_concurrency: int = PUBLISH_CONCURRENCY,
) -> int:
    """声明并发布一批 pending 事件，返回成功发布条数。"""
    current = now or datetime.now(timezone.utc)
    claimed = await _claim(session_factory, current, batch_size=batch_size)
    semaphore = asyncio.Semaphore(max(1, publish_concurrency))

    async def publish_one(
        pk: uuid.UUID, event_id: str, subject: str, payload: dict
    ) -> tuple[uuid.UUID, str | None]:
        try:
            async with semaphore:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                await publish_bytes(js, subject, data, message_id=event_id)
            return pk, None
        except Exception as exc:  # noqa: BLE001
            log.warning("outbox publish failed event_id=%s err=%s", pk, exc)
            return pk, str(exc)

    results = await asyncio.gather(*(publish_one(*event) for event in claimed))
    await _mark_results(
        session_factory, results, current, retry_delays=retry_delays
    )
    return sum(error is None for _, error in results)


async def _claim(
    session_factory: async_sessionmaker,
    now: datetime,
    *,
    batch_size: int = BATCH_SIZE,
) -> list[tuple[uuid.UUID, str, str, dict]]:
    """用 `FOR UPDATE SKIP LOCKED` 抢占 pending 事件，并回吸卡死的 publishing。"""
    stale = now - timedelta(seconds=CLAIM_STALE_SECONDS)
    async with session_factory() as session:
        result = await session.execute(
            select(OutboxEvent)
            .where(
                (
                    (OutboxEvent.status == "pending")
                    & (OutboxEvent.next_attempt_at <= now)
                    & (OutboxEvent.attempts < MAX_ATTEMPTS)
                )
                | (
                    (OutboxEvent.status == "publishing")
                    & (OutboxEvent.claimed_at < stale)
                    & (OutboxEvent.attempts < MAX_ATTEMPTS)
                )
            )
            .order_by(OutboxEvent.created_at)
            .limit(max(1, batch_size))
            .with_for_update(skip_locked=True)
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.status = "publishing"
            row.attempts += 1
            row.claimed_at = now
        await session.commit()
        return [(r.id, r.event_id, r.subject, r.payload) for r in rows]


async def _mark_results(
    session_factory: async_sessionmaker,
    results: list[tuple[uuid.UUID, str | None]],
    now: datetime,
    *,
    retry_delays: tuple[int, ...] = RETRY_DELAYS,
) -> None:
    """在一个事务中批量确认成功项，并记录各失败项的退避状态。"""
    successful = [pk for pk, error in results if error is None]
    failures = {pk: error for pk, error in results if error is not None}
    async with session_factory() as session:
        if successful:
            await session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.id.in_(successful),
                    OutboxEvent.status == "publishing",
                )
                .values(status="published", published_at=now, claimed_at=None)
            )
        if failures:
            rows = list(
                (
                    await session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.id.in_(failures),
                            OutboxEvent.status == "publishing",
                        )
                    )
                ).all()
            )
        else:
            rows = []
        for row in rows:
            if row.attempts >= MAX_ATTEMPTS:
                row.status = "failed"
            else:
                row.status = "pending"
                delay_index = min(max(row.attempts - 1, 0), len(retry_delays) - 1)
                row.next_attempt_at = now + timedelta(seconds=retry_delays[delay_index])
            row.claimed_at = None
            row.last_error = (failures[row.id] or "unknown publish failure")[:2000]
        await session.commit()


async def relay_loop(
    js,
    session_factory: async_sessionmaker,
    *,
    poll_interval: float = settings.outbox_poll_interval_seconds,
) -> None:
    """后台循环发布 pending outbox 事件。"""
    while True:
        try:
            await relay_once(js, session_factory)
        except Exception:  # noqa: BLE001
            log.exception("outbox relay iteration failed")
        await asyncio.sleep(poll_interval)
