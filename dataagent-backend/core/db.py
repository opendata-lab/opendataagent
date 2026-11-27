"""Single PyMySQL connection entrypoint pinned to UTC session timezone.

Every connection enforces the aware-datetime boundary:
- Writes: aware ``datetime`` values are converted to UTC; naive ``datetime`` raises ``TypeError``.
- Reads: ``DATETIME`` and ``TIMESTAMP`` columns are returned as UTC-aware ``datetime`` objects.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pymysql
from pymysql.constants import FIELD_TYPE
from pymysql.converters import conversions, convert_datetime, escape_datetime

from config import get_settings


def _escape_aware_datetime(val: datetime, mapping=None) -> str:
    if val.tzinfo is None or val.utcoffset() is None:
        raise TypeError(f"Naive datetime is not allowed for MySQL writes: {val!r}")
    utc_val = val.astimezone(UTC).replace(tzinfo=None)
    return escape_datetime(utc_val, mapping)


def _convert_utc_datetime(obj: Any) -> Any:
    parsed = convert_datetime(obj)
    if isinstance(parsed, datetime) and parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _build_utc_conversions() -> dict[Any, Any]:
    conv = conversions.copy()
    conv[datetime] = _escape_aware_datetime
    conv[FIELD_TYPE.DATETIME] = _convert_utc_datetime
    conv[FIELD_TYPE.TIMESTAMP] = _convert_utc_datetime
    return conv


_UTC_CONVERSIONS = _build_utc_conversions()


def connect_mysql(
    *,
    database: str | None = None,
    autocommit: bool = False,
    io_timeout_seconds: int | None = None,
):
    cfg = get_settings()
    timeouts: dict[str, Any] = (
        {
            "connect_timeout": io_timeout_seconds,
            "read_timeout": io_timeout_seconds,
            "write_timeout": io_timeout_seconds,
        }
        if io_timeout_seconds
        else {}
    )
    return pymysql.connect(
        host=cfg.mysql_host,
        port=int(cfg.mysql_port or 3306),
        user=cfg.mysql_user,
        password=cfg.mysql_password,
        database=cfg.session_mysql_database if database is None else database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=autocommit,
        conv=_UTC_CONVERSIONS,
        init_command="SET time_zone = '+00:00'",
        **timeouts,
    )
