"""Unified request authentication, identity resolution, and authorization entry points."""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from typing import Any

from fastapi import Depends, HTTPException, Request
from starlette.requests import Request as StarletteRequest

from core.accounts.config import get_auth_settings, read_session_token
from core.accounts.models import (
    AccountContext,
    AccountDisabledError,
    AgentAccessDeniedError,
    AutoRegisterUnavailableError,
    IdentityConflictError,
    User,
)
from core.accounts.passwords import hash_password, verify_password
from core.accounts.permissions import ROLE_ADMIN, ROLE_USER
from core.accounts.store import get_account_store

logger = logging.getLogger(__name__)

_MCP_TOKEN_PREFIX = "dak_"
_MCP_TOKEN_RE = re.compile(rf"{_MCP_TOKEN_PREFIX}[A-Za-z0-9_-]{{43}}")


def _clean_header(value: str | None, max_length: int = 255) -> str:
    text = str(value or "").strip()
    return text[:max_length] if len(text) > max_length else text


def _allowed_widget_sites() -> list[dict[str, Any]]:
    from core.skill_admin_service import current_settings_payload

    try:
        payload = current_settings_payload()
    except Exception:
        logger.warning("Failed to load widget allowed sites from settings store", exc_info=True)
        return []
    sites = payload.get("widget_allowed_sites")
    if not isinstance(sites, list):
        return []
    return [item for item in sites if isinstance(item, dict)]


def _default_password_hash() -> str:
    cfg = get_auth_settings()
    if not cfg.default_user_password:
        raise AutoRegisterUnavailableError("未配置 DEFAULT_USER_PASSWORD，无法自动注册或重置密码")
    return hash_password(cfg.default_user_password)


def hash_default_password() -> str:
    """Compute a freshly salted Argon2id hash of the configured DEFAULT_USER_PASSWORD."""
    return _default_password_hash()


# ---------------------------------------------------------------------------
# Password authentication & local account bootstrap
# ---------------------------------------------------------------------------

def authenticate_password(username: str, password: str) -> User | None:
    """Verify username + password against da_user."""
    store = get_account_store()
    found = store.get_user_with_password(username)
    if found is None:
        logger.warning("Password login failed: unknown username=%r", username)
        return None
    user, stored_hash = found
    if not user.is_active:
        logger.warning("Password login rejected: disabled user_id=%s username=%r", user.id, user.username)
        return None
    if not verify_password(password, stored_hash):
        logger.warning("Password login failed: bad password user_id=%s username=%r", user.id, user.username)
        return None
    try:
        store.touch_last_login(user.id)
    except Exception:
        logger.warning("touch_last_login failed user_id=%s", user.id, exc_info=True)
    return store.get_user(user.id) or user


def ensure_local_user(
    *,
    username: str,
    password: str,
    display_name: str = "",
    is_admin: bool = False,
) -> User:
    """Create or update a local user (for bootstrap CLI, local dev, and smoke tests)."""
    store = get_account_store()
    pw_hash = hash_password(password)
    existing = store.get_user_with_password(username)
    role_keys = [ROLE_ADMIN, ROLE_USER] if is_admin else [ROLE_USER]
    if existing is None:
        return store.create_user(
            username=username,
            display_name=display_name or username,
            password_hash=pw_hash,
            role_keys=role_keys,
            is_active=True,
        )
    user, _ = existing
    store.update_password(user.id, pw_hash)
    roles = store.list_roles()
    role_ids = [r["id"] for r in roles if r["role_key"] in role_keys]
    return store.update_user(
        user.id,
        display_name=display_name or user.display_name,
        is_active=True,
        role_ids=role_ids,
    )


# ---------------------------------------------------------------------------
# External identity resolution & atomic registration
# ---------------------------------------------------------------------------

def link_or_register(
    source: str,
    subject: str,
    profile: dict[str, Any] | None = None,
) -> User:
    """Unified public entry point for OAuth & trusted Widget external identity binding/registration.

    An external user name is the platform user name (user decision 2026-11-24):
    an unbound identity binds to the same-named account or registers one.
    """
    store = get_account_store()

    def _pw_fn() -> str:
        return _default_password_hash()

    return store.link_or_register(
        source,
        subject,
        profile,
        default_password_hash_fn=_pw_fn,
    )


