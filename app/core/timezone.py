from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")
DIARY_DAY_START_HOUR = 6


def now_shanghai() -> datetime:
    return datetime.now(ASIA_SHANGHAI)


def diary_date_for_datetime(value: datetime) -> date:
    local_value = ensure_shanghai_tz(value)
    return (local_value - timedelta(hours=DIARY_DAY_START_HOUR)).date()


def diary_today_shanghai() -> date:
    return diary_date_for_datetime(now_shanghai())


def today_shanghai() -> date:
    return diary_today_shanghai()


def ensure_shanghai_tz(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=ASIA_SHANGHAI)
    return value.astimezone(ASIA_SHANGHAI)
