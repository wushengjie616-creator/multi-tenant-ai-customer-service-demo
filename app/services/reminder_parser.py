"""将常见中文提醒表达解析为待确认候选。

本模块只解析，不写数据库；真正创建仍必须调用 POST /reminders。
"""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def parse_reminder_candidate(
    text: str, *, timezone_name: str = "Asia/Shanghai", now: datetime | None = None
) -> dict:
    zone = ZoneInfo(timezone_name)
    current = (now or datetime.now(zone)).astimezone(zone)
    normalized = " ".join(text.strip().split())

    day_offset = 2 if "后天" in normalized else 1 if "明天" in normalized else 0
    date_match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?", normalized)
    if date_match:
        year, month, day = map(int, date_match.groups())
        target_date = current.replace(year=year, month=month, day=day).date()
    else:
        target_date = (current + timedelta(days=day_offset)).date()

    time_match = re.search(
        r"(?:(凌晨|早上|上午|中午|下午|晚上))?\s*(\d{1,2})(?:[:：点时](\d{1,2})?分?)?",
        normalized,
    )
    if not time_match:
        raise ValueError("请补充明确时间，例如“明天晚上 7 点”。")
    period, hour_text, minute_text = time_match.groups()
    hour = int(hour_text)
    minute = int(minute_text or 0)
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    elif period == "中午" and hour < 11:
        hour += 12
    elif period == "凌晨" and hour == 12:
        hour = 0
    try:
        target = datetime(
            target_date.year, target_date.month, target_date.day, hour, minute,
            tzinfo=zone,
        )
    except ValueError as exc:
        raise ValueError("提醒时间无效，请检查小时和分钟。") from exc
    if target <= current:
        raise ValueError("提醒时间必须在未来。")

    lead = 0
    lead_match = re.search(r"提前\s*(半|一|\d+)\s*(分钟|小时|天)", normalized)
    if lead_match:
        raw, unit = lead_match.groups()
        amount = 0.5 if raw == "半" else 1 if raw == "一" else int(raw)
        multiplier = {"分钟": 1, "小时": 60, "天": 1440}[unit]
        lead = int(amount * multiplier)

    repeat = "once"
    if "每个工作日" in normalized or "工作日" in normalized:
        repeat = "weekdays"
    elif "每天" in normalized:
        repeat = "daily"
    elif "每周" in normalized or "每星期" in normalized:
        repeat = "weekly"

    content = normalized
    content = re.sub(r"提前\s*(?:半|一|\d+)\s*(?:分钟|小时|天)", "", content)
    content = content.replace("，", " ").replace(",", " ")
    content = re.sub(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?", "", content)
    content = re.sub(r"(?:今天|明天|后天)?\s*(?:凌晨|早上|上午|中午|下午|晚上)?\s*\d{1,2}(?:[:：点时]\d{0,2}分?)?", "", content)
    content = re.sub(r"(?:请)?提醒我|(?:帮我)?提醒|(?:创建|设置)(?:一个)?提醒", "", content)
    content = re.sub(r"(?:每天|每周|每星期|每个工作日|工作日)", "", content)
    content = " ".join(content.split()).strip("。，, ") or "日程提醒"

    return {
        "content": content,
        "run_at_local": target.strftime("%Y-%m-%dT%H:%M"),
        "timezone": timezone_name,
        "repeat": repeat,
        "lead_time_minutes": lead,
        "needs_confirmation": True,
    }
