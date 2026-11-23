"""MySQL storage for personal MCP access tokens."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import secrets

import pymysql

from config import get_settings
from core.auth import AuthIdentity

TOKEN_PREFIX = "dak_"
_MAX_ACTIVE_TOKENS = 10
_VISIBLE_AFTER_INVALIDATION = timedelta(days=30)
_USE_INTERVAL = timedelta(minutes=1)
_FIELDS = "id, name, token_prefix, user_id, display_name, created_at, last_used_at, expires_at, revoked_at"
_ACTIVE = "revoked_at IS NULL AND (expires_at IS NULL OR expires_at > %s)"


class TokenLimitReached(ValueError):
    pass


def _connect():
    cfg = get_settings()
    return pymysql.connect(host=cfg.mysql_host, port=cfg.mysql_port, user=cfg.mysql_user,
                           password=cfg.mysql_password, database=cfg.session_mysql_database,
                           charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=False)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> datetime:
    # MySQL DATETIME stores UTC here at second precision.
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def _record(row: dict, now: datetime) -> dict:
    status = "active"
    if row["revoked_at"] is not None:
        status = "revoked"
    elif row["expires_at"] is not None and row["expires_at"] <= now:
        status = "expired"
    record = {**row, "status": status}
    for field in ("created_at", "last_used_at", "expires_at", "revoked_at"):
        if row[field] is not None:
            record[field] = row[field].isoformat(timespec="seconds") + "Z"
    return record


def create_token(identity: AuthIdentity, name: str, expires_in_days: int | None) -> tuple[dict, str]:
    cfg = get_settings()
    lock_key = _hash(f"dataagent:mcp-access-token:{cfg.session_mysql_database}:{identity.user_id}")
    with _connect() as connection, connection.cursor() as cursor:
        # The connection holds this per-user lock through commit and releases it on close.
        # This serializes even the first concurrent creations, when the user has no rows yet.
        cursor.execute("SELECT GET_LOCK(%s, 5) AS acquired", (lock_key,))
        if cursor.fetchone()["acquired"] != 1:
            raise TimeoutError("Could not lock MCP token creation")
        now = _now()
        cursor.execute(
            f"SELECT COUNT(*) AS total FROM da_mcp_access_token WHERE user_id = %s AND {_ACTIVE}",
            (identity.user_id, now),
        )
        if cursor.fetchone()["total"] >= _MAX_ACTIVE_TOKENS:
            raise TokenLimitReached
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        prefix = token[:len(TOKEN_PREFIX) + 6]
        expires_at = now + timedelta(days=expires_in_days) if expires_in_days is not None else None
        cursor.execute(
            """INSERT INTO da_mcp_access_token
               (token_hash, token_prefix, user_id, display_name, name, expires_at, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (_hash(token), prefix, identity.user_id, identity.display_name, name, expires_at, now),
        )
        cursor.execute(f"SELECT {_FIELDS} FROM da_mcp_access_token WHERE id = %s", (cursor.lastrowid,))
        item = _record(cursor.fetchone(), now)
        connection.commit()
    return item, token


def list_tokens(user_id: str | None) -> list[dict]:
    now = _now()
    cutoff = now - _VISIBLE_AFTER_INVALIDATION
    where = "(expires_at IS NULL OR expires_at >= %s) AND (revoked_at IS NULL OR revoked_at >= %s)"
    params = (cutoff, cutoff)
    if user_id is not None:
        where += " AND user_id = %s"
        params += (user_id,)
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {_FIELDS} FROM da_mcp_access_token WHERE {where} ORDER BY created_at DESC, id DESC",
            params,
        )
        return [_record(row, now) for row in cursor.fetchall()]


def revoke_token(token_id: int, identity: AuthIdentity) -> dict | None:
    where = "id = %s AND (%s OR user_id = %s)"
    params = (token_id, identity.is_admin, identity.user_id)
    now = _now()
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE da_mcp_access_token SET revoked_at = COALESCE(revoked_at, %s) WHERE {where}",
            (now, *params),
        )
        cursor.execute(f"SELECT {_FIELDS} FROM da_mcp_access_token WHERE {where}", params)
        row = cursor.fetchone()
        connection.commit()
    return _record(row, now) if row else None


def find_active_token(token: str) -> dict | None:
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"SELECT id, user_id, display_name, last_used_at FROM da_mcp_access_token "
            f"WHERE token_hash = %s AND {_ACTIVE}",
            (_hash(token), _now()),
        )
        return cursor.fetchone()


def record_use(token_id: int, last_used_at: datetime | None) -> None:
    now = _now()
    cutoff = now - _USE_INTERVAL
    if last_used_at is not None and last_used_at > cutoff:
        return
    with _connect() as connection, connection.cursor() as cursor:
        # Multiple requests can read the same timestamp; only the first writes.
        cursor.execute(
            f"UPDATE da_mcp_access_token SET last_used_at = %s WHERE id = %s AND {_ACTIVE} "
            "AND (last_used_at IS NULL OR last_used_at <= %s)",
            (now, token_id, now, cutoff),
        )
        connection.commit()
