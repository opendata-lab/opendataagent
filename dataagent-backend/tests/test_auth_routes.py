from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import api.auth_routes as auth_routes
import core.accounts as accounts
from core.accounts import AuthSettings, OAuthSettings, ROLE_ADMIN, ROLE_USER
from main import app
from tests.account_test_helpers import TEST_DEFAULT_PASSWORD, TEST_SECRET_KEY, install_fake_accounts


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch):
    monkeypatch.delenv(accounts.AUTH_CONFIG_ENV, raising=False)
    accounts.reset_auth_for_tests()
    accounts.set_account_store_for_tests(None)
    yield
    accounts.reset_auth_for_tests()
    accounts.set_account_store_for_tests(None)


def _setup_oauth(*, trusted_username_namespace: bool = False):
    oauth = OAuthSettings(
        provider_name="SSO",
        icon="fa-github",
        client_id="cid",
        client_secret="secret",
        authorize_url="https://sso.example.com/authorize",
        token_url="https://sso.example.com/token",
        api_base_url="https://sso.example.com/api/",
        scopes="openid profile",
        redirect_uri="https://app.example.com/oauth-authorized/SSO",
    )
    settings = AuthSettings(
        secret_key=TEST_SECRET_KEY,
        default_user_password=TEST_DEFAULT_PASSWORD,
        oauth=oauth,
        oauth_user_info=lambda p, t, r: t.get("userinfo") or {"sub": "u-100"},
        oauth_trusted_username_namespace=trusted_username_namespace,
    )
    return install_fake_accounts(settings=settings)


def test_auth_config_always_enabled_and_exposes_oauth():
    _setup_oauth()
    resp = TestClient(app).get("/api/v1/nl2sql/auth/config")
    assert resp.status_code == 200
    assert resp.json() == {
        "enabled": True,
        "provider_name": "SSO",
        "provider_icon": "fa-github",
        "local_login_enabled": True,
        "oauth_login_enabled": True,
    }


def test_local_login_me_and_logout_roundtrip():
    store, admin, alice = install_fake_accounts()
    client = TestClient(app)

    bad = client.post("/api/v1/nl2sql/auth/login", json={"username": "admin", "password": "wrong"})
    assert bad.status_code == 401

    resp = client.post("/api/v1/nl2sql/auth/login", json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["user_id"] == admin.id
    assert data["username"] == "admin"
    assert data["is_admin"] is True
    assert "users:manage" in data["permissions"]
    assert "httponly" in resp.headers.get("set-cookie", "").lower()

    me = client.get("/api/v1/nl2sql/auth/me")
    assert me.status_code == 200
    assert me.json()["data"]["user_id"] == admin.id

    logout = client.post("/api/v1/nl2sql/auth/logout")
    assert logout.status_code == 200
    assert client.get("/api/v1/nl2sql/auth/me").status_code == 401


def test_oauth_callback_auto_registers_and_ignores_hook_role_claim(monkeypatch):
    store, admin, alice = _setup_oauth()

    async def fake_exchange(code: str) -> dict:
        return {
            "sub": "u-900",
            "preferred_username": "mallory",
            "display_name": "Mallory",
            "email": "mallory@example.com",
            "email_verified": True,
            # Legacy role claim MUST be ignored!
            "role": "admin",
        }

    monkeypatch.setattr(auth_routes, "_exchange_code_for_userinfo", fake_exchange)
    client = TestClient(app, follow_redirects=False)

    auth_resp = client.get("/api/v1/nl2sql/auth/oauth/authorize?redirect=/chat")
    assert auth_resp.status_code == 302
    state = parse_qs(urlparse(auth_resp.headers["location"]).query)["state"][0]

    cb = client.get(f"/oauth-authorized/SSO?code=abc&state={state}")
    assert cb.status_code == 302
    assert cb.headers["location"] == "/chat"

    me = client.get("/api/v1/nl2sql/auth/me")
    assert me.status_code == 200
    body = me.json()["data"]
    assert body["username"] == "mallory"
    assert body["display_name"] == "Mallory"
    assert body["is_admin"] is False
    assert [r["role_key"] for r in body["roles"]] == [ROLE_USER]


def test_oauth_callback_rejects_csrf_mismatch_conflict_and_disabled(monkeypatch):
    store, admin, alice = _setup_oauth(trusted_username_namespace=False)
    client = TestClient(app, follow_redirects=False)

    # 1. Missing nonce cookie -> 400
    state_only = accounts.issue_oauth_state("/chat", nonce="n1", provider="SSO")
    assert client.get(f"/oauth-authorized/SSO?code=x&state={state_only}").status_code == 400

    # 2. Username conflict with existing 'alice' -> redirects with identity_conflict
    async def conflict_exchange(code: str) -> dict:
        return {"sub": "u-alice", "preferred_username": "alice"}

    monkeypatch.setattr(auth_routes, "_exchange_code_for_userinfo", conflict_exchange)
    auth_resp = client.get("/api/v1/nl2sql/auth/oauth/authorize")
    state = parse_qs(urlparse(auth_resp.headers["location"]).query)["state"][0]
    cb = client.get(f"/oauth-authorized/SSO?code=x&state={state}")
    assert cb.status_code == 302
    assert cb.headers["location"] == "/login?error=identity_conflict"

    # 3. Disabled bound user -> redirects with account_disabled
    store.add_identity(alice.id, "SSO", "u-alice-bound")
    store.update_user(alice.id, is_active=False)

    async def disabled_exchange(code: str) -> dict:
        return {"sub": "u-alice-bound", "preferred_username": "alice"}

    monkeypatch.setattr(auth_routes, "_exchange_code_for_userinfo", disabled_exchange)
    auth_resp2 = client.get("/api/v1/nl2sql/auth/oauth/authorize")
    state2 = parse_qs(urlparse(auth_resp2.headers["location"]).query)["state"][0]
    cb2 = client.get(f"/oauth-authorized/SSO?code=x&state={state2}")
    assert cb2.status_code == 302
    assert cb2.headers["location"] == "/login?error=account_disabled"
