from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.reminder_parser import parse_reminder_candidate


def test_parse_chinese_reminder_candidate_without_creating_anything():
    now = datetime(2026, 9, 24, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    candidate = parse_reminder_candidate(
        "明天晚上7点提醒我带数学作业，提前30分钟", now=now
    )

    assert candidate == {
        "content": "带数学作业",
        "run_at_local": "2026-09-25T19:00",
        "timezone": "Asia/Shanghai",
        "repeat": "once",
        "lead_time_minutes": 30,
        "needs_confirmation": True,
    }
