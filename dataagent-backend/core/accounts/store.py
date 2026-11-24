"""MySQL persistence for unified user accounts, identities, roles, and agent grants."""
from __future__ import annotations

import logging
import re
import threading
from datetime import UTC, datetime
from typing import Any, Callable, Iterable, Sequence

import pymysql

from config import get_settings
from core.accounts.models import (
    AccountDisabledError,
    AccountNotFoundError,
    IdentityConflictError,
    IdentityRef,
    RoleRef,
    User,
)
from core.accounts.permissions import (
    BUILTIN_ROLE_KEYS,
    ROLE_ADMIN,
    ROLE_USER,
    menus_for_permissions,
    permissions_for_roles,
    role_permission_summary,
)

logger = logging.getLogger(__name__)

_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,127}$")
_ROLE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def _to_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds") + "Z"
    return str(value) if value is not None else ""


def validate_username(raw: Any) -> str:
    username = str(raw or "").strip()
    if not username or not _USERNAME_RE.fullmatch(username):
        raise ValueError("用户名需为 1-128 位英文、数字或 . _ @ + -，且以字母或数字开头")
    return username


def validate_role_key(raw: Any) -> str:
    role_key = str(raw or "").strip().lower()
    if not role_key or not _ROLE_KEY_RE.fullmatch(role_key):
        raise ValueError("角色标识需为 1-64 位小写字母、数字、_ 或 -，且以字母或数字开头")
    return role_key


