from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import core.accounts as accounts
from core.accounts import (
    AuthConfigError,
    init_auth,
    issue_oauth_state,
    issue_session_token,
    load_auth_settings,
    sanitize_redirect_path,
    verify_oauth_state,
    verify_oauth_state_binding,
    verify_session_token,
)

VALID_SECRET = "unit-test-secret-key-0123456789abcdef-0123456789"
VALID_OAUTH_USER_INFO = (
    "def _oauth_user_info(provider, token_response, oauth_remotes):\n"
    "    return {'sub': 'test-sub'}\n"
    "OAUTH_USER_INFO = _oauth_user_info\n"
)


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch):
    monkeypatch.delenv(accounts.AUTH_CONFIG_ENV, raising=False)
    monkeypatch.delenv(accounts.SECRET_KEY_ENV, raising=False)
    monkeypatch.delenv(accounts.DEFAULT_PASSWORD_ENV, raising=False)
    accounts.reset_auth_for_tests()
    saved_sys_path = list(sys.path)
    yield
    sys.path[:] = saved_sys_path
    sys.modules.pop("dataagent_config_docker", None)
    accounts.reset_auth_for_tests()


def write_config(tmp_path: Path, body: str) -> str:
    config_file = tmp_path / "auth_config.py"
    config_file.write_text(body, encoding="utf-8")
    return str(config_file)


def test_env_unset_and_no_secret_fails_closed():
    with pytest.raises(AuthConfigError):
        load_auth_settings()


def test_env_secret_key_loads_without_config_file(monkeypatch):
    monkeypatch.setenv(accounts.SECRET_KEY_ENV, VALID_SECRET)
    monkeypatch.setenv(accounts.DEFAULT_PASSWORD_ENV, "init-pass")
    cfg = init_auth()
    assert cfg.enabled is True
    assert cfg.secret_key == VALID_SECRET
    assert cfg.default_user_password == "init-pass"


def test_auth_disabled_flag_is_rejected_fail_closed(tmp_path):
    path = write_config(tmp_path, f"AUTH_ENABLED = False\nSECRET_KEY = {VALID_SECRET!r}\n")
    with pytest.raises(AuthConfigError, match="AUTH_ENABLED=False"):
        load_auth_settings(path)


def test_env_set_but_file_missing_or_broken_fails_startup(monkeypatch, tmp_path):
    monkeypatch.setenv(accounts.AUTH_CONFIG_ENV, str(tmp_path / "missing.py"))
    with pytest.raises(AuthConfigError):
        init_auth()

    bad = write_config(tmp_path, "this is not python !!!")
    monkeypatch.setenv(accounts.AUTH_CONFIG_ENV, bad)
    with pytest.raises(AuthConfigError):
        init_auth()


@pytest.mark.parametrize(
    "secret",
    ["", accounts.SECRET_KEY_PLACEHOLDER, "short-secret"],
    ids=["empty", "example-placeholder", "under-32-bytes"],
)
def test_weak_secret_key_fails_startup(tmp_path, secret):
    path = write_config(tmp_path, f"SECRET_KEY = {secret!r}\n")
    with pytest.raises(AuthConfigError):
        load_auth_settings(path)


def test_oauth_requires_user_info_and_default_password_when_auto_register_enabled(tmp_path):
    provider = {
        "name": "SSO",
        "remote_app": {
            "client_id": "cid",
            "client_secret": "csec",
            "authorize_url": "https://sso.example.com/authorize",
            "access_token_url": "https://sso.example.com/token",
            "api_base_url": "https://sso.example.com/api/",
            "redirect_uri": "https://app.example.com/oauth-authorized/SSO",
        },
    }
    # Missing OAUTH_USER_INFO -> fail
    p1 = write_config(tmp_path, f"SECRET_KEY = {VALID_SECRET!r}\nOAUTH_PROVIDERS = {[provider]!r}\n")
    with pytest.raises(AuthConfigError, match="OAUTH_USER_INFO"):
        load_auth_settings(p1)

    # Missing DEFAULT_USER_PASSWORD when auto_register=True -> fail
    p2 = write_config(
        tmp_path,
        f"SECRET_KEY = {VALID_SECRET!r}\nOAUTH_PROVIDERS = {[provider]!r}\n{VALID_OAUTH_USER_INFO}",
    )
    with pytest.raises(AuthConfigError, match="DEFAULT_USER_PASSWORD"):
        load_auth_settings(p2)

    # With DEFAULT_USER_PASSWORD -> passes
    p3 = write_config(
        tmp_path,
        f"SECRET_KEY = {VALID_SECRET!r}\nDEFAULT_USER_PASSWORD = 'init-123'\n"
        f"OAUTH_PROVIDERS = {[provider]!r}\n{VALID_OAUTH_USER_INFO}",
    )
    cfg = load_auth_settings(p3)
    assert cfg.oauth_login_enabled is True
    assert cfg.default_user_password == "init-123"


def test_runtime_settings_validation(tmp_path):
    bad = write_config(
        tmp_path,
        f"SECRET_KEY = {VALID_SECRET!r}\nDATAAGENT_SETTINGS = {{'unknown_field': 1}}\n",
    )
    with pytest.raises(AuthConfigError, match="未知字段"):
        load_auth_settings(bad)

    good = write_config(
        tmp_path,
        f"SECRET_KEY = {VALID_SECRET!r}\nDATAAGENT_SETTINGS = {{'agent_timeout_seconds': 420}}\n",
    )
    cfg = load_auth_settings(good)
    assert cfg.runtime_settings == {"agent_timeout_seconds": 420}


def test_session_jwt_carries_only_integer_user_id(monkeypatch, tmp_path):
    path = write_config(tmp_path, f"SECRET_KEY = {VALID_SECRET!r}\nSESSION_TTL_SECONDS = 3600\n")
    monkeypatch.setenv(accounts.AUTH_CONFIG_ENV, path)
    init_auth()

    token = issue_session_token(42)
    assert verify_session_token(token) == 42
    assert verify_session_token(issue_session_token(42, now=time.time() - 7200)) is None
    assert verify_session_token("not-a-jwt") is None
    with pytest.raises(ValueError):
        issue_session_token("local:alice")  # type: ignore[arg-type]


def test_oauth_state_binding_and_redirect_sanitization(monkeypatch, tmp_path):
    path = write_config(tmp_path, f"SECRET_KEY = {VALID_SECRET!r}\n")
    monkeypatch.setenv(accounts.AUTH_CONFIG_ENV, path)
    init_auth()

    state = issue_oauth_state("/chat", nonce="nonce-1", provider="SSO")
    parsed = verify_oauth_state(state)
    assert parsed is not None
    assert parsed["redirect"] == "/chat"
    assert verify_oauth_state_binding(parsed, "nonce-1") is True
    assert verify_oauth_state_binding(parsed, "wrong-nonce") is False
    assert verify_oauth_state(state + "tamper") is None

    assert sanitize_redirect_path("/chat?a=1") == "/chat?a=1"
    assert sanitize_redirect_path("//evil.example") == "/"
    assert sanitize_redirect_path("https://evil.example") == "/"
    assert sanitize_redirect_path("/\\evil") == "/"
