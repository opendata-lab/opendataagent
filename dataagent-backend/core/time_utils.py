"""Unified timezone-aware time helpers.

All datetimes inside backend code are timezone-aware. Conversion to/from MySQL
storage happens solely at the ``core.db.connect_mysql`` boundary.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from config import get_settings


def utc_now() -> datetime:
    """Current instant as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def get_business_tz() -> ZoneInfo:
    """Configured deployment business timezone (IANA ZoneInfo)."""
    return ZoneInfo(get_settings().dataagent_timezone)


def business_day_bounds(day: date) -> tuple[datetime, datetime]:
    """Return aware ``[start, end)`` bounds for one calendar day in business timezone."""
    tz = get_business_tz()
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return start, end


def to_rfc3339(value: datetime | None) -> str:
    """Serialize an aware datetime as ``YYYY-MM-DDTHH:MM:SSZ``.

    Returns ``""`` for ``None`` and raises ``ValueError`` for naive datetimes.
    """
    if value is None:
        return ""
    if not isinstance(value, datetime):
        raise TypeError(f"to_rfc3339 expects datetime or None, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"Naive datetime is not allowed in to_rfc3339: {value!r}")
    return value.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
