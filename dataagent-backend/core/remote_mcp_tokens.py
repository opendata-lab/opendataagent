"""MySQL storage for personal MCP access tokens (integer user_id FK -> da_user.id)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import secrets

import pymysql

from config import get_settings

TOKEN_PREFIX = "dak_"
_MAX_ACTIVE_TOKENS = 10
_VISIBLE_AFTER_INVALIDATION = timedelta(days=30)
_USE_INTERVAL = timedelta(minutes=1)
_SELECT_FIELDS = (
    "t.id, t.name, t.token_prefix, t.user_id, u.username, u.display_name, "
    "t.created_at, t.last_used_at, t.expires_at, t.revoked_at"
)
_ACTIVE = "t.revoked_at IS NULL AND (t.expires_at IS NULL OR t.expires_at > %s)"
_ACTIVE_RAW = "revoked_at IS NULL AND (expires_at IS NULL OR expires_at > %s)"


class TokenLimitReached(ValueError):
    pass


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


def _record(row: dict, now: datetime) -> dict:
    status = "active"
    if row["revoked_at"] is not None:
        status = "revoked"
    elif row["expires_at"] is not None and row["expires_at"] <= now:
        status = "expired"
    record = {
        **row,
        "user_id": int(row["user_id"]),
        "username": str(row.get("username") or ""),
        "display_name": str(row.get("display_name") or row.get("username") or ""),
        "status": status,
    }
    for field in ("created_at", "last_used_at", "expires_at", "revoked_at"):
        if row[field] is not None:
            record[field] = row[field].isoformat(timespec="seconds") + "Z"
    return record


def create_token(user_id: int, name: str, expires_in_days: int | None) -> tuple[dict, str]:
    uid = int(user_id)
    cfg = get_settings()
    lock_key = _hash(f"dataagent:mcp-access-token:{cfg.session_mysql_database}:{uid}")
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK(%s, 5) AS acquired", (lock_key,))
        if cursor.fetchone()["acquired"] != 1:
            raise TimeoutError("Could not lock MCP token creation")
        now = _now()
        cursor.execute(
            f"SELECT COUNT(*) AS total FROM da_mcp_access_token WHERE user_id = %s AND {_ACTIVE_RAW}",
            (uid, now),
        )
        if cursor.fetchone()["total"] >= _MAX_ACTIVE_TOKENS:
            raise TokenLimitReached
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        prefix = token[:len(TOKEN_PREFIX) + 6]
        expires_at = now + timedelta(days=expires_in_days) if expires_in_days is not None else None
        cursor.execute(
            """INSERT INTO da_mcp_access_token
               (token_hash, token_prefix, user_id, name, expires_at, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (_hash(token), prefix, uid, name, expires_at, now),
        )
        cursor.execute(
            f"""SELECT {_SELECT_FIELDS}
                FROM da_mcp_access_token t
                LEFT JOIN da_user u ON u.id = t.user_id
                WHERE t.id = %s""",
            (cursor.lastrowid,),
        )
        item = _record(cursor.fetchone(), now)
        connection.commit()
    return item, token


def list_tokens(user_id: int | None) -> list[dict]:
    now = _now()
    cutoff = now - _VISIBLE_AFTER_INVALIDATION
    where = "(t.expires_at IS NULL OR t.expires_at >= %s) AND (t.revoked_at IS NULL OR t.revoked_at >= %s)"
    params: tuple = (cutoff, cutoff)
    if user_id is not None:
        where += " AND t.user_id = %s"
        params += (int(user_id),)
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"""SELECT {_SELECT_FIELDS}
                FROM da_mcp_access_token t
                LEFT JOIN da_user u ON u.id = t.user_id
                WHERE {where}
                ORDER BY t.created_at DESC, t.id DESC""",
            params,
        )
        return [_record(row, now) for row in cursor.fetchall()]


def revoke_token(token_id: int, *, user_id: int, can_manage_all: bool = False) -> dict | None:
    uid = int(user_id)
    where_raw = "id = %s AND (%s OR user_id = %s)"
    where_join = "t.id = %s AND (%s OR t.user_id = %s)"
    params = (int(token_id), bool(can_manage_all), uid)
    now = _now()
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE da_mcp_access_token SET revoked_at = COALESCE(revoked_at, %s) WHERE {where_raw}",
            (now, *params),
        )
        cursor.execute(
            f"""SELECT {_SELECT_FIELDS}
                FROM da_mcp_access_token t
                LEFT JOIN da_user u ON u.id = t.user_id
                WHERE {where_join}""",
            params,
        )
        row = cursor.fetchone()
        connection.commit()
    return _record(row, now) if row else None


def find_active_token(token: str) -> dict | None:
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"SELECT id, user_id, last_used_at FROM da_mcp_access_token "
            f"WHERE token_hash = %s AND {_ACTIVE_RAW}",
            (_hash(token), _now()),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "user_id": int(row["user_id"]),
            "last_used_at": row.get("last_used_at"),
        }


def record_use(token_id: int, last_used_at: datetime | None) -> None:
    now = _now()
    cutoff = now - _USE_INTERVAL
    if last_used_at is not None and last_used_at > cutoff:
        return
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE da_mcp_access_token SET last_used_at = %s WHERE id = %s AND {_ACTIVE_RAW} "
            "AND (last_used_at IS NULL OR last_used_at <= %s)",
            (now, int(token_id), now, cutoff),
        )
        connection.commit()
