"""提醒时间规则。"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Reminder


SUPPORTED_REPEATS = {"once", "daily", "weekly", "weekdays"}


def advance_occurrence(current: datetime, repeat: str) -> datetime | None:
    if repeat == "once":
        return None
    if repeat == "daily":
        return current + timedelta(days=1)
    if repeat == "weekly":
        return current + timedelta(days=7)
    if repeat == "weekdays":
        candidate = current + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate
    raise ValueError("unsupported repeat rule")


def compute_next_run(
    *, run_at_local: str, timezone_name: str, repeat: str, now: datetime
) -> datetime:
    if repeat not in SUPPORTED_REPEATS:
        raise ValueError("unsupported repeat rule")
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("unknown timezone") from exc
    local = datetime.fromisoformat(run_at_local)
    if local.tzinfo is None:
        local = local.replace(tzinfo=zone)
    else:
        local = local.astimezone(zone)
    result = local.astimezone(timezone.utc)
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if result <= current:
        raise ValueError("reminder must be in the future")
    return result


async def list_owned(session: AsyncSession, tenant_id, user_id) -> list[Reminder]:
    return list((await session.scalars(select(Reminder).where(Reminder.tenant_id == tenant_id, Reminder.user_id == user_id).order_by(Reminder.next_run_at))).all())
