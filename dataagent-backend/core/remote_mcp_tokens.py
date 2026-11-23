"""Storage for single-use MCP authorization codes and rotating refresh tokens."""
from __future__ import annotations

import hashlib
import json
import secrets

import pymysql
from redis.exceptions import RedisError

from config import get_settings
from core.remote_mcp_policy import CODE_TTL, RemoteMcpError
from core.task_coordinator import get_task_coordinator

_CODE_PREFIX = "dataagent:mcp:code:"
_REFRESH_TTL_DAYS = 30


def _redis():
    client = get_task_coordinator().redis_client
    if client is None:
        raise RemoteMcpError("temporarily_unavailable", "Authorization code storage is unavailable.")
    return client


async def create_code(grant: dict) -> str:
    code = secrets.token_urlsafe(32)
    try:
        created = await _redis().set(_CODE_PREFIX + code, json.dumps(grant), ex=CODE_TTL, nx=True)
    except RedisError as exc:
        raise RemoteMcpError("temporarily_unavailable", "Could not store the authorization code.") from exc
    if not created:
        raise RemoteMcpError("temporarily_unavailable", "Could not store the authorization code.")
    return code


async def consume_code(code: str) -> dict | None:
    try:
        raw = await _redis().getdel(_CODE_PREFIX + code)
    except RedisError as exc:
        raise RemoteMcpError("temporarily_unavailable", "Could not read the authorization code.") from exc
    return json.loads(raw) if raw is not None else None


def _connect():
    cfg = get_settings()
    return pymysql.connect(host=cfg.mysql_host, port=cfg.mysql_port, user=cfg.mysql_user,
                           password=cfg.mysql_password, database=cfg.session_mysql_database,
                           charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=False)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _insert(cursor, token: str, grant: dict) -> None:
    cursor.execute(
        "INSERT INTO da_mcp_refresh_token "
        "(token_hash, user_id, display_name, client_id, scope, expires_at, created_at) "
        "VALUES (%s, %s, %s, %s, %s, UTC_TIMESTAMP() + INTERVAL %s DAY, UTC_TIMESTAMP())",
        (_hash(token), grant["user_id"], grant["display_name"], grant["client_id"], grant["scope"], _REFRESH_TTL_DAYS),
    )


def save_refresh_token(token: str, grant: dict) -> None:
    with _connect() as connection:
        with connection.cursor() as cursor:
            _insert(cursor, token, grant)
        connection.commit()


def rotate_refresh_token(token: str, client_id: str, replacement: str) -> dict | None:
    token_hash = _hash(token)
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT user_id, display_name, client_id, scope FROM da_mcp_refresh_token "
                "WHERE token_hash = %s AND expires_at > UTC_TIMESTAMP() FOR UPDATE", (token_hash,),
            )
            grant = cursor.fetchone()
            if grant is None or grant["client_id"] != client_id:
                return None
            cursor.execute("DELETE FROM da_mcp_refresh_token WHERE token_hash = %s", (token_hash,))
            _insert(cursor, replacement, grant)
        connection.commit()
    return grant
