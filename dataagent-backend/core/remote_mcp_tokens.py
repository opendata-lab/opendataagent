"""MySQL storage for personal MCP access tokens (integer user_id FK -> da_user.id).

Tokens never expire and are not capped (user decision 2026-11-24): a user
revokes their own tokens, and disabling the account stops all of them.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import secrets

import pymysql

from config import get_settings

TOKEN_PREFIX = "dak_"
_VISIBLE_AFTER_REVOCATION = timedelta(days=30)
_USE_INTERVAL = timedelta(minutes=1)
_SELECT_FIELDS = "t.id, t.name, t.token_prefix, t.user_id, t.created_at, t.last_used_at, t.revoked_at"


def _connect():
    cfg = get_settings()
    return pymysql.connect(
        host=cfg.mysql_host,
        port=cfg.mysql_port,
        user=cfg.mysql_user,
        password=cfg.mysql_password,
        database=cfg.session_mysql_database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> datetime:
    # MySQL DATETIME stores UTC here at second precision.
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def _record(row: dict) -> dict:
    record = {
        **row,
        "user_id": int(row["user_id"]),
        "status": "revoked" if row["revoked_at"] is not None else "active",
    }
    for field in ("created_at", "last_used_at", "revoked_at"):
        if row[field] is not None:
            record[field] = row[field].isoformat(timespec="seconds") + "Z"
    return record


def create_token(user_id: int, name: str) -> tuple[dict, str]:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO da_mcp_access_token (token_hash, token_prefix, user_id, name, created_at)
               VALUES (%s, %s, %s, %s, %s)""",
            (_hash(token), token[:len(TOKEN_PREFIX) + 6], int(user_id), name, _now()),
        )
        cursor.execute(f"SELECT {_SELECT_FIELDS} FROM da_mcp_access_token t WHERE t.id = %s", (cursor.lastrowid,))
        item = _record(cursor.fetchone())
        connection.commit()
    return item, token


def list_tokens(user_id: int) -> list[dict]:
    """The user's tokens: active ones and those revoked in the last 30 days."""
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"""SELECT {_SELECT_FIELDS} FROM da_mcp_access_token t
                WHERE t.user_id = %s AND (t.revoked_at IS NULL OR t.revoked_at >= %s)
                ORDER BY t.created_at DESC, t.id DESC""",
            (int(user_id), _now() - _VISIBLE_AFTER_REVOCATION),
        )
        return [_record(row) for row in cursor.fetchall()]


def revoke_token(token_id: int, *, user_id: int) -> dict | None:
    """Revoke one of the user's own tokens."""
    params = (int(token_id), int(user_id))
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE da_mcp_access_token SET revoked_at = COALESCE(revoked_at, %s) WHERE id = %s AND user_id = %s",
            (_now(), *params),
        )
        cursor.execute(f"SELECT {_SELECT_FIELDS} FROM da_mcp_access_token t WHERE t.id = %s AND t.user_id = %s", params)
        row = cursor.fetchone()
        connection.commit()
    return _record(row) if row else None


def find_active_token(token: str) -> dict | None:
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, user_id, last_used_at FROM da_mcp_access_token WHERE token_hash = %s AND revoked_at IS NULL",
            (_hash(token),),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {"id": int(row["id"]), "user_id": int(row["user_id"]), "last_used_at": row.get("last_used_at")}


def record_use(token_id: int, last_used_at: datetime | None) -> None:
    now = _now()
    cutoff = now - _USE_INTERVAL
    if last_used_at is not None and last_used_at > cutoff:
        return
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE da_mcp_access_token SET last_used_at = %s WHERE id = %s AND revoked_at IS NULL "
            "AND (last_used_at IS NULL OR last_used_at <= %s)",
            (now, int(token_id), cutoff),
        )
        connection.commit()
