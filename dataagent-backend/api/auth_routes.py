"""Authentication routes: password login against da_user + OAuth2 authorization code login."""
from __future__ import annotations

import logging
from urllib.parse import urlencode

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

from core.accounts import (
    OAUTH_STATE_COOKIE,
    OAUTH_STATE_COOKIE_MAX_AGE,
    AccountDisabledError,
    IdentityConflictError,
    User,
    authenticate_password,
    current_user,
    generate_oauth_nonce,
    get_auth_settings,
    issue_oauth_state,
    issue_session_token,
    link_or_register,
    sanitize_redirect_path,
    verify_oauth_state,
    verify_oauth_state_binding,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/nl2sql/auth", tags=["auth"])
oauth_callback_router = APIRouter(tags=["auth"])

_OAUTH_HTTP_TIMEOUT_SECONDS = 15.0
_MAX_IDENTITY_FIELD_LENGTH = 255


class _OAuthUserInfoError(RuntimeError):
    """External OAUTH_USER_INFO hook failed or violated contract."""


def _terminal_oauth_redirect(error: str) -> RedirectResponse:
    response = RedirectResponse(url=f"/login?error={error}", status_code=302)
    response.delete_cookie(key=OAUTH_STATE_COOKIE, path="/")
    return response


def _valid_identity_text(value: object, *, max_length: int = _MAX_IDENTITY_FIELD_LENGTH) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= max_length
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


class LoginRequest(BaseModel):
    username: str
    password: str


def _set_session_cookie(response: Response, token: str) -> None:
    cfg = get_auth_settings()
    response.set_cookie(
        key=cfg.cookie_name,
        value=token,
        max_age=cfg.session_ttl_seconds,
        path="/",
        httponly=True,
        secure=cfg.cookie_secure,
        samesite=cfg.cookie_samesite,
    )


def _clear_session_cookie(response: Response) -> None:
    cfg = get_auth_settings()
    response.delete_cookie(key=cfg.cookie_name, path="/")


@router.get("/config")
async def auth_config():
    cfg = get_auth_settings()
    return {
        "enabled": True,
        "provider_name": cfg.oauth.provider_name if cfg.oauth_login_enabled else "",
        "provider_icon": cfg.oauth.icon if cfg.oauth_login_enabled else "",
        "local_login_enabled": True,
        "oauth_login_enabled": cfg.oauth_login_enabled,
    }


@router.post("/login")
def login(payload: LoginRequest):
    user = authenticate_password(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    response = JSONResponse({"code": 200, "data": user.to_public_dict()})
    _set_session_cookie(response, issue_session_token(user.id))
    return response


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return {"code": 200, "data": user.to_public_dict()}


@router.post("/logout")
async def logout():
    response = JSONResponse({"code": 200, "data": {"ok": True}})
    _clear_session_cookie(response)
    return response


@router.get("/oauth/authorize")
async def oauth_authorize(request: Request):
    cfg = get_auth_settings()
    if not cfg.oauth_login_enabled:
        raise HTTPException(status_code=400, detail="OAuth login is not configured")
    redirect_path = sanitize_redirect_path(request.query_params.get("redirect"), fallback="")
    nonce = generate_oauth_nonce()
    params = {
        "response_type": cfg.oauth.response_type,
        "client_id": cfg.oauth.client_id,
        "redirect_uri": cfg.oauth.redirect_uri,
        "scope": cfg.oauth.scopes,
        "state": issue_oauth_state(
            redirect_path,
            nonce=nonce,
            provider=cfg.oauth.provider_name,
        ),
    }
    response = RedirectResponse(url=f"{cfg.oauth.authorize_url}?{urlencode(params)}", status_code=302)
    response.set_cookie(
        key=OAUTH_STATE_COOKIE,
        value=nonce,
        max_age=OAUTH_STATE_COOKIE_MAX_AGE,
        path="/",
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="lax",
    )
    return response


async def _exchange_code_for_userinfo(code: str) -> dict:
    cfg = get_auth_settings()
    async with httpx.AsyncClient(timeout=_OAUTH_HTTP_TIMEOUT_SECONDS) as client:
        token_response = await client.post(
            cfg.oauth.token_url,
            data={
                "grant_type": cfg.oauth.grant_type,
                "code": code,
                "client_id": cfg.oauth.client_id,
                "client_secret": cfg.oauth.client_secret,
                "redirect_uri": cfg.oauth.redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        token_response.raise_for_status()
        token_payload = token_response.json()
        if not isinstance(token_payload, dict):
            raise HTTPException(status_code=502, detail="OAuth token endpoint returned unexpected payload")

    handler = cfg.oauth_user_info
    if handler is None:
        raise _OAuthUserInfoError("OAUTH_USER_INFO is not configured")
    access_token = token_payload.get("access_token")
    raw_token_type = token_payload.get("token_type")
    token_type = "Bearer" if raw_token_type in (None, "") else raw_token_type
    if not _valid_identity_text(access_token, max_length=8192):
        raise HTTPException(status_code=502, detail="OAuth token endpoint returned no valid access_token")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise HTTPException(status_code=502, detail="OAuth token endpoint returned unsupported token_type")

    def _call_user_info_handler() -> object:
        with httpx.Client(
            base_url=cfg.oauth.api_base_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=_OAUTH_HTTP_TIMEOUT_SECONDS,
        ) as remote:
            oauth_remotes = {cfg.oauth.provider_name: remote}
            return handler(cfg.oauth.provider_name, token_payload, oauth_remotes)

    try:
        userinfo = await anyio.to_thread.run_sync(_call_user_info_handler)
    except Exception as e:
        raise _OAuthUserInfoError("OAUTH_USER_INFO failed") from e
    if not isinstance(userinfo, dict):
        raise _OAuthUserInfoError("OAUTH_USER_INFO must return a dict")
    return userinfo


@oauth_callback_router.get("/oauth-authorized/{provider}")
async def oauth_callback(provider: str, request: Request):
    cfg = get_auth_settings()
    if not cfg.oauth_login_enabled:
        raise HTTPException(status_code=400, detail="OAuth login is not configured")
    if provider != cfg.oauth.provider_name:
        raise HTTPException(status_code=404, detail="OAuth provider is not configured")

    state_payload = verify_oauth_state(request.query_params.get("state") or "")
    if state_payload is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    if str(state_payload.get("provider") or "") != provider:
        raise HTTPException(status_code=400, detail="OAuth state provider does not match callback")
    if not verify_oauth_state_binding(state_payload, request.cookies.get(OAUTH_STATE_COOKIE)):
        raise HTTPException(status_code=400, detail="OAuth state is not bound to this browser")
    if request.query_params.get("error"):
        return _terminal_oauth_redirect("oauth_denied")
    code = str(request.query_params.get("code") or "")
    if not code:
        return _terminal_oauth_redirect("oauth_exchange_failed")

    try:
        userinfo = await _exchange_code_for_userinfo(code)
    except _OAuthUserInfoError as e:
        cause = e.__cause__ or e
        logger.error("OAUTH_USER_INFO failed provider=%s error_type=%s", provider, type(cause).__name__)
        return _terminal_oauth_redirect("oauth_user_info_failed")
    except HTTPException as e:
        logger.error("OAuth code exchange rejected provider=%s status_code=%s", provider, e.status_code)
        return _terminal_oauth_redirect("oauth_exchange_failed")
    except Exception as e:
        logger.error("OAuth code exchange failed provider=%s error_type=%s", provider, type(e).__name__)
        return _terminal_oauth_redirect("oauth_exchange_failed")

    oauth_sub = userinfo.get("sub")
    if not _valid_identity_text(oauth_sub):
        logger.error("OAuth userinfo missing sub: keys=%s", sorted(str(key) for key in userinfo))
        return _terminal_oauth_redirect("oauth_missing_user_id")

    # OAUTH_USER_INFO contract: sub (identity), preferred_username (login name),
    # display_name (shown name). The hook maps the IdP's own fields onto these.
    preferred_username = userinfo.get("preferred_username")
    display_name = userinfo.get("display_name")
    for claim, value in (("preferred_username", preferred_username), ("display_name", display_name)):
        if value not in (None, "") and not _valid_identity_text(value):
            logger.error("OAUTH_USER_INFO returned invalid %s", claim)
            return _terminal_oauth_redirect("oauth_user_info_failed")

    # Role claims from OAUTH_USER_INFO are deliberately ignored; roles live solely in MySQL.
    profile = {
        "username": preferred_username or oauth_sub,
        "display_name": display_name or "",
    }
    try:
        user = await anyio.to_thread.run_sync(
            lambda: link_or_register(cfg.oauth.provider_name, oauth_sub, profile)
        )
    except AccountDisabledError:
        return _terminal_oauth_redirect("account_disabled")
    except IdentityConflictError:
        return _terminal_oauth_redirect("identity_conflict")
    except ValueError:
        return _terminal_oauth_redirect("oauth_user_info_failed")

    redirect_path = sanitize_redirect_path(state_payload.get("redirect"), fallback="/")
    response = RedirectResponse(url=redirect_path, status_code=302)
    _set_session_cookie(response, issue_session_token(user.id, login_method="oauth"))
    response.delete_cookie(key=OAUTH_STATE_COOKIE, path="/")
    return response
