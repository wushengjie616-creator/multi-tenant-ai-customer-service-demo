"""事务型 outbox relay：把 pending 事件发布到 NATS，成功后标记 published。"""

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.logging import get_logger
from app.core.nats import publish_bytes
from app.models import OutboxEvent

log = get_logger(__name__)

CLAIM_STALE_SECONDS = 60
BATCH_SIZE = 20
MAX_ATTEMPTS = 5
RETRY_DELAYS = (30, 120, 600, 1800)


async def relay_once(
    js,
    session_factory: async_sessionmaker,
    *,
    now: datetime | None = None,
    retry_delays: tuple[int, ...] = RETRY_DELAYS,
) -> int:
    """声明并发布一批 pending 事件，返回成功发布条数。"""
    current = now or datetime.now(timezone.utc)
    claimed = await _claim(session_factory, current)
    published = 0
    for pk, subject, payload in claimed:
        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            await publish_bytes(js, subject, data)
            await _mark_published(session_factory, pk, current)
            published += 1
        except Exception as exc:  # noqa: BLE001
            await _mark_failed(
                session_factory, pk, str(exc), current, retry_delays=retry_delays
            )
            log.warning("outbox publish failed event_id=%s err=%s", pk, exc)
    return published


async def _claim(
    session_factory: async_sessionmaker, now: datetime
) -> list[tuple[uuid.UUID, str, dict]]:
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
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.status = "publishing"
            row.attempts += 1
            row.claimed_at = now
        await session.commit()
        return [(r.id, r.subject, r.payload) for r in rows]


async def _mark_published(
    session_factory: async_sessionmaker, pk: uuid.UUID, now: datetime
) -> None:
    async with session_factory() as session:
        row = await session.get(OutboxEvent, pk)
        if row is not None:
            row.status = "published"
            row.published_at = now
            row.claimed_at = None
            await session.commit()


async def _mark_failed(
    session_factory: async_sessionmaker,
    pk: uuid.UUID,
    error: str,
    now: datetime,
    *,
    retry_delays: tuple[int, ...] = RETRY_DELAYS,
) -> None:
    async with session_factory() as session:
        row = await session.get(OutboxEvent, pk)
        if row is not None:
            if row.attempts >= MAX_ATTEMPTS:
                row.status = "failed"
            else:
                row.status = "pending"
                delay_index = min(max(row.attempts - 1, 0), len(retry_delays) - 1)
                row.next_attempt_at = now + timedelta(seconds=retry_delays[delay_index])
            row.claimed_at = None
            row.last_error = error[:2000]
            await session.commit()


async def relay_loop(js, session_factory: async_sessionmaker, *, poll_interval: float = 0.1) -> None:
    """后台循环发布 pending outbox 事件。"""
    while True:
        try:
            await relay_once(js, session_factory)
        except Exception:  # noqa: BLE001
            log.exception("outbox relay iteration failed")
        await asyncio.sleep(poll_interval)
