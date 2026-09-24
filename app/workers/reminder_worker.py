"""持久提醒调度：行锁抢占、occurrence 幂等、outbox 投递。"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.core import nats as nats_core
from app.core.database import async_session
from app.core.logging import get_logger
from app.models import OutboxEvent, Reminder, ReminderDelivery
from app.schemas.event import EventEnvelope, EventType
from app.services.outbox_relay import relay_loop
from app.services.reminder_service import advance_occurrence
from app.services.handoff_service import close_expired

log = get_logger(__name__)


async def dispatch_due(now: datetime | None = None, limit: int = 50) -> int:
    current = now or datetime.now(timezone.utc)
    async with async_session() as session:
        rows = list((await session.scalars(select(Reminder).where(Reminder.status == "active", Reminder.next_run_at <= current).order_by(Reminder.next_run_at).limit(limit).with_for_update(skip_locked=True))).all())
        for reminder in rows:
            occurrence = reminder.next_run_at
            delivery = ReminderDelivery(reminder_id=reminder.id, occurrence_at=occurrence, status="dispatched")
            session.add(delivery); await session.flush()
            envelope = EventEnvelope(trace_id=f"reminder-{delivery.id}", tenant_id=str(reminder.tenant_id), type=EventType.IM_OUTBOUND, payload={"message_id": f"reminder-{delivery.id}", "tenant_id": str(reminder.tenant_id), "user_id": str(reminder.user_id), "conversation_id": str(reminder.conversation_id), "content": reminder.content, "reminder_id": str(reminder.id), "occurrence_at": occurrence.isoformat()})
            session.add(OutboxEvent(event_id=envelope.event_id, trace_id=envelope.trace_id, tenant_id=reminder.tenant_id, subject=EventType.IM_OUTBOUND, payload=envelope.model_dump(mode="json")))
            following = advance_occurrence(occurrence, reminder.repeat)
            if following is None:
                reminder.status = "completed"
            else:
                reminder.next_run_at = following
        await session.commit()
        return len(rows)


async def main() -> None:
    log.info("reminder worker started")
    nc = await nats_core.connect(); js = nc.jetstream(); await nats_core.ensure_stream(js)
    relay_task = asyncio.create_task(relay_loop(js, async_session))
    try:
        while True:
            try:
                await dispatch_due()
                async with async_session() as session:
                    await close_expired(session)
            except Exception:
                log.exception("reminder dispatch iteration failed")
            await asyncio.sleep(1)
    finally:
        relay_task.cancel(); await nc.close()


if __name__ == "__main__":
    asyncio.run(main())