def _resolve_session(request: StarletteRequest) -> tuple[User, str] | None:
    cfg = get_auth_settings()
    token = request.cookies.get(cfg.cookie_name) or ""
    if not token:
        authorization = request.headers.get("Authorization") or ""
        if authorization.lower().startswith("bearer "):
            candidate = authorization[7:].strip()
            # Never treat personal MCP access tokens (dak_*) as web session JWTs.
            if not candidate.startswith(_MCP_TOKEN_PREFIX):
                token = candidate
    session = read_session_token(token)
    if session is None:
        return None
    user_id, login_method = session
    user = get_account_store().get_user(user_id)
    if user is None or not user.is_active:
        return None
    return user, login_method


# ---------------------------------------------------------------------------
# Public request context & FastAPI dependencies
# ---------------------------------------------------------------------------

def current_account_context(request: Request) -> AccountContext:
    """Validate credentials once per request and return the verified AccountContext."""
    cached = getattr(request.state, "_account_context", None)
    if isinstance(cached, AccountContext):
        return cached

    client = _clean_header(request.headers.get("X-DataAgent-Client"), 32).lower()
    if client != "widget":
        session = _resolve_session(request)
        if session is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        user, login_method = session
        ctx = AccountContext(user=user, login_method=login_method)
        request.state._account_context = ctx
        return ctx

    # Widget path: an allowed site whose host backend forwards the request with the
    # site's server-side access key and its signed-in user.
    website_id = _clean_header(request.headers.get("X-DataAgent-Website-Id"), 128)
    if not website_id:
        raise HTTPException(status_code=400, detail="X-DataAgent-Website-Id is required")

    matched_site = next(
        (site for site in _allowed_widget_sites() if str(site.get("website_id") or "").strip() == website_id),
        None,
    )
    if matched_site is None:
        logger.warning("Widget site rejected website_id=%r", website_id)
        raise HTTPException(status_code=403, detail="Widget site is not allowed")

    server_side = matched_site.get("server_side") if isinstance(matched_site.get("server_side"), dict) else {}
    is_server_side = server_side.get("enabled") is True

    if not is_server_side:
        raise HTTPException(
            status_code=401,
            detail={"code": "WIDGET_LOGIN_REQUIRED", "message": "请先登录后使用智能问数"},
        )

    expected_hash = str(server_side.get("access_key_hash") or "")
    presented_key = _clean_header(request.headers.get("X-DataAgent-Access-Key"), 128)
    if not expected_hash or not presented_key or not hmac.compare_digest(
        hashlib.sha256(presented_key.encode("utf-8")).hexdigest(), expected_hash
    ):
        logger.warning("Widget access key rejected website_id=%r", website_id)
        raise HTTPException(status_code=403, detail="Widget access key is invalid")

    raw_user_id = _clean_header(request.headers.get("X-DataAgent-User-Id"), 255)
    if not raw_user_id:
        raise HTTPException(
            status_code=401,
            detail={"code": "WIDGET_LOGIN_REQUIRED", "message": "请先登录后使用智能问数"},
        )
    # The host's user name is the platform user name (user decision 2026-11-24):
    # it binds to the same-named account or registers it. Only the name is forwarded.
    try:
        user = link_or_register(website_id, raw_user_id, {"username": raw_user_id})
    except AccountDisabledError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except IdentityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AutoRegisterUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ctx = AccountContext(
        user=user,
        source="widget",
        website_id=website_id,
        auth_method="widget_trusted",
    )
    request.state._account_context = ctx
    return ctx


def current_user(request: Request) -> User:
    """FastAPI dependency returning the verified active User of a platform session.

    Platform routes (account, admin, eval, MCP tokens) accept only the user's own
    DataAgent login. A host-forwarded Widget request is limited to the routes that
    take ``current_account_context`` (Widget runtime and assistant catalog).
    """
    ctx = current_account_context(request)
    if ctx.user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if ctx.auth_method == "widget_trusted":
        raise HTTPException(status_code=403, detail="Widget credentials cannot access platform routes")
    return ctx.user