class AccountStore:
    """Single MySQL store for da_user, da_user_identity, da_role, da_user_role, da_agent_grant."""

    def _connect(self):
        cfg = get_settings()
        return pymysql.connect(
            host=cfg.mysql_host,
            port=int(cfg.mysql_port or 3306),
            user=cfg.mysql_user,
            password=cfg.mysql_password,
            database=cfg.session_mysql_database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )

    def _hydrate_users(self, cur, rows: list[dict[str, Any]]) -> list[User]:
        if not rows:
            return []
        user_ids = [int(r["id"]) for r in rows]
        placeholders = ", ".join(["%s"] * len(user_ids))

        cur.execute(
            f"""
            SELECT ur.user_id, r.id, r.role_key, r.name, r.is_builtin
            FROM da_user_role ur
            JOIN da_role r ON r.id = ur.role_id
            WHERE ur.user_id IN ({placeholders})
            ORDER BY r.is_builtin DESC, r.id ASC
            """,
            user_ids,
        )
        roles_by_uid: dict[int, list[RoleRef]] = {uid: [] for uid in user_ids}
        for r in cur.fetchall() or []:
            roles_by_uid[int(r["user_id"])].append(
                RoleRef(
                    id=int(r["id"]),
                    role_key=str(r["role_key"]),
                    name=str(r["name"]),
                    is_builtin=bool(r["is_builtin"]),
                )
            )

        cur.execute(
            f"""
            SELECT id, user_id, source, subject, created_at
            FROM da_user_identity
            WHERE user_id IN ({placeholders})
            ORDER BY id ASC
            """,
            user_ids,
        )
        identities_by_uid: dict[int, list[IdentityRef]] = {uid: [] for uid in user_ids}
        for i in cur.fetchall() or []:
            identities_by_uid[int(i["user_id"])].append(
                IdentityRef(
                    id=int(i["id"]),
                    source=str(i["source"]),
                    subject=str(i["subject"]),
                    created_at=_to_iso(i.get("created_at")),
                )
            )

        users: list[User] = []
        for row in rows:
            uid = int(row["id"])
            roles = tuple(roles_by_uid.get(uid) or ())
            perms = permissions_for_roles(r.role_key for r in roles)
            menus = menus_for_permissions(perms)
            users.append(
                User(
                    id=uid,
                    username=str(row["username"]),
                    display_name=str(row.get("display_name") or "") or str(row["username"]),
                    email=str(row["email"]).strip() if row.get("email") else None,
                    email_verified=bool(row.get("email_verified")),
                    is_active=bool(row.get("is_active")),
                    roles=roles,
                    identities=tuple(identities_by_uid.get(uid) or ()),
                    permissions=perms,
                    menus=menus,
                    created_at=_to_iso(row.get("created_at")),
                    updated_at=_to_iso(row.get("updated_at")),
                    last_login_at=_to_iso(row.get("last_login_at")) or None,
                )
            )
        return users

    def _get_builtin_role_id(self, cur, role_key: str) -> int:
        cur.execute("SELECT id FROM da_role WHERE role_key = %s LIMIT 1", (role_key,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"Built-in role missing: {role_key}; run Alembic migrations first")
        return int(row["id"])

    def _count_active_admins(self, cur, *, exclude_user_id: int | None = None) -> int:
        sql = """
            SELECT COUNT(DISTINCT u.id) AS total
            FROM da_user u
            JOIN da_user_role ur ON ur.user_id = u.id
            JOIN da_role r ON r.id = ur.role_id
            WHERE u.is_active = 1 AND r.role_key = %s
        """
        params: list[Any] = [ROLE_ADMIN]
        if exclude_user_id is not None:
            sql += " AND u.id <> %s"
            params.append(exclude_user_id)
        cur.execute(sql, params)
        return int((cur.fetchone() or {}).get("total") or 0)

    def _validate_role_ids(self, cur, role_ids: Iterable[int]) -> list[int]:
        deduped = list(dict.fromkeys(int(rid) for rid in role_ids))
        if not deduped:
            return []
        placeholders = ", ".join(["%s"] * len(deduped))
        cur.execute(f"SELECT id FROM da_role WHERE id IN ({placeholders})", deduped)
        found = {int(r["id"]) for r in cur.fetchall() or []}
        missing = [rid for rid in deduped if rid not in found]
        if missing:
            raise ValueError(f"角色不存在: {missing[0]}")
        return deduped

    def _validate_user_ids(self, cur, user_ids: Iterable[int]) -> list[int]:
        deduped = list(dict.fromkeys(int(uid) for uid in user_ids))
        if not deduped:
            return []
        placeholders = ", ".join(["%s"] * len(deduped))
        cur.execute(f"SELECT id FROM da_user WHERE id IN ({placeholders})", deduped)
        found = {int(r["id"]) for r in cur.fetchall() or []}
        missing = [uid for uid in deduped if uid not in found]
        if missing:
            raise ValueError(f"用户不存在: {missing[0]}")
        return deduped

    def _validate_agent_exists(self, cur, agent_id: str) -> None:
        cur.execute("SELECT 1 FROM da_agent_draft WHERE agent_id = %s LIMIT 1", (agent_id,))
        if cur.fetchone():
            return
        cur.execute("SELECT 1 FROM da_agent_profile WHERE agent_id = %s LIMIT 1", (agent_id,))
        if cur.fetchone():
            return
        raise AccountNotFoundError(f"助手不存在: {agent_id}")

    # ---- Users -------------------------------------------------------------

    def get_user(self, user_id: int) -> User | None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM da_user WHERE id = %s LIMIT 1", (int(user_id),))
                row = cur.fetchone()
                if not row:
                    return None
                return self._hydrate_users(cur, [row])[0]
        finally:
            conn.close()

    def get_user_with_password(self, username: str) -> tuple[User, str] | None:
        clean = str(username or "").strip()
        if not clean:
            return None
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM da_user WHERE username = %s LIMIT 1", (clean,))
                row = cur.fetchone()
                if not row:
                    return None
                user = self._hydrate_users(cur, [row])[0]
                return user, str(row.get("password_hash") or "")
        finally:
            conn.close()

    def get_user_password_hash(self, user_id: int) -> str:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT password_hash FROM da_user WHERE id = %s LIMIT 1", (int(user_id),))
                row = cur.fetchone()
                return str((row or {}).get("password_hash") or "")
        finally:
            conn.close()

    def get_user_by_identity(self, source: str, subject: str) -> User | None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT u.*
                    FROM da_user_identity i
                    JOIN da_user u ON u.id = i.user_id
                    WHERE i.source = %s AND i.subject = %s
                    LIMIT 1
                    """,
                    (source, subject),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return self._hydrate_users(cur, [row])[0]
        finally:
            conn.close()

    def list_users(
        self,
        *,
        keyword: str = "",
        is_active: bool | None = None,
        role_id: int | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        where = ["1 = 1"]
        params: list[Any] = []
        clean_kw = str(keyword or "").strip()
        if clean_kw:
            where.append("(u.username LIKE %s OR u.display_name LIKE %s OR COALESCE(u.email, '') LIKE %s)")
            like = f"%{clean_kw}%"
            params.extend([like, like, like])
        if is_active is not None:
            where.append("u.is_active = %s")
            params.append(1 if is_active else 0)
        if role_id is not None:
            where.append("EXISTS (SELECT 1 FROM da_user_role ur WHERE ur.user_id = u.id AND ur.role_id = %s)")
            params.append(int(role_id))
        clause = " AND ".join(where)
        safe_page = max(1, int(page or 1))
        safe_size = max(1, min(200, int(page_size or 50)))
        offset = (safe_page - 1) * safe_size

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS total FROM da_user u WHERE {clause}", params)
                total = int((cur.fetchone() or {}).get("total") or 0)
                cur.execute(
                    f"""
                    SELECT u.*
                    FROM da_user u
                    WHERE {clause}
                    ORDER BY u.id ASC
                    LIMIT %s OFFSET %s
                    """,
                    [*params, safe_size, offset],
                )
                rows = cur.fetchall() or []
                users = self._hydrate_users(cur, rows)
        finally:
            conn.close()
        return {
            "items": [u.to_public_dict() for u in users],
            "total": total,
            "page": safe_page,
            "page_size": safe_size,
        }

    def create_user(
        self,
        *,
        username: str,
        display_name: str = "",
        password_hash: str,
        email: str | None = None,
        email_verified: bool = False,
        role_ids: Sequence[int] | None = None,
        role_keys: Sequence[str] | None = None,
        is_active: bool = True,
    ) -> User:
        clean_username = validate_username(username)
        clean_display = str(display_name or "").strip()[:255] or clean_username
        clean_email = str(email or "").strip()[:255] or None

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM da_user WHERE username = %s LIMIT 1", (clean_username,))
                if cur.fetchone():
                    raise ValueError(f"用户名已存在: {clean_username}")

                resolved_role_ids: list[int] = []
                if role_ids is not None:
                    resolved_role_ids = self._validate_role_ids(cur, role_ids)
                elif role_keys is not None:
                    for key in role_keys:
                        cur.execute("SELECT id FROM da_role WHERE role_key = %s LIMIT 1", (key,))
                        row = cur.fetchone()
                        if not row:
                            raise ValueError(f"角色不存在: {key}")
                        resolved_role_ids.append(int(row["id"]))
                else:
                    resolved_role_ids = [self._get_builtin_role_id(cur, ROLE_USER)]

                cur.execute(
                    """
                    INSERT INTO da_user (username, display_name, email, email_verified, password_hash, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        clean_username,
                        clean_display,
                        clean_email,
                        1 if (clean_email and email_verified) else 0,
                        password_hash,
                        1 if is_active else 0,
                    ),
                )
                uid = int(cur.lastrowid)
                for rid in resolved_role_ids:
                    cur.execute(
                        "INSERT IGNORE INTO da_user_role (user_id, role_id) VALUES (%s, %s)",
                        (uid, rid),
                    )
                cur.execute("SELECT * FROM da_user WHERE id = %s LIMIT 1", (uid,))
                user = self._hydrate_users(cur, [cur.fetchone()])[0]
            conn.commit()
            return user
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_user(
        self,
        user_id: int,
        *,
        display_name: str | None = None,
        email: Any = ...,
        email_verified: bool | None = None,
        is_active: bool | None = None,
        role_ids: Sequence[int] | None = None,
        acting_user_id: int | None = None,
    ) -> User:
        uid = int(user_id)
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM da_user WHERE id = %s FOR UPDATE", (uid,))
                row = cur.fetchone()
                if not row:
                    raise AccountNotFoundError("用户不存在")
                current = self._hydrate_users(cur, [row])[0]

                updates: list[str] = []
                params: list[Any] = []
                if display_name is not None:
                    clean_display = str(display_name).strip()[:255]
                    if not clean_display:
                        raise ValueError("显示名不能为空")
                    updates.append("display_name = %s")
                    params.append(clean_display)
                if email is not ...:
                    clean_email = str(email or "").strip()[:255] or None
                    updates.append("email = %s")
                    params.append(clean_email)
                    if email_verified is not None:
                        updates.append("email_verified = %s")
                        params.append(1 if (clean_email and email_verified) else 0)
                    elif clean_email != current.email:
                        updates.append("email_verified = 0")
                if is_active is not None:
                    if not is_active and acting_user_id is not None and acting_user_id == uid:
                        raise ValueError("不能停用当前登录账号")
                    if not is_active and current.is_admin and self._count_active_admins(cur, exclude_user_id=uid) == 0:
                        raise ValueError("至少需要保留一个启用的管理员账号")
                    updates.append("is_active = %s")
                    params.append(1 if is_active else 0)

                if role_ids is not None:
                    validated = self._validate_role_ids(cur, role_ids)
                    admin_role_id = self._get_builtin_role_id(cur, ROLE_ADMIN)
                    losing_admin = current.is_admin and admin_role_id not in validated
                    if losing_admin and self._count_active_admins(cur, exclude_user_id=uid) == 0:
                        raise ValueError("至少需要保留一个启用的管理员账号")
                    cur.execute("DELETE FROM da_user_role WHERE user_id = %s", (uid,))
                    for rid in validated:
                        cur.execute(
                            "INSERT IGNORE INTO da_user_role (user_id, role_id) VALUES (%s, %s)",
                            (uid, rid),
                        )

                if updates:
                    cur.execute(
                        f"UPDATE da_user SET {', '.join(updates)} WHERE id = %s",
                        [*params, uid],
                    )
                cur.execute("SELECT * FROM da_user WHERE id = %s LIMIT 1", (uid,))
                updated = self._hydrate_users(cur, [cur.fetchone()])[0]
            conn.commit()
            return updated
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_password(self, user_id: int, password_hash: str) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE da_user SET password_hash = %s WHERE id = %s",
                    (password_hash, int(user_id)),
                )
                if cur.rowcount == 0:
                    raise AccountNotFoundError("用户不存在")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def touch_last_login(self, user_id: int) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE da_user SET last_login_at = %s WHERE id = %s",
                    (_utc_now(), int(user_id)),
                )
            conn.commit()
        finally:
            conn.close()

    def add_identity(self, user_id: int, source: str, subject: str) -> IdentityRef:
        clean_source = str(source or "").strip()[:128]
        clean_subject = str(subject or "").strip()[:255]
        if not clean_source or not clean_subject:
            raise ValueError("source 和 subject 不能为空")
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM da_user WHERE id = %s LIMIT 1", (int(user_id),))
                if not cur.fetchone():
                    raise AccountNotFoundError("用户不存在")
                cur.execute(
                    "SELECT user_id FROM da_user_identity WHERE source = %s AND subject = %s LIMIT 1",
                    (clean_source, clean_subject),
                )
                existing = cur.fetchone()
                if existing:
                    if int(existing["user_id"]) == int(user_id):
                        raise ValueError("该外部身份已绑定到当前用户")
                    raise IdentityConflictError("该外部身份已绑定到其他用户")
                cur.execute(
                    "INSERT INTO da_user_identity (source, subject, user_id) VALUES (%s, %s, %s)",
                    (clean_source, clean_subject, int(user_id)),
                )
                ident_id = int(cur.lastrowid)
                cur.execute("SELECT * FROM da_user_identity WHERE id = %s LIMIT 1", (ident_id,))
                row = cur.fetchone()
            conn.commit()
            return IdentityRef(
                id=int(row["id"]),
                source=str(row["source"]),
                subject=str(row["subject"]),
                created_at=_to_iso(row.get("created_at")),
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def remove_identity(self, user_id: int, identity_id: int) -> bool:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM da_user_identity WHERE id = %s AND user_id = %s",
                    (int(identity_id), int(user_id)),
                )
                deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        finally:
            conn.close()

    def link_or_register(
        self,
        source: str,
        subject: str,
        profile: dict[str, Any] | None,
        *,
        default_password_hash_fn: Callable[[], str],
        trusted_username_namespace: bool = False,
    ) -> User:
        """Atomically look up, controlled-match, or register a platform user for (source, subject)."""
        clean_source = str(source or "").strip()[:128]
        clean_subject = str(subject or "").strip()[:255]
        if not clean_source or not clean_subject:
            raise ValueError("source and subject are required")

        prof = dict(profile or {})
        raw_username = str(prof.get("username") or prof.get("preferred_username") or clean_subject).strip()
        raw_display = str(prof.get("display_name") or prof.get("name") or "").strip()[:255]
        raw_email = str(prof.get("email") or "").strip()[:255] or None
        email_verified = bool(prof.get("email_verified")) and bool(raw_email)

        for attempt in range(2):
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    # 1. Exact binding lookup
                    cur.execute(
                        """
                        SELECT u.*
                        FROM da_user_identity i
                        JOIN da_user u ON u.id = i.user_id
                        WHERE i.source = %s AND i.subject = %s
                        LIMIT 1
                        """,
                        (clean_source, clean_subject),
                    )
                    row = cur.fetchone()
                    if row:
                        user = self._hydrate_users(cur, [row])[0]
                        if not user.is_active:
                            raise AccountDisabledError("账号已停用")
                        updates = ["last_login_at = %s"]
                        params: list[Any] = [_utc_now()]
                        # User-edited display_name always wins; only fill when empty.
                        if not str(row.get("display_name") or "").strip() and raw_display:
                            updates.append("display_name = %s")
                            params.append(raw_display)
                        # Only fill empty email when explicitly verified by the IdP.
                        if not row.get("email") and email_verified and raw_email:
                            updates.append("email = %s")
                            updates.append("email_verified = 1")
                            params.append(raw_email)
                        cur.execute(
                            f"UPDATE da_user SET {', '.join(updates)} WHERE id = %s",
                            [*params, user.id],
                        )
                        cur.execute("SELECT * FROM da_user WHERE id = %s LIMIT 1", (user.id,))
                        refreshed = self._hydrate_users(cur, [cur.fetchone()])[0]
                        conn.commit()
                        return refreshed

                    # 2. Unbound external identity: validate candidate username
                    candidate_username = validate_username(raw_username)
                    cur.execute(
                        "SELECT * FROM da_user WHERE username = %s FOR UPDATE",
                        (candidate_username,),
                    )
                    existing_row = cur.fetchone()
                    if existing_row:
                        existing_user = self._hydrate_users(cur, [existing_row])[0]
                        if not trusted_username_namespace:
                            raise IdentityConflictError(
                                f"用户名 {candidate_username!r} 已存在且未绑定外部身份，请联系管理员核对绑定"
                            )
                        if existing_user.is_admin:
                            raise IdentityConflictError(
                                f"管理员账号 {candidate_username!r} 需要由管理员手动绑定外部身份"
                            )
                        if not existing_user.is_active:
                            raise AccountDisabledError("账号已停用")
                        cur.execute(
                            "INSERT INTO da_user_identity (source, subject, user_id) VALUES (%s, %s, %s)",
                            (clean_source, clean_subject, existing_user.id),
                        )
                        updates = ["last_login_at = %s"]
                        params = [_utc_now()]
                        if not str(existing_row.get("display_name") or "").strip() and raw_display:
                            updates.append("display_name = %s")
                            params.append(raw_display)
                        if not existing_row.get("email") and email_verified and raw_email:
                            updates.append("email = %s")
                            updates.append("email_verified = 1")
                            params.append(raw_email)
                        cur.execute(
                            f"UPDATE da_user SET {', '.join(updates)} WHERE id = %s",
                            [*params, existing_user.id],
                        )
                        cur.execute("SELECT * FROM da_user WHERE id = %s LIMIT 1", (existing_user.id,))
                        bound = self._hydrate_users(cur, [cur.fetchone()])[0]
                        conn.commit()
                        return bound

                    # 3. First-time auto-registration (atomic user + identity + default 'user' role)
                    password_hash = default_password_hash_fn()
                    user_role_id = self._get_builtin_role_id(cur, ROLE_USER)
                    initial_display = raw_display or candidate_username
                    now = _utc_now()
                    cur.execute(
                        """
                        INSERT INTO da_user
                            (username, display_name, email, email_verified, password_hash, is_active, last_login_at)
                        VALUES (%s, %s, %s, %s, %s, 1, %s)
                        """,
                        (
                            candidate_username,
                            initial_display,
                            raw_email if email_verified else None,
                            1 if email_verified else 0,
                            password_hash,
                            now,
                        ),
                    )
                    uid = int(cur.lastrowid)
                    cur.execute(
                        "INSERT INTO da_user_identity (source, subject, user_id) VALUES (%s, %s, %s)",
                        (clean_source, clean_subject, uid),
                    )
                    cur.execute(
                        "INSERT INTO da_user_role (user_id, role_id) VALUES (%s, %s)",
                        (uid, user_role_id),
                    )
                    cur.execute("SELECT * FROM da_user WHERE id = %s LIMIT 1", (uid,))
                    created = self._hydrate_users(cur, [cur.fetchone()])[0]
                conn.commit()
                return created
            except pymysql.err.IntegrityError:
                conn.rollback()
                if attempt == 0:
                    # Concurrent first login for the same (source, subject) won the race; retry lookup.
                    existing = self.get_user_by_identity(clean_source, clean_subject)
                    if existing is not None:
                        if not existing.is_active:
                            raise AccountDisabledError("账号已停用")
                        self.touch_last_login(existing.id)
                        return existing
                    continue
                raise IdentityConflictError(f"用户名 {raw_username!r} 注册冲突，请重试或联系管理员")
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        raise IdentityConflictError("外部身份注册失败")

    # ---- Roles -------------------------------------------------------------

    def list_roles(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.*,
                           (SELECT COUNT(*) FROM da_user_role ur WHERE ur.role_id = r.id) AS user_count
                    FROM da_role r
                    ORDER BY r.is_builtin DESC, r.id ASC
                    """
                )
                rows = cur.fetchall() or []
                cur.execute(
                    "SELECT agent_id, principal_id FROM da_agent_grant WHERE principal_type = 'role' ORDER BY agent_id ASC"
                )
                grants_by_role: dict[int, list[str]] = {}
                for g in cur.fetchall() or []:
                    grants_by_role.setdefault(int(g["principal_id"]), []).append(str(g["agent_id"]))
        finally:
            conn.close()

        result: list[dict[str, Any]] = []
        for row in rows:
            rid = int(row["id"])
            role_key = str(row["role_key"])
            perm_summary = role_permission_summary(role_key)
            result.append(
                {
                    "id": rid,
                    "role_key": role_key,
                    "name": str(row["name"]),
                    "description": str(row.get("description") or ""),
                    "is_builtin": bool(row.get("is_builtin")),
                    "user_count": int(row.get("user_count") or 0),
                    "agent_ids": grants_by_role.get(rid, []),
                    "permissions": perm_summary["permissions"],
                    "menus": perm_summary["menus"],
                    "created_at": _to_iso(row.get("created_at")),
                    "updated_at": _to_iso(row.get("updated_at")),
                }
            )
        return result

    def get_role(self, role_id: int) -> dict[str, Any] | None:
        roles = [r for r in self.list_roles() if r["id"] == int(role_id)]
        return roles[0] if roles else None

    def create_role(self, *, role_key: str, name: str, description: str = "") -> dict[str, Any]:
        clean_key = validate_role_key(role_key)
        if clean_key in BUILTIN_ROLE_KEYS:
            raise ValueError(f"角色标识 {clean_key!r} 为内置保留标识")
        clean_name = str(name or "").strip()[:128]
        if not clean_name:
            raise ValueError("角色名称不能为空")
        clean_desc = str(description or "").strip()[:500]
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM da_role WHERE role_key = %s LIMIT 1", (clean_key,))
                if cur.fetchone():
                    raise ValueError(f"角色标识已存在: {clean_key}")
                cur.execute(
                    "INSERT INTO da_role (role_key, name, description, is_builtin) VALUES (%s, %s, %s, 0)",
                    (clean_key, clean_name, clean_desc),
                )
                rid = int(cur.lastrowid)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get_role(rid) or {}

    def update_role(self, role_id: int, *, name: str | None = None, description: str | None = None) -> dict[str, Any]:
        rid = int(role_id)
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM da_role WHERE id = %s FOR UPDATE", (rid,))
                row = cur.fetchone()
                if not row:
                    raise AccountNotFoundError("角色不存在")
                updates: list[str] = []
                params: list[Any] = []
                if name is not None:
                    clean_name = str(name).strip()[:128]
                    if not clean_name:
                        raise ValueError("角色名称不能为空")
                    updates.append("name = %s")
                    params.append(clean_name)
                if description is not None:
                    updates.append("description = %s")
                    params.append(str(description).strip()[:500])
                if updates:
                    cur.execute(f"UPDATE da_role SET {', '.join(updates)} WHERE id = %s", [*params, rid])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get_role(rid) or {}

    def delete_role(self, role_id: int) -> bool:
        rid = int(role_id)
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, role_key, is_builtin FROM da_role WHERE id = %s FOR UPDATE", (rid,))
                row = cur.fetchone()
                if not row:
                    raise AccountNotFoundError("角色不存在")
                if row.get("is_builtin") or str(row.get("role_key") or "") in BUILTIN_ROLE_KEYS:
                    raise ValueError("内置角色不可删除")
                cur.execute("DELETE FROM da_user_role WHERE role_id = %s", (rid,))
                cur.execute(
                    "DELETE FROM da_agent_grant WHERE principal_type = 'role' AND principal_id = %s",
                    (rid,),
                )
                cur.execute("DELETE FROM da_role WHERE id = %s", (rid,))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_role_users(self, role_id: int) -> list[dict[str, Any]]:
        rid = int(role_id)
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM da_role WHERE id = %s LIMIT 1", (rid,))
                if not cur.fetchone():
                    raise AccountNotFoundError("角色不存在")
                cur.execute(
                    """
                    SELECT u.id, u.username, u.display_name, u.is_active
                    FROM da_user_role ur
                    JOIN da_user u ON u.id = ur.user_id
                    WHERE ur.role_id = %s
                    ORDER BY u.id ASC
                    """,
                    (rid,),
                )
                return [
                    {
                        "id": int(r["id"]),
                        "user_id": int(r["id"]),
                        "username": str(r["username"]),
                        "display_name": str(r.get("display_name") or r["username"]),
                        "is_active": bool(r.get("is_active")),
                    }
                    for r in cur.fetchall() or []
                ]
        finally:
            conn.close()

    def set_role_users(self, role_id: int, user_ids: Sequence[int]) -> list[dict[str, Any]]:
        rid = int(role_id)
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, role_key FROM da_role WHERE id = %s FOR UPDATE", (rid,))
                role = cur.fetchone()
                if not role:
                    raise AccountNotFoundError("角色不存在")
                validated = self._validate_user_ids(cur, user_ids)
                if str(role["role_key"]) == ROLE_ADMIN:
                    if not validated:
                        raise ValueError("管理员角色至少需要保留一个启用的账号")
                    placeholders = ", ".join(["%s"] * len(validated))
                    cur.execute(
                        f"SELECT COUNT(*) AS total FROM da_user WHERE id IN ({placeholders}) AND is_active = 1",
                        validated,
                    )
                    if int((cur.fetchone() or {}).get("total") or 0) == 0:
                        raise ValueError("管理员角色至少需要保留一个启用的账号")
                cur.execute("DELETE FROM da_user_role WHERE role_id = %s", (rid,))
                for uid in validated:
                    cur.execute(
                        "INSERT IGNORE INTO da_user_role (user_id, role_id) VALUES (%s, %s)",
                        (uid, rid),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.list_role_users(rid)

    # ---- Agent Grants (da_agent_grant) -------------------------------------

    def get_agent_grants(self, agent_id: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                self._validate_agent_exists(cur, agent_id)
                cur.execute(
                    """
                    SELECT u.id, u.username, u.display_name, u.is_active
                    FROM da_agent_grant g
                    JOIN da_user u ON u.id = g.principal_id
                    WHERE g.agent_id = %s AND g.principal_type = 'user'
                    ORDER BY u.id ASC
                    """,
                    (agent_id,),
                )
                users = [
                    {
                        "id": int(r["id"]),
                        "user_id": int(r["id"]),
                        "username": str(r["username"]),
                        "display_name": str(r.get("display_name") or r["username"]),
                        "is_active": bool(r.get("is_active")),
                    }
                    for r in cur.fetchall() or []
                ]
                cur.execute(
                    """
                    SELECT r.id, r.role_key, r.name, r.is_builtin
                    FROM da_agent_grant g
                    JOIN da_role r ON r.id = g.principal_id
                    WHERE g.agent_id = %s AND g.principal_type = 'role'
                    ORDER BY r.id ASC
                    """,
                    (agent_id,),
                )
                roles = [
                    {
                        "id": int(r["id"]),
                        "role_key": str(r["role_key"]),
                        "name": str(r["name"]),
                        "is_builtin": bool(r.get("is_builtin")),
                    }
                    for r in cur.fetchall() or []
                ]
        finally:
            conn.close()
        return {
            "agent_id": agent_id,
            "user_ids": [u["id"] for u in users],
            "role_ids": [r["id"] for r in roles],
            "users": users,
            "roles": roles,
        }

    def set_agent_grants(
        self,
        agent_id: str,
        *,
        user_ids: Sequence[int] | None = None,
        role_ids: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                self._validate_agent_exists(cur, agent_id)
                if user_ids is not None:
                    valid_users = self._validate_user_ids(cur, user_ids)
                    cur.execute(
                        "DELETE FROM da_agent_grant WHERE agent_id = %s AND principal_type = 'user'",
                        (agent_id,),
                    )
                    for uid in valid_users:
                        cur.execute(
                            "INSERT IGNORE INTO da_agent_grant (agent_id, principal_type, principal_id) VALUES (%s, 'user', %s)",
                            (agent_id, uid),
                        )
                if role_ids is not None:
                    valid_roles = self._validate_role_ids(cur, role_ids)
                    cur.execute(
                        "DELETE FROM da_agent_grant WHERE agent_id = %s AND principal_type = 'role'",
                        (agent_id,),
                    )
                    for rid in valid_roles:
                        cur.execute(
                            "INSERT IGNORE INTO da_agent_grant (agent_id, principal_type, principal_id) VALUES (%s, 'role', %s)",
                            (agent_id, rid),
                        )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get_agent_grants(agent_id)

    def set_role_agent_grants(self, role_id: int, agent_ids: Sequence[str]) -> dict[str, Any]:
        rid = int(role_id)
        deduped = list(dict.fromkeys(str(a or "").strip() for a in agent_ids if str(a or "").strip()))
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM da_role WHERE id = %s FOR UPDATE", (rid,))
                if not cur.fetchone():
                    raise AccountNotFoundError("角色不存在")
                for aid in deduped:
                    self._validate_agent_exists(cur, aid)
                cur.execute(
                    "DELETE FROM da_agent_grant WHERE principal_type = 'role' AND principal_id = %s",
                    (rid,),
                )
                for aid in deduped:
                    cur.execute(
                        "INSERT IGNORE INTO da_agent_grant (agent_id, principal_type, principal_id) VALUES (%s, 'role', %s)",
                        (aid, rid),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get_role(rid) or {}

    def user_has_agent_grant(self, user_id: int, role_ids: Sequence[int], agent_id: str) -> bool:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                if role_ids:
                    placeholders = ", ".join(["%s"] * len(role_ids))
                    cur.execute(
                        f"""
                        SELECT 1 FROM da_agent_grant
                        WHERE agent_id = %s
                          AND (
                              (principal_type = 'user' AND principal_id = %s)
                              OR (principal_type = 'role' AND principal_id IN ({placeholders}))
                          )
                        LIMIT 1
                        """,
                        [agent_id, int(user_id), *[int(r) for r in role_ids]],
                    )
                else:
                    cur.execute(
                        """
                        SELECT 1 FROM da_agent_grant
                        WHERE agent_id = %s AND principal_type = 'user' AND principal_id = %s
                        LIMIT 1
                        """,
                        (agent_id, int(user_id)),
                    )
                return cur.fetchone() is not None
        finally:
            conn.close()

    def list_granted_agent_ids(self, user_id: int, role_ids: Sequence[int]) -> set[str]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                if role_ids:
                    placeholders = ", ".join(["%s"] * len(role_ids))
                    cur.execute(
                        f"""
                        SELECT DISTINCT agent_id FROM da_agent_grant
                        WHERE (principal_type = 'user' AND principal_id = %s)
                           OR (principal_type = 'role' AND principal_id IN ({placeholders}))
                        """,
                        [int(user_id), *[int(r) for r in role_ids]],
                    )
                else:
                    cur.execute(
                        "SELECT DISTINCT agent_id FROM da_agent_grant WHERE principal_type = 'user' AND principal_id = %s",
                        (int(user_id),),
                    )
                return {str(r["agent_id"]) for r in cur.fetchall() or [] if r.get("agent_id")}
        finally:
            conn.close()


_STORE: AccountStore | None = None
_STORE_LOCK = threading.Lock()


def get_account_store() -> AccountStore:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = AccountStore()
    return _STORE


def set_account_store_for_tests(store: AccountStore | None) -> None:
    global _STORE
    with _STORE_LOCK:
        _STORE = store
