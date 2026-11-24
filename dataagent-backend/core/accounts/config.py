"""External configuration loading, session JWT (user_id only), and OAuth state helpers.

Authentication is always enforced. Runtime role assignment via ``LOCAL_ADMINS``,
``ADMIN_USERS``, ``OAUTH_USER_INFO`` role claims, and JWT role claims is removed;
accounts and roles live solely in MySQL (``da_user`` / ``da_role`` / ``da_user_role``).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import inspect
import json
import logging
import os
import secrets
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

AUTH_CONFIG_ENV = "DATAAGENT_CONFIG"
SECRET_KEY_ENV = "DATAAGENT_SECRET_KEY"
DEFAULT_PASSWORD_ENV = "DATAAGENT_DEFAULT_USER_PASSWORD"

SECRET_KEY_PLACEHOLDER = "CHANGE-ME-generate-with-secrets.token_urlsafe(32)"
SECRET_KEY_MIN_BYTES = 32

LOCAL_PROVIDER = "local"

_STATE_TTL_SECONDS = 600
_MAX_OAUTH_PROVIDER_NAME_LENGTH = 64

OAUTH_STATE_COOKIE = "da_oauth_nonce"
OAUTH_STATE_COOKIE_MAX_AGE = _STATE_TTL_SECONDS


class AuthConfigError(RuntimeError):
    """External authentication configuration is invalid (fail-closed at startup)."""


@dataclass
class OAuthSettings:
    provider_name: str = "SSO"
    icon: str = ""
    client_id: str = ""
    client_secret: str = ""
    authorize_url: str = ""
    token_url: str = ""
    api_base_url: str = ""
    scopes: str = "openid profile"
    redirect_uri: str = ""
    response_type: str = "code"
    grant_type: str = "authorization_code"

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.authorize_url and self.token_url)


@dataclass
class AuthSettings:
    secret_key: str = ""
    default_user_password: str = ""
    auto_register_enabled: bool = True
    session_ttl_seconds: int = 28800
    cookie_name: str = "da_session"
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    oauth: OAuthSettings = field(default_factory=OAuthSettings)
    oauth_user_info: Any = None
    oauth_trusted_username_namespace: bool = False
    runtime_settings: dict[str, Any] = field(default_factory=dict)
    config_path: str = ""

    @property
    def enabled(self) -> bool:
        return True

    @property
    def local_login_enabled(self) -> bool:
        # Password login against da_user is always available.
        return True

    @property
    def oauth_login_enabled(self) -> bool:
        return self.oauth.configured


_settings: AuthSettings | None = None
_lock = threading.Lock()


def _validate_secret_key(secret_key: Any) -> str:
    if not isinstance(secret_key, str) or not secret_key:
        raise AuthConfigError("SECRET_KEY 不能为空（请在 DATAAGENT_CONFIG 或 DATAAGENT_SECRET_KEY 中配置）")
    if secret_key == SECRET_KEY_PLACEHOLDER:
        raise AuthConfigError(
            "SECRET_KEY 仍是示例占位值，请用 python -c \"import secrets; print(secrets.token_urlsafe(32))\" 生成"
        )
    if len(secret_key.encode("utf-8")) < SECRET_KEY_MIN_BYTES:
        raise AuthConfigError(f"SECRET_KEY 过短（HS256 弱密钥可伪造会话），至少 {SECRET_KEY_MIN_BYTES} 字节")
    return secret_key


def _parse_oauth_providers(raw: Any) -> OAuthSettings:
    if raw is None:
        return OAuthSettings()
    if not isinstance(raw, list):
        raise AuthConfigError("OAUTH_PROVIDERS 必须是 provider dict 列表")
    if not raw:
        return OAuthSettings()
    if len(raw) != 1:
        raise AuthConfigError("OAUTH_PROVIDERS 本期仅支持单 Provider，必须恰好配置一项")
    provider = raw[0]
    if not isinstance(provider, dict):
        raise AuthConfigError("OAUTH_PROVIDERS 条目必须是 dict")
    deprecated_fields = sorted(
        f for f in ("userinfo_url", "user_id_field", "username_field") if f in provider
    )
    if deprecated_fields:
        raise AuthConfigError(
            f"OAUTH_PROVIDERS 顶层字段已不支持: {deprecated_fields}；"
            "请迁移到 OAUTH_USER_INFO(provider, token_response, oauth_remotes) 自定义钩子"
        )
    remote_app = provider.get("remote_app")
    if remote_app is None:
        remote_app = {}
    if not isinstance(remote_app, dict):
        raise AuthConfigError("OAUTH_PROVIDERS.remote_app 必须是 dict")
    if "userinfo_endpoint" in remote_app:
        raise AuthConfigError(
            "OAUTH_PROVIDERS.remote_app.userinfo_endpoint 已不支持；"
            "请通过 OAUTH_USER_INFO 的 oauth_remotes[provider] 获取用户信息"
        )
    client_kwargs = remote_app.get("client_kwargs")
    if client_kwargs is None:
        client_kwargs = {}
    if not isinstance(client_kwargs, dict):
        raise AuthConfigError("OAUTH_PROVIDERS.remote_app.client_kwargs 必须是 dict")
    oauth = OAuthSettings(
        provider_name=str(provider.get("name") or "").strip(),
        icon=str(provider.get("icon") or "").strip(),
        client_id=str(remote_app.get("client_id") or ""),
        client_secret=str(remote_app.get("client_secret") or ""),
        authorize_url=str(remote_app.get("authorize_url") or ""),
        token_url=str(remote_app.get("access_token_url") or ""),
        api_base_url=str(remote_app.get("api_base_url") or ""),
        scopes=str(client_kwargs.get("scope") or "openid profile"),
        redirect_uri=str(remote_app.get("redirect_uri") or ""),
        response_type=str(remote_app.get("response_type") or "code"),
        grant_type=str(remote_app.get("grant_type") or "authorization_code"),
    )
    if not oauth.provider_name:
        raise AuthConfigError("OAUTH_PROVIDERS 条目缺少 name")
    if (
        len(oauth.provider_name) > _MAX_OAUTH_PROVIDER_NAME_LENGTH
        or oauth.provider_name.casefold() == LOCAL_PROVIDER
        or not oauth.provider_name[0].isalnum()
        or any(not (char.isalnum() or char in "._-") for char in oauth.provider_name)
    ):
        raise AuthConfigError(
            "OAUTH_PROVIDERS.name 必须是 1-64 位安全 Provider key："
            "以字母或数字开头，仅含字母、数字、'.'、'_'、'-'，且不能使用保留值 'local'"
        )
    if not oauth.configured:
        raise AuthConfigError(
            "OAUTH_PROVIDERS.remote_app 配置不完整：client_id / authorize_url / access_token_url 必须同时提供"
        )
    if oauth.response_type != "code":
        raise AuthConfigError("DataAgent 当前仅支持 OAuth 授权码模式，remote_app.response_type 必须为 'code'")
    if oauth.grant_type != "authorization_code":
        raise AuthConfigError(
            "DataAgent 当前仅支持 OAuth 授权码模式，remote_app.grant_type 必须为 'authorization_code'"
        )
    missing = [name for name in ("api_base_url", "redirect_uri") if not getattr(oauth, name)]
    if missing:
        raise AuthConfigError(f"OAUTH_PROVIDERS 配置不完整：缺少 OAuth 必需字段 {missing}")
    api_base = urlparse(oauth.api_base_url)
    if (
        api_base.scheme not in {"http", "https"}
        or not api_base.netloc
        or api_base.params
        or api_base.query
        or api_base.fragment
        or not oauth.api_base_url.endswith("/")
    ):
        raise AuthConfigError(
            "OAUTH_PROVIDERS.remote_app.api_base_url 必须是以 '/' 结尾的绝对 HTTP(S) URL"
        )
    redirect = urlparse(oauth.redirect_uri)
    expected_callback_path = f"/oauth-authorized/{oauth.provider_name}"
    if (
        redirect.scheme not in {"http", "https"}
        or not redirect.netloc
        or unquote(redirect.path) != expected_callback_path
        or redirect.params
        or redirect.query
        or redirect.fragment
    ):
        raise AuthConfigError(
            "OAUTH_PROVIDERS.remote_app.redirect_uri 必须是绝对 URL，且路径严格为 "
            f"{expected_callback_path!r}"
        )
    return oauth


def _build_settings(module: Any, config_path: str) -> AuthSettings:
    if hasattr(module, "AUTH_ENABLED") and getattr(module, "AUTH_ENABLED") is False:
        raise AuthConfigError(
            "AUTH_ENABLED=False 已移除：DataAgent 不再支持无认证共享池模式，请配置 SECRET_KEY 并启用登录"
        )
    if getattr(module, "OAUTH", None):
        raise AuthConfigError("OAUTH 扁平配置已不支持，请迁移为单项 OAUTH_PROVIDERS")
    if getattr(module, "OAUTH_USERINFO_MAPPER", None) is not None:
        raise AuthConfigError(
            "OAUTH_USERINFO_MAPPER 已不支持；请迁移为 OAUTH_USER_INFO(provider, token_response, oauth_remotes)"
        )
    if getattr(module, "LOCAL_ADMINS", None) or getattr(module, "ADMIN_USERS", None):
        logger.warning(
            "LOCAL_ADMINS / ADMIN_USERS are ignored at runtime; roles and passwords are managed in MySQL"
        )

    raw_secret = getattr(module, "SECRET_KEY", "") or os.environ.get(SECRET_KEY_ENV, "")
    raw_default_pw = getattr(module, "DEFAULT_USER_PASSWORD", "") or os.environ.get(DEFAULT_PASSWORD_ENV, "")
    auto_register = bool(getattr(module, "AUTO_REGISTER_ENABLED", True))

    settings = AuthSettings(
        secret_key=_validate_secret_key(raw_secret),
        default_user_password=str(raw_default_pw or "").strip(),
        auto_register_enabled=auto_register,
        session_ttl_seconds=int(getattr(module, "SESSION_TTL_SECONDS", 28800)),
        cookie_name=str(getattr(module, "COOKIE_NAME", "da_session") or "da_session"),
        cookie_secure=bool(getattr(module, "COOKIE_SECURE", False)),
        cookie_samesite=str(getattr(module, "COOKIE_SAMESITE", "lax") or "lax").lower(),
        oauth=_parse_oauth_providers(getattr(module, "OAUTH_PROVIDERS", None)),
        oauth_user_info=getattr(module, "OAUTH_USER_INFO", None),
        oauth_trusted_username_namespace=bool(getattr(module, "OAUTH_TRUSTED_USERNAME_NAMESPACE", False)),
        config_path=config_path,
    )
    if settings.oauth_user_info is not None and not callable(settings.oauth_user_info):
        raise AuthConfigError(
            "OAUTH_USER_INFO 必须是 callable(provider, token_response, oauth_remotes) -> dict"
        )
    if settings.oauth_user_info is not None and (
        inspect.iscoroutinefunction(settings.oauth_user_info)
        or inspect.iscoroutinefunction(getattr(settings.oauth_user_info, "__call__", None))
    ):
        raise AuthConfigError("OAUTH_USER_INFO 必须是同步 callable；DataAgent 会在线程中执行")
    if settings.oauth_user_info is not None:
        try:
            inspect.signature(settings.oauth_user_info).bind("provider", {}, {})
        except (TypeError, ValueError) as e:
            raise AuthConfigError(
                "OAUTH_USER_INFO 必须接受 (provider, token_response, oauth_remotes) 三个参数"
            ) from e
    if settings.oauth_login_enabled and settings.oauth_user_info is None:
        raise AuthConfigError(
            "OAUTH_PROVIDERS 已配置，但缺少 OAUTH_USER_INFO(provider, token_response, oauth_remotes)"
        )
    if settings.oauth_login_enabled and settings.auto_register_enabled and not settings.default_user_password:
        raise AuthConfigError(
            "启用 OAuth 自动注册时必须配置 DEFAULT_USER_PASSWORD（用于为新注册用户写入默认密码哈希）"
        )

    raw_runtime = getattr(module, "DATAAGENT_SETTINGS", None)
    if raw_runtime is not None:
        if not isinstance(raw_runtime, dict):
            raise AuthConfigError("DATAAGENT_SETTINGS 必须是 dict（config.py Settings 字段 → 值）")
        from config import Settings as RuntimeSettings

        known_fields = set(RuntimeSettings.model_fields)
        unknown = sorted(str(k) for k in raw_runtime if str(k) not in known_fields)
        if unknown:
            raise AuthConfigError(f"DATAAGENT_SETTINGS 含未知字段: {unknown}")
        settings.runtime_settings = {str(k): v for k, v in raw_runtime.items()}
    if settings.cookie_samesite not in {"lax", "strict", "none"}:
        raise AuthConfigError(f"COOKIE_SAMESITE 非法: {settings.cookie_samesite!r}")
    if settings.session_ttl_seconds <= 0:
        raise AuthConfigError("SESSION_TTL_SECONDS 必须为正数")
    return settings


def load_auth_settings(config_path: str | None = None) -> AuthSettings:
    """Fail-closed external config loader."""
    path = (config_path if config_path is not None else os.environ.get(AUTH_CONFIG_ENV, "")).strip()
    if not path:
        env_secret = os.environ.get(SECRET_KEY_ENV, "").strip()
        if not env_secret:
            raise AuthConfigError(
                f"未配置 {AUTH_CONFIG_ENV} 且未设置 {SECRET_KEY_ENV}；DataAgent 始终需要认证配置"
            )
        return AuthSettings(
            secret_key=_validate_secret_key(env_secret),
            default_user_password=os.environ.get(DEFAULT_PASSWORD_ENV, "").strip(),
            config_path="",
        )

    if not os.path.isfile(path) or not os.access(path, os.R_OK):
        raise AuthConfigError(f"{AUTH_CONFIG_ENV} 指向的配置文件不可读: {path}")

    config_dir = os.path.dirname(os.path.abspath(path))
    if config_dir not in sys.path:
        sys.path.insert(0, config_dir)

    try:
        spec = importlib.util.spec_from_file_location("dataagent_external_config", path)
        if spec is None or spec.loader is None:
            raise AuthConfigError(f"无法加载认证配置: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except AuthConfigError:
        raise
    except Exception as e:
        raise AuthConfigError(f"认证配置 import 失败: {path}: {e}") from e

    return _build_settings(module, path)


def init_auth(config_path: str | None = None) -> AuthSettings:
    """Initialize auth settings at startup (fail-closed)."""
    global _settings
    with _lock:
        _settings = load_auth_settings(config_path)
    return _settings


def set_auth_settings_for_tests(settings: AuthSettings) -> AuthSettings:
    global _settings
    with _lock:
        _settings = settings
    return _settings


def get_auth_settings() -> AuthSettings:
    global _settings
    if _settings is None:
        with _lock:
            if _settings is None:
                _settings = load_auth_settings()
    return _settings


def reset_auth_for_tests() -> None:
    global _settings
    with _lock:
        _settings = None


# ---------------------------------------------------------------------------
# Session JWT (HS256): stores integer user_id only (no role/name snapshots)
# ---------------------------------------------------------------------------

def issue_session_token(user_id: int, now: float | None = None) -> str:
    import jwt

    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise ValueError("user_id must be a positive integer")
    cfg = get_auth_settings()
    issued_at = int(now if now is not None else time.time())
    payload = {
        "sub": str(user_id),
        "uid": user_id,
        "iat": issued_at,
        "exp": issued_at + cfg.session_ttl_seconds,
    }
    return jwt.encode(payload, cfg.secret_key, algorithm="HS256")


def verify_session_token(token: str) -> int | None:
    """Decode a session JWT and return the integer user_id, or None if invalid."""
    import jwt

    if not token:
        return None
    try:
        cfg = get_auth_settings()
        payload = jwt.decode(token, cfg.secret_key, algorithms=["HS256"])
    except (jwt.PyJWTError, AuthConfigError):
        return None
    raw_uid = payload.get("uid")
    if isinstance(raw_uid, int) and not isinstance(raw_uid, bool) and raw_uid > 0:
        return raw_uid
    raw_sub = str(payload.get("sub") or "").strip()
    if raw_sub.isdigit():
        uid = int(raw_sub)
        return uid if uid > 0 else None
    return None


# ---------------------------------------------------------------------------
# OAuth state (HMAC + browser nonce cookie binding)
# ---------------------------------------------------------------------------

def generate_oauth_nonce() -> str:
    return secrets.token_urlsafe(16)


def _state_signature(payload: str) -> str:
    cfg = get_auth_settings()
    return hmac.new(cfg.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_oauth_state(
    redirect_path: str = "",
    nonce: str | None = None,
    provider: str = "",
) -> str:
    payload = json.dumps(
        {
            "nonce": nonce or generate_oauth_nonce(),
            "ts": int(time.time()),
            "redirect": redirect_path,
            "provider": provider,
        },
        separators=(",", ":"),
    )
    encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{encoded}.{_state_signature(encoded)}"


def verify_oauth_state_binding(payload: dict[str, Any] | None, cookie_nonce: str | None) -> bool:
    state_nonce = str((payload or {}).get("nonce") or "")
    cookie_value = str(cookie_nonce or "")
    return bool(state_nonce) and bool(cookie_value) and hmac.compare_digest(state_nonce, cookie_value)


def verify_oauth_state(state: str) -> dict[str, Any] | None:
    try:
        encoded, signature = str(state or "").rsplit(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _state_signature(encoded)):
        return None
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except Exception:
        return None
    if int(time.time()) - int(payload.get("ts") or 0) > _STATE_TTL_SECONDS:
        return None
    return payload


def sanitize_redirect_path(raw: Any, fallback: str = "/") -> str:
    value = str(raw or "").strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return fallback
    return value
