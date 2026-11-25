"""One-time cutover data migration helpers for unified user accounts.

Invoked by Alembic revision ``20261123_000042`` and directly testable against
throwaway MySQL schemas.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
from typing import Any

import sqlalchemy as sa

logger = logging.getLogger(__name__)

AUTH_CONFIG_ENV = "DATAAGENT_CONFIG"
LOCAL_PROVIDER = "local"


def _load_cutover_config(config_path: str | None = None) -> dict[str, Any]:
    """Read legacy LOCAL_ADMINS / ADMIN_USERS / DEFAULT_USER_PASSWORD for cutover import only."""
    path = (config_path if config_path is not None else os.environ.get(AUTH_CONFIG_ENV, "")).strip()
    if not path or not os.path.isfile(path) or not os.access(path, os.R_OK):
        return {
            "local_admins": [],
            "admin_users": [],
            "default_user_password": os.environ.get("DATAAGENT_DEFAULT_USER_PASSWORD", "").strip(),
        }

    config_dir = os.path.dirname(os.path.abspath(path))
    if config_dir not in sys.path:
        sys.path.insert(0, config_dir)

    spec = importlib.util.spec_from_file_location("dataagent_cutover_config", path)
    if spec is None or spec.loader is None:
        return {"local_admins": [], "admin_users": [], "default_user_password": ""}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    raw_local = getattr(module, "LOCAL_ADMINS", None) or []
    local_admins: list[dict[str, str]] = []
    if isinstance(raw_local, (list, tuple)):
        for item in raw_local:
            if not isinstance(item, dict):
                continue
            username = str(item.get("username") or "").strip()
            password_bcrypt = str(item.get("password_bcrypt") or "").strip()
            if username and password_bcrypt.startswith("$2"):
                local_admins.append({"username": username, "password_bcrypt": password_bcrypt})

    raw_admins = getattr(module, "ADMIN_USERS", None) or []
    admin_users = [str(x).strip() for x in raw_admins if str(x or "").strip()] if isinstance(raw_admins, (list, tuple)) else []
    default_pw = str(
        getattr(module, "DEFAULT_USER_PASSWORD", "") or os.environ.get("DATAAGENT_DEFAULT_USER_PASSWORD", "") or ""
    ).strip()
    return {
        "local_admins": local_admins,
        "admin_users": admin_users,
        "default_user_password": default_pw,
    }


def _ensure_builtin_roles(conn: sa.Connection) -> tuple[int, int]:
    conn.execute(
        sa.text(
            """
            INSERT IGNORE INTO da_role (role_key, name, description, is_builtin)
            VALUES
              ('admin', '管理员', '系统管理员，拥有全部管理菜单与助手访问权限', 1),
              ('user', '普通用户', '默认用户角色，可访问授权给本人或所属角色的助手', 1)
            """
        )
    )
    rows = conn.execute(
        sa.text("SELECT id, role_key FROM da_role WHERE role_key IN ('admin', 'user')")
    ).mappings().all()
    by_key = {str(r["role_key"]): int(r["id"]) for r in rows}
    return by_key["admin"], by_key["user"]


def _allocate_unique_username(conn: sa.Connection, base: str, source: str) -> str:
    candidate = base.strip()[:120] or f"{source}_user"
    existing = conn.execute(
        sa.text("SELECT id FROM da_user WHERE username = :u LIMIT 1"),
        {"u": candidate},
    ).fetchone()
    if not existing:
        return candidate
    prefixed = f"{source}_{candidate}"[:120]
    existing = conn.execute(
        sa.text("SELECT id FROM da_user WHERE username = :u LIMIT 1"),
        {"u": prefixed},
    ).fetchone()
    if not existing:
        return prefixed
    idx = 2
    while True:
        numbered = f"{prefixed[:110]}_{idx}"
        if not conn.execute(sa.text("SELECT id FROM da_user WHERE username = :u LIMIT 1"), {"u": numbered}).fetchone():
            return numbered
        idx += 1


def _import_cutover_admins(
    conn: sa.Connection,
    *,
    admin_role_id: int,
    config: dict[str, Any],
) -> dict[str, Any]:
    imported_local: list[str] = []
    imported_oauth_admins: list[str] = []

    for item in config.get("local_admins") or []:
        username = item["username"]
        password_bcrypt = item["password_bcrypt"]
        row = conn.execute(
            sa.text("SELECT id FROM da_user WHERE username = :u LIMIT 1"),
            {"u": username},
        ).fetchone()
        if row:
            user_id = int(row[0])
        else:
            res = conn.execute(
                sa.text(
                    """
                    INSERT INTO da_user (username, display_name, password_hash, is_active)
                    VALUES (:u, :d, :pw, 1)
                    """
                ),
                {"u": username, "d": username, "pw": password_bcrypt},
            )
            user_id = int(res.lastrowid)
        conn.execute(
            sa.text(
                """
                INSERT IGNORE INTO da_user_identity (source, subject, user_id)
                VALUES (:src, :sub, :uid)
                """
            ),
            {"src": LOCAL_PROVIDER, "sub": username, "uid": user_id},
        )
        conn.execute(
            sa.text(
                """
                INSERT IGNORE INTO da_user_role (user_id, role_id)
                VALUES (:uid, :rid)
                """
            ),
            {"uid": user_id, "rid": admin_role_id},
        )
        imported_local.append(username)

    for entry in config.get("admin_users") or []:
        source, sep, subject = entry.partition(":")
        source = source.strip()
        subject = subject.strip()
        if not sep or not source or not subject:
            continue
        ident = conn.execute(
            sa.text(
                "SELECT user_id FROM da_user_identity WHERE source = :src AND subject = :sub LIMIT 1"
            ),
            {"src": source, "sub": subject},
        ).fetchone()
        if ident:
            user_id = int(ident[0])
        else:
            username = _allocate_unique_username(conn, subject, source)
            # Imported OAuth administrators keep OAuth-only login: the shared
            # default password is for ordinary users only.
            pw_hash = ""
            res = conn.execute(
                sa.text(
                    """
                    INSERT INTO da_user (username, display_name, password_hash, is_active)
                    VALUES (:u, :d, :pw, 1)
                    """
                ),
                {"u": username, "d": subject, "pw": pw_hash},
            )
            user_id = int(res.lastrowid)
            conn.execute(
                sa.text(
                    """
                    INSERT IGNORE INTO da_user_identity (source, subject, user_id)
                    VALUES (:src, :sub, :uid)
                    """
                ),
                {"src": source, "sub": subject, "uid": user_id},
            )
        conn.execute(
            sa.text(
                "INSERT IGNORE INTO da_user_role (user_id, role_id) VALUES (:uid, :rid)"
            ),
            {"uid": user_id, "rid": admin_role_id},
        )
        imported_oauth_admins.append(entry)

    return {
        "imported_local_admins": imported_local,
        "imported_admin_users": imported_oauth_admins,
    }


def _import_legacy_principals(
    conn: sa.Connection,
    *,
    user_role_id: int,
) -> list[str]:
    """Register every legacy principal as an ordinary account before backfill.

    Old topics and published allowlists name users as ``<source>:<subject>``
    (``local:<u>`` or ``<provider>:<sub>``), the exact identity a later login
    looks up. Creating that binding now is what the user's first login would
    do, so their old topics and assistant grants carry over instead of being
    stranded as admin-only rows. The accounts get no password: the owner
    signs in through the same external identity (or an admin resets the
    password), so nobody can use the shared default before them.
    """
    principals: dict[str, str] = {}
    inspector = sa.inspect(conn)
    if inspector.has_table("da_agent_topic"):
        for row in conn.execute(
            sa.text(
                """
                SELECT CAST(auth_user_id AS BINARY) AS auth_user_id, MAX(auth_username) AS auth_username
                FROM da_agent_topic
                WHERE auth_user_id IS NOT NULL AND auth_user_id <> ''
                GROUP BY CAST(auth_user_id AS BINARY)
                """
            )
        ).mappings():
            raw_id = row["auth_user_id"]
            key = raw_id.decode("utf-8") if isinstance(raw_id, (bytes, bytearray)) else str(raw_id)
            principals[key] = str(row.get("auth_username") or "")
    if inspector.has_table("da_agent_profile"):
        for row in conn.execute(sa.text("SELECT visibility_json FROM da_agent_profile")).mappings():
            for entry in _clean_visibility(row.get("visibility_json"))[1]:
                principals.setdefault(entry, "")

    imported: list[str] = []
    for entry, legacy_name in sorted(principals.items()):
        source, sep, subject = entry.partition(":")
        if not sep or not source or not subject:
            continue
        exists = conn.execute(
            sa.text(
                """
                SELECT 1 FROM da_user_identity
                WHERE source = :src
                  AND subject = :sub
                LIMIT 1
                """
            ),
            {"src": source, "sub": subject},
        ).fetchone()
        if exists:
            continue
        name = legacy_name.strip() or subject
        res = conn.execute(
            sa.text(
                """
                INSERT INTO da_user (username, display_name, password_hash, is_active)
                VALUES (:u, :d, :pw, 1)
                """
            ),
            {
                "u": _allocate_unique_username(conn, name, source),
                "d": name[:255],
                "pw": "",
            },
        )
        user_id = int(res.lastrowid)
        conn.execute(
            sa.text("INSERT INTO da_user_identity (source, subject, user_id) VALUES (:src, :sub, :uid)"),
            {"src": source, "sub": subject, "uid": user_id},
        )
        conn.execute(
            sa.text("INSERT IGNORE INTO da_user_role (user_id, role_id) VALUES (:uid, :rid)"),
            {"uid": user_id, "rid": user_role_id},
        )
        imported.append(entry)
    return imported


def _backfill_topic_user_ids(conn: sa.Connection) -> int:
    """Backfill da_agent_topic.user_id where auth_user_id maps exactly through da_user_identity.

    Unmapped rows stay NULL (admin-only). Binary comparison prevents case-folding collisions.
    """
    res = conn.execute(
        sa.text(
            """
            UPDATE da_agent_topic t
            JOIN da_user_identity i
              ON CAST(t.auth_user_id AS BINARY) = CAST(CONCAT(i.source, ':', i.subject) AS BINARY)
            SET t.user_id = i.user_id
            WHERE t.user_id IS NULL
              AND t.auth_user_id IS NOT NULL
              AND t.auth_user_id <> ''
            """
        )
    )
    return int(res.rowcount or 0)


def _parse_json_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            return {}
    return {}


def _clean_visibility(raw_vis: Any) -> tuple[dict[str, str], list[str]]:
    vis = _parse_json_dict(raw_vis)
    mode = str(vis.get("mode") or "all").strip().lower()
    if mode not in {"all", "authenticated", "selected"}:
        mode = "all"
    raw_allowed = vis.get("allowed_users")
    allowed: list[str] = []
    if isinstance(raw_allowed, list):
        for item in raw_allowed:
            text = str(item or "").strip()
            if text and text not in allowed:
                allowed.append(text)
    return {"mode": mode}, allowed


def _migrate_agent_grants_and_visibility(conn: sa.Connection) -> dict[str, Any]:
    """Import published visibility.allowed_users into da_agent_grant; report draft diffs & unmapped."""
    ident_rows = conn.execute(
        sa.text("SELECT source, subject, user_id FROM da_user_identity")
    ).mappings().all()
    identity_map: dict[str, int] = {
        f"{r['source']}:{r['subject']}": int(r["user_id"]) for r in ident_rows
    }

    migrated_grants = 0
    unmapped_entries: list[dict[str, str]] = []
    draft_diffs: list[dict[str, Any]] = []
    published_allowed_by_agent: dict[str, list[str]] = {}

    inspector = sa.inspect(conn)
    if inspector.has_table("da_agent_profile"):
        profiles = conn.execute(
            sa.text("SELECT agent_id, visibility_json FROM da_agent_profile")
        ).mappings().all()
        for row in profiles:
            agent_id = str(row["agent_id"])
            cleaned_vis, allowed_users = _clean_visibility(row.get("visibility_json"))
            published_allowed_by_agent[agent_id] = allowed_users
            for entry in allowed_users:
                uid = identity_map.get(entry)
                if uid is not None:
                    res = conn.execute(
                        sa.text(
                            """
                            INSERT IGNORE INTO da_agent_grant (agent_id, principal_type, principal_id)
                            VALUES (:aid, 'user', :uid)
                            """
                        ),
                        {"aid": agent_id, "uid": uid},
                    )
                    migrated_grants += int(res.rowcount or 0)
                else:
                    unmapped_entries.append({"agent_id": agent_id, "entry": entry})
            conn.execute(
                sa.text(
                    "UPDATE da_agent_profile SET visibility_json = :vis WHERE agent_id = :aid"
                ),
                {"vis": json.dumps(cleaned_vis, ensure_ascii=False, sort_keys=True), "aid": agent_id},
            )

    if inspector.has_table("da_agent_draft"):
        drafts = conn.execute(
            sa.text("SELECT agent_id, draft_json, published_json FROM da_agent_draft")
        ).mappings().all()
        for row in drafts:
            agent_id = str(row["agent_id"])
            draft_obj = _parse_json_dict(row.get("draft_json"))
            pub_obj = _parse_json_dict(row.get("published_json")) if row.get("published_json") else None

            draft_vis_clean, draft_allowed = _clean_visibility(draft_obj.get("visibility"))
            pub_allowed = published_allowed_by_agent.get(agent_id, [])
            if pub_obj is not None:
                pub_vis_clean, pub_allowed_snap = _clean_visibility(pub_obj.get("visibility"))
                if not pub_allowed:
                    pub_allowed = pub_allowed_snap
                pub_obj["visibility"] = pub_vis_clean

            draft_only = [u for u in draft_allowed if u not in pub_allowed]
            removed_in_draft = [u for u in pub_allowed if u not in draft_allowed]
            if draft_only or removed_in_draft:
                draft_diffs.append(
                    {
                        "agent_id": agent_id,
                        "draft_only_allowed_users": draft_only,
                        "removed_in_draft_allowed_users": removed_in_draft,
                    }
                )

            draft_obj["visibility"] = draft_vis_clean
            conn.execute(
                sa.text(
                    """
                    UPDATE da_agent_draft
                    SET draft_json = :draft_json, published_json = :published_json
                    WHERE agent_id = :aid
                    """
                ),
                {
                    "draft_json": json.dumps(draft_obj, ensure_ascii=False, sort_keys=True),
                    "published_json": (
                        json.dumps(pub_obj, ensure_ascii=False, sort_keys=True) if pub_obj is not None else None
                    ),
                    "aid": agent_id,
                },
            )

    return {
        "migrated_grants": migrated_grants,
        "unmapped_entries": unmapped_entries,
        "draft_diffs": draft_diffs,
    }


def run_accounts_data_migration(conn: sa.Connection, *, config_path: str | None = None) -> dict[str, Any]:
    """Execute the full one-time accounts cutover data migration on an open connection."""
    admin_role_id, user_role_id = _ensure_builtin_roles(conn)
    config = _load_cutover_config(config_path)
    admin_report = _import_cutover_admins(conn, admin_role_id=admin_role_id, config=config)
    imported_users = _import_legacy_principals(conn, user_role_id=user_role_id)
    backfilled_topics = _backfill_topic_user_ids(conn)
    grant_report = _migrate_agent_grants_and_visibility(conn)
    report = {
        **admin_report,
        "imported_legacy_users": imported_users,
        "backfilled_topics": backfilled_topics,
        **grant_report,
    }
    logger.info("Unified accounts migration summary: %s", json.dumps(report, ensure_ascii=False))
    return report
