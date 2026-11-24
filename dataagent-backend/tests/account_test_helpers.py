"""In-memory AccountStore and auth setup helpers for unit/contract backend tests."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable, Sequence

from core.accounts import (
    AccountDisabledError,
    AccountNotFoundError,
    AuthSettings,
    IdentityConflictError,
    IdentityRef,
    ROLE_ADMIN,
    ROLE_USER,
    RoleRef,
    User,
    hash_password,
    issue_session_token,
    menus_for_permissions,
    permissions_for_roles,
    role_permission_summary,
    set_account_store_for_tests,
    set_auth_settings_for_tests,
    validate_role_key,
    validate_username,
)

TEST_SECRET_KEY = "test-secret-key-at-least-thirty-two-bytes-long-0001"
TEST_DEFAULT_PASSWORD = "default-pass-123"


def _now_iso() -> str:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0).isoformat(timespec="seconds") + "Z"


class FakeAccountStore:
    """In-memory drop-in replacement for AccountStore used in unit/contract tests."""

    def __init__(self, *, valid_agent_ids: set[str] | None = None):
        self._next_uid = 1
        self._next_rid = 1
        self._next_iid = 1
        self.users: dict[int, dict[str, Any]] = {}
        self.roles: dict[int, dict[str, Any]] = {}
        self.user_roles: set[tuple[int, int]] = set()
        self.identities: dict[int, dict[str, Any]] = {}
        self.agent_grants: set[tuple[str, str, int]] = set()
        self.valid_agent_ids: set[str] | None = valid_agent_ids

        # Seed built-in roles
        self._seed_role(ROLE_ADMIN, "管理员", "系统管理员", is_builtin=True)
        self._seed_role(ROLE_USER, "普通用户", "默认用户角色", is_builtin=True)

    def _seed_role(self, role_key: str, name: str, description: str, *, is_builtin: bool) -> int:
        rid = self._next_rid
        self._next_rid += 1
        self.roles[rid] = {
            "id": rid,
            "role_key": role_key,
            "name": name,
            "description": description,
            "is_builtin": is_builtin,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        return rid

    def role_id_for(self, role_key: str) -> int:
        for rid, r in self.roles.items():
            if r["role_key"] == role_key:
                return rid
        raise KeyError(role_key)

    def _hydrate(self, row: dict[str, Any]) -> User:
        uid = int(row["id"])
        roles = tuple(
            RoleRef(
                id=self.roles[rid]["id"],
                role_key=self.roles[rid]["role_key"],
                name=self.roles[rid]["name"],
                is_builtin=self.roles[rid]["is_builtin"],
            )
            for (u, rid) in sorted(self.user_roles)
            if u == uid and rid in self.roles
        )
        idents = tuple(
            IdentityRef(
                id=i["id"],
                source=i["source"],
                subject=i["subject"],
                created_at=i["created_at"],
            )
            for i in sorted(self.identities.values(), key=lambda x: x["id"])
            if i["user_id"] == uid
        )
        perms = permissions_for_roles(r.role_key for r in roles)
        return User(
            id=uid,
            username=row["username"],
            display_name=row["display_name"] or row["username"],
            email=row["email"],
            email_verified=bool(row["email_verified"]),
            is_active=bool(row["is_active"]),
            roles=roles,
            identities=idents,
            permissions=perms,
            menus=menus_for_permissions(perms),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_login_at=row["last_login_at"],
        )

    def _count_active_admins(self, *, exclude_user_id: int | None = None) -> int:
        admin_rid = self.role_id_for(ROLE_ADMIN)
        return sum(
            1
            for uid, u in self.users.items()
            if uid != exclude_user_id and u["is_active"] and (uid, admin_rid) in self.user_roles
        )

    def _check_agent(self, agent_id: str) -> None:
        if self.valid_agent_ids is not None and agent_id not in self.valid_agent_ids:
            raise AccountNotFoundError(f"助手不存在: {agent_id}")

    def get_user(self, user_id: int) -> User | None:
        row = self.users.get(int(user_id))
        return self._hydrate(row) if row else None

    def get_user_with_password(self, username: str) -> tuple[User, str] | None:
        clean = str(username or "").strip()
        for row in self.users.values():
            if row["username"].lower() == clean.lower():
                return self._hydrate(row), row["password_hash"]
        return None

    def get_user_password_hash(self, user_id: int) -> str:
        row = self.users.get(int(user_id))
        return row["password_hash"] if row else ""

    def get_user_by_identity(self, source: str, subject: str) -> User | None:
        for i in self.identities.values():
            if i["source"] == source and i["subject"] == subject:
                return self.get_user(i["user_id"])
        return None

    def list_users(
        self,
        *,
        keyword: str = "",
        is_active: bool | None = None,
        role_id: int | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        kw = str(keyword or "").strip().lower()
        items: list[User] = []
        for row in sorted(self.users.values(), key=lambda x: x["id"]):
            u = self._hydrate(row)
            if kw and kw not in u.username.lower() and kw not in u.display_name.lower() and kw not in (u.email or "").lower():
                continue
            if is_active is not None and u.is_active != is_active:
                continue
            if role_id is not None and int(role_id) not in u.role_ids:
                continue
            items.append(u)
        total = len(items)
        start = (max(1, page) - 1) * page_size
        return {
            "items": [u.to_public_dict() for u in items[start : start + page_size]],
            "total": total,
            "page": max(1, page),
            "page_size": page_size,
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
        if any(r["username"].lower() == clean_username.lower() for r in self.users.values()):
            raise ValueError(f"用户名已存在: {clean_username}")
        if role_ids is not None:
            rids = [int(r) for r in dict.fromkeys(role_ids)]
            for r in rids:
                if r not in self.roles:
                    raise ValueError(f"角色不存在: {r}")
        elif role_keys is not None:
            rids = [self.role_id_for(k) for k in role_keys]
        else:
            rids = [self.role_id_for(ROLE_USER)]

        uid = self._next_uid
        self._next_uid += 1
        now = _now_iso()
        clean_email = str(email or "").strip() or None
        self.users[uid] = {
            "id": uid,
            "username": clean_username,
            "display_name": str(display_name or "").strip() or clean_username,
            "email": clean_email,
            "email_verified": bool(clean_email and email_verified),
            "password_hash": password_hash,
            "is_active": bool(is_active),
            "created_at": now,
            "updated_at": now,
            "last_login_at": None,
        }
        for rid in rids:
            self.user_roles.add((uid, rid))
        return self._hydrate(self.users[uid])

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
        row = self.users.get(uid)
        if not row:
            raise AccountNotFoundError("用户不存在")
        current = self._hydrate(row)
        if display_name is not None:
            clean_d = str(display_name).strip()
            if not clean_d:
                raise ValueError("显示名不能为空")
            row["display_name"] = clean_d
        if email is not ...:
            clean_e = str(email or "").strip() or None
            if email_verified is not None:
                row["email_verified"] = bool(clean_e and email_verified)
            elif clean_e != row["email"]:
                row["email_verified"] = False
            row["email"] = clean_e
        if is_active is not None:
            if not is_active and acting_user_id == uid:
                raise ValueError("不能停用当前登录账号")
            if not is_active and current.is_admin and self._count_active_admins(exclude_user_id=uid) == 0:
                raise ValueError("至少需要保留一个启用的管理员账号")
            row["is_active"] = bool(is_active)
        if role_ids is not None:
            rids = [int(r) for r in dict.fromkeys(role_ids)]
            for r in rids:
                if r not in self.roles:
                    raise ValueError(f"角色不存在: {r}")
            admin_rid = self.role_id_for(ROLE_ADMIN)
            if current.is_admin and admin_rid not in rids and self._count_active_admins(exclude_user_id=uid) == 0:
                raise ValueError("至少需要保留一个启用的管理员账号")
            self.user_roles = {(u, r) for (u, r) in self.user_roles if u != uid}
            for rid in rids:
                self.user_roles.add((uid, rid))
        row["updated_at"] = _now_iso()
        return self._hydrate(row)

    def update_password(self, user_id: int, password_hash: str) -> None:
        row = self.users.get(int(user_id))
        if not row:
            raise AccountNotFoundError("用户不存在")
        row["password_hash"] = password_hash
        row["updated_at"] = _now_iso()

    def touch_last_login(self, user_id: int) -> None:
        row = self.users.get(int(user_id))
        if row:
            row["last_login_at"] = _now_iso()

    def add_identity(self, user_id: int, source: str, subject: str) -> IdentityRef:
        uid = int(user_id)
        if uid not in self.users:
            raise AccountNotFoundError("用户不存在")
        for i in self.identities.values():
            if i["source"] == source and i["subject"] == subject:
                if i["user_id"] == uid:
                    raise ValueError("该外部身份已绑定到当前用户")
                raise IdentityConflictError("该外部身份已绑定到其他用户")
        iid = self._next_iid
        self._next_iid += 1
        rec = {"id": iid, "source": source, "subject": subject, "user_id": uid, "created_at": _now_iso()}
        self.identities[iid] = rec
        return IdentityRef(id=iid, source=source, subject=subject, created_at=rec["created_at"])

    def remove_identity(self, user_id: int, identity_id: int) -> bool:
        rec = self.identities.get(int(identity_id))
        if rec and rec["user_id"] == int(user_id):
            del self.identities[int(identity_id)]
            return True
        return False

    def link_or_register(
        self,
        source: str,
        subject: str,
        profile: dict[str, Any] | None,
        *,
        default_password_hash_fn: Callable[[], str],
        trusted_username_namespace: bool = False,
    ) -> User:
        prof = dict(profile or {})
        raw_username = str(prof.get("username") or prof.get("preferred_username") or subject).strip()
        raw_display = str(prof.get("display_name") or prof.get("name") or "").strip()
        raw_email = str(prof.get("email") or "").strip() or None
        email_verified = bool(prof.get("email_verified")) and bool(raw_email)

        existing = self.get_user_by_identity(source, subject)
        if existing is not None:
            if not existing.is_active:
                raise AccountDisabledError("账号已停用")
            row = self.users[existing.id]
            row["last_login_at"] = _now_iso()
            if not row["display_name"] and raw_display:
                row["display_name"] = raw_display
            if not row["email"] and email_verified and raw_email:
                row["email"] = raw_email
                row["email_verified"] = True
            return self._hydrate(row)

        candidate = validate_username(raw_username)
        matched_row = next((r for r in self.users.values() if r["username"].lower() == candidate.lower()), None)
        if matched_row is not None:
            matched_user = self._hydrate(matched_row)
            if not trusted_username_namespace:
                raise IdentityConflictError(f"用户名 {candidate!r} 已存在且未绑定外部身份")
            if matched_user.is_admin:
                raise IdentityConflictError(f"管理员账号 {candidate!r} 需要手动绑定外部身份")
            if not matched_user.is_active:
                raise AccountDisabledError("账号已停用")
            self.add_identity(matched_user.id, source, subject)
            matched_row["last_login_at"] = _now_iso()
            return self._hydrate(matched_row)

        pw_hash = default_password_hash_fn()
        created = self.create_user(
            username=candidate,
            display_name=raw_display or candidate,
            password_hash=pw_hash,
            email=raw_email if email_verified else None,
            email_verified=email_verified,
            role_keys=[ROLE_USER],
        )
        self.add_identity(created.id, source, subject)
        self.touch_last_login(created.id)
        return self.get_user(created.id) or created

    def list_roles(self) -> list[dict[str, Any]]:
        result = []
        for r in sorted(self.roles.values(), key=lambda x: (not x["is_builtin"], x["id"])):
            rid = r["id"]
            summary = role_permission_summary(r["role_key"])
            result.append({
                **r,
                "user_count": sum(1 for (u, role_id) in self.user_roles if role_id == rid),
                "agent_ids": sorted(a for (a, pt, pid) in self.agent_grants if pt == "role" and pid == rid),
                **summary,
            })
        return result

    def get_role(self, role_id: int) -> dict[str, Any] | None:
        return next((r for r in self.list_roles() if r["id"] == int(role_id)), None)

    def create_role(self, *, role_key: str, name: str, description: str = "") -> dict[str, Any]:
        key = validate_role_key(role_key)
        if key in {ROLE_ADMIN, ROLE_USER}:
            raise ValueError(f"角色标识 {key!r} 为内置保留标识")
        if any(r["role_key"] == key for r in self.roles.values()):
            raise ValueError(f"角色标识已存在: {key}")
        rid = self._seed_role(key, str(name).strip(), str(description).strip(), is_builtin=False)
        return self.get_role(rid) or {}

    def update_role(self, role_id: int, *, name: str | None = None, description: str | None = None) -> dict[str, Any]:
        r = self.roles.get(int(role_id))
        if not r:
            raise AccountNotFoundError("角色不存在")
        if name is not None:
            r["name"] = str(name).strip()
        if description is not None:
            r["description"] = str(description).strip()
        r["updated_at"] = _now_iso()
        return self.get_role(int(role_id)) or {}

    def delete_role(self, role_id: int) -> bool:
        rid = int(role_id)
        r = self.roles.get(rid)
        if not r:
            raise AccountNotFoundError("角色不存在")
        if r["is_builtin"]:
            raise ValueError("内置角色不可删除")
        self.user_roles = {(u, role_id_) for (u, role_id_) in self.user_roles if role_id_ != rid}
        self.agent_grants = {(a, pt, pid) for (a, pt, pid) in self.agent_grants if not (pt == "role" and pid == rid)}
        del self.roles[rid]
        return True

    def list_role_users(self, role_id: int) -> list[dict[str, Any]]:
        rid = int(role_id)
        if rid not in self.roles:
            raise AccountNotFoundError("角色不存在")
        return [
            {
                "id": u["id"],
                "user_id": u["id"],
                "username": u["username"],
                "display_name": u["display_name"],
                "is_active": u["is_active"],
            }
            for u in sorted(self.users.values(), key=lambda x: x["id"])
            if (u["id"], rid) in self.user_roles
        ]

    def set_role_users(self, role_id: int, user_ids: Sequence[int]) -> list[dict[str, Any]]:
        rid = int(role_id)
        if rid not in self.roles:
            raise AccountNotFoundError("角色不存在")
        uids = [int(u) for u in dict.fromkeys(user_ids)]
        for u in uids:
            if u not in self.users:
                raise ValueError(f"用户不存在: {u}")
        if self.roles[rid]["role_key"] == ROLE_ADMIN:
            if not any(self.users[u]["is_active"] for u in uids):
                raise ValueError("管理员角色至少需要保留一个启用的账号")
        self.user_roles = {(u, r) for (u, r) in self.user_roles if r != rid}
        for u in uids:
            self.user_roles.add((u, rid))
        return self.list_role_users(rid)

    def get_agent_grants(self, agent_id: str) -> dict[str, Any]:
        self._check_agent(agent_id)
        uids = sorted(pid for (a, pt, pid) in self.agent_grants if a == agent_id and pt == "user" and pid in self.users)
        rids = sorted(pid for (a, pt, pid) in self.agent_grants if a == agent_id and pt == "role" and pid in self.roles)
        return {
            "agent_id": agent_id,
            "user_ids": uids,
            "role_ids": rids,
            "users": [
                {
                    "id": u,
                    "user_id": u,
                    "username": self.users[u]["username"],
                    "display_name": self.users[u]["display_name"],
                    "is_active": self.users[u]["is_active"],
                }
                for u in uids
            ],
            "roles": [
                {
                    "id": r,
                    "role_key": self.roles[r]["role_key"],
                    "name": self.roles[r]["name"],
                    "is_builtin": self.roles[r]["is_builtin"],
                }
                for r in rids
            ],
        }

    def set_agent_grants(
        self,
        agent_id: str,
        *,
        user_ids: Sequence[int] | None = None,
        role_ids: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        self._check_agent(agent_id)
        if user_ids is not None:
            for u in user_ids:
                if int(u) not in self.users:
                    raise ValueError(f"用户不存在: {u}")
            self.agent_grants = {g for g in self.agent_grants if not (g[0] == agent_id and g[1] == "user")}
            for u in user_ids:
                self.agent_grants.add((agent_id, "user", int(u)))
        if role_ids is not None:
            for r in role_ids:
                if int(r) not in self.roles:
                    raise ValueError(f"角色不存在: {r}")
            self.agent_grants = {g for g in self.agent_grants if not (g[0] == agent_id and g[1] == "role")}
            for r in role_ids:
                self.agent_grants.add((agent_id, "role", int(r)))
        return self.get_agent_grants(agent_id)

    def set_role_agent_grants(self, role_id: int, agent_ids: Sequence[str]) -> dict[str, Any]:
        rid = int(role_id)
        if rid not in self.roles:
            raise AccountNotFoundError("角色不存在")
        for a in agent_ids:
            self._check_agent(a)
        self.agent_grants = {g for g in self.agent_grants if not (g[1] == "role" and g[2] == rid)}
        for a in agent_ids:
            self.agent_grants.add((a, "role", rid))
        return self.get_role(rid) or {}

    def user_has_agent_grant(self, user_id: int, role_ids: Sequence[int], agent_id: str) -> bool:
        if (agent_id, "user", int(user_id)) in self.agent_grants:
            return True
        return any((agent_id, "role", int(r)) in self.agent_grants for r in role_ids)

    def list_granted_agent_ids(self, user_id: int, role_ids: Sequence[int]) -> set[str]:
        rset = {int(r) for r in role_ids}
        return {
            a
            for (a, pt, pid) in self.agent_grants
            if (pt == "user" and pid == int(user_id)) or (pt == "role" and pid in rset)
        }


def install_fake_accounts(
    *,
    admin_username: str = "admin",
    admin_password: str = "admin123",
    user_username: str = "alice",
    user_password: str = "alice123",
    valid_agent_ids: set[str] | None = None,
    settings: AuthSettings | None = None,
) -> tuple[FakeAccountStore, User, User]:
    """Configure AuthSettings + FakeAccountStore with an admin and ordinary user."""
    cfg = settings or AuthSettings(
        secret_key=TEST_SECRET_KEY,
        default_user_password=TEST_DEFAULT_PASSWORD,
        auto_register_enabled=True,
    )
    set_auth_settings_for_tests(cfg)
    store = FakeAccountStore(valid_agent_ids=valid_agent_ids)
    admin = store.create_user(
        username=admin_username,
        display_name="管理员",
        password_hash=hash_password(admin_password),
        role_keys=[ROLE_ADMIN, ROLE_USER],
    )
    user = store.create_user(
        username=user_username,
        display_name="Alice",
        password_hash=hash_password(user_password),
        role_keys=[ROLE_USER],
    )
    set_account_store_for_tests(store)
    return store, admin, user


def bearer_headers(user_id: int, extra: dict[str, str] | None = None) -> dict[str, str]:
    token = issue_session_token(user_id)
    headers = {"Authorization": f"Bearer {token}"}
    if extra:
        headers.update(extra)
    return headers
