"""提醒时间规则。"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Reminder


SUPPORTED_REPEATS = {"once", "daily", "weekly", "weekdays"}


def advance_occurrence(
    current: datetime, repeat: str, *, timezone_name: str = "UTC"
) -> datetime | None:
    if repeat == "once":
        return None
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("unknown timezone") from exc
    local = current.astimezone(zone)
    if repeat == "daily":
        candidate = local + timedelta(days=1)
    elif repeat == "weekly":
        candidate = local + timedelta(days=7)
    elif repeat == "weekdays":
        candidate = local + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
    else:
        raise ValueError("unsupported repeat rule")
    return candidate.astimezone(timezone.utc)


def compute_next_run(
    *, run_at_local: str, timezone_name: str, repeat: str, now: datetime,
    lead_time_minutes: int = 0,
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
    if not 0 <= lead_time_minutes <= 10080:
        raise ValueError("lead time must be between 0 and 10080 minutes")
    result = local.astimezone(timezone.utc) - timedelta(minutes=lead_time_minutes)
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if result <= current:
        raise ValueError("reminder must be in the future")
    return result


def advance_to_future(
    current: datetime, repeat: str, now: datetime, *, timezone_name: str = "UTC"
) -> datetime | None:
    """Advance a recurring occurrence past now, skipping stale catch-up bursts."""
    following = advance_occurrence(current, repeat, timezone_name=timezone_name)
    while following is not None and following <= now:
        following = advance_occurrence(following, repeat, timezone_name=timezone_name)
    return following


async def list_owned(session: AsyncSession, tenant_id, user_id) -> list[Reminder]:
    return list((await session.scalars(select(Reminder).where(Reminder.tenant_id == tenant_id, Reminder.user_id == user_id).order_by(Reminder.next_run_at))).all())