def current_user_id(request: Request) -> int:
    """FastAPI dependency returning the integer primary key of the current User."""
    return current_user(request).id


def require_admin(user: User = Depends(current_user)) -> User:
    """FastAPI dependency for administrator-only routes (menus and management)."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator required")
    return user


# ---------------------------------------------------------------------------
# MCP personal access token authentication
# ---------------------------------------------------------------------------

def authenticate_mcp_token(authorization: str) -> User:
    """Verify a Bearer personal MCP access token and load the live active User."""
    from core import remote_mcp_tokens

    scheme, _, token = str(authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not _MCP_TOKEN_RE.fullmatch(token):
        raise HTTPException(status_code=401, detail="MCP 访问令牌无效或已被吊销")
    record = remote_mcp_tokens.find_active_token(token)
    if record is None:
        raise HTTPException(status_code=401, detail="MCP 访问令牌无效或已被吊销")
    user = get_account_store().get_user(int(record["user_id"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="账号已停用或不存在")
    remote_mcp_tokens.record_use(int(record["id"]), record.get("last_used_at"))
    return user


def authenticate_mcp_request(
    request: StarletteRequest,
    *,
    allow_web_session: bool = False,
) -> AccountContext:
    """Authenticate an MCP endpoint or MCP file-download request into an AccountContext."""
    authorization = request.headers.get("authorization")
    if allow_web_session and authorization is None:
        session = _resolve_session(request)
        if session is None:
            raise HTTPException(status_code=401, detail="请登录 DataAgent 后下载文件")
        user, _ = session
        return AccountContext(user=user, source="remote", auth_method="session")
    user = authenticate_mcp_token(authorization or "")
    return AccountContext(user=user, source="remote", auth_method="mcp_token")


# ---------------------------------------------------------------------------
# Assistant access enforcement (shared by HTTP routes, MCP, schedules, queues)
# ---------------------------------------------------------------------------

def can_user_access_agent(user: User | None, profile: dict[str, Any] | None) -> bool:
    if user is None or not user.is_active or not profile:
        return False
    if user.is_admin:
        return True
    visibility = profile.get("visibility") or {}
    mode = str(visibility.get("mode") or "all").strip().lower()
    if mode != "selected":  # "all": every signed-in user
        return True
    agent_id = str(profile.get("agent_id") or "").strip()
    if not agent_id:
        return False
    return get_account_store().user_has_agent_grant(user.id, user.role_ids, agent_id)


def require_agent_access(user_id: int, agent_id: str) -> dict[str, Any]:
    """Verify the user is active and allowed to use ``agent_id``.

    Shared by HTTP endpoints, MCP endpoints, and background scheduled/queued
    task execution (where ``user_id`` comes from the persisted topic owner).
    Raises ``AgentAccessDeniedError`` when denied or missing.
    """
    from core.agent_profile_service import DEFAULT_AGENT_ID, get_agent_profile

    user = get_account_store().get_user(int(user_id))
    if user is None or not user.is_active:
        raise AgentAccessDeniedError("账号不存在或已停用")
    resolved_agent_id = str(agent_id or "").strip() or DEFAULT_AGENT_ID
    profile = get_agent_profile(resolved_agent_id)
    if not profile or not can_user_access_agent(user, profile):
        raise AgentAccessDeniedError("agent not found")
    return profile


def filter_accessible_agents(user: User | None, profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if user is None or not user.is_active:
        return []
    if user.is_admin:
        return list(profiles)
    granted: set[str] | None = None
    visible: list[dict[str, Any]] = []
    for profile in profiles:
        mode = str((profile.get("visibility") or {}).get("mode") or "all").strip().lower()
        if mode != "selected":  # "all": every signed-in user
            visible.append(profile)
            continue
        if granted is None:
            granted = get_account_store().list_granted_agent_ids(user.id, user.role_ids)
        if str(profile.get("agent_id") or "") in granted:
            visible.append(profile)
    return visible
