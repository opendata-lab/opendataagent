"""Widget site Origin allowlist and user-name resolution (no access key)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from core.accounts import current_account_context, reset_auth_for_tests, set_account_store_for_tests
from core.accounts import service as account_service
from core.skill_admin_service import _normalize_widget_allowed_sites
from main import app
from tests.account_test_helpers import bearer_headers, install_fake_accounts


def sites(*items):
    return _normalize_widget_allowed_sites(list(items))


class TestNormalisation:
    def test_a_site_without_origins_defaults_to_empty_list(self):
        [site] = sites({"website_id": "ontofoundry"})
        assert site == {
            "website_id": "ontofoundry",
            "allowed_origins": [],
        }

    def test_allowed_origins_are_deduplicated_and_trimmed(self):
        [site] = sites({
            "website_id": "portal",
            "allowed_origins": [" https://portal.example.com ", "https://portal.example.com", "", "  "],
        })
        assert site == {
            "website_id": "portal",
            "allowed_origins": ["https://portal.example.com"],
        }

    def test_legacy_server_side_and_display_fields_are_dropped(self):
        [site] = sites({
            "website_id": "web",
            "allowed_origins": ["https://a.example.com"],
            "server_side": {"enabled": True, "access_key_hash": "deadbeef"},
            "project_name": "Demo",
            "project_color": "#000000",
            "allow_anonymous": True,
        })
        assert site == {
            "website_id": "web",
            "allowed_origins": ["https://a.example.com"],
        }

    def test_non_list_allowed_origins_falls_back_to_empty_list(self):
        [site] = sites({"website_id": "web", "allowed_origins": "https://a.example.com"})
        assert site["allowed_origins"] == []


class TestOriginAllowlist:
    def test_absent_origin_is_allowed_for_server_side_callers(self):
        assert account_service._origin_allowed("", []) is True
        assert account_service._origin_allowed("", ["https://portal.example.com"]) is True

    def test_exact_origin_match_strips_trailing_slash(self):
        assert account_service._origin_allowed("https://portal.example.com/", ["https://portal.example.com"]) is True
        assert account_service._origin_allowed("https://portal.example.com", ["https://portal.example.com/"]) is True

    def test_wildcard_is_not_special(self):
        assert account_service._origin_allowed("https://anything.example.com", ["*"]) is False

    def test_disallowed_origin_is_rejected(self):
        assert account_service._origin_allowed("https://evil.example.com", ["https://portal.example.com"]) is False
        assert account_service._origin_allowed("https://portal.example.com", []) is False
        assert account_service._origin_allowed("null", ["https://portal.example.com"]) is False


class TestRequestContext:
    @pytest.fixture(autouse=True)
    def _accounts(self):
        self.store, self.admin, self.alice = install_fake_accounts()
        yield
        reset_auth_for_tests()
        set_account_store_for_tests(None)

    @pytest.fixture
    def resolve(self, monkeypatch):
        def _resolve(site_list, headers):
            monkeypatch.setattr(account_service, "_allowed_widget_sites", lambda: site_list)
            req = SimpleNamespace(
                headers=headers,
                cookies={},
                url=SimpleNamespace(scheme="http", netloc="testserver"),
                state=SimpleNamespace(),
            )
            return current_account_context(req)

        return _resolve

    @staticmethod
    def widget_headers(**extra):
        return {
            "X-DataAgent-Client": "widget",
            "X-DataAgent-Website-Id": "ontofoundry",
            "X-DataAgent-User-Id": "bob",
            **extra,
        }

    def test_unknown_site_is_rejected_403(self, resolve):
        with pytest.raises(HTTPException) as caught:
            resolve([{"website_id": "other", "allowed_origins": []}], self.widget_headers())
        assert caught.value.status_code == 403

    def test_missing_website_id_is_rejected_400(self, resolve):
        headers = self.widget_headers()
        headers.pop("X-DataAgent-Website-Id")
        with pytest.raises(HTTPException) as caught:
            resolve([{"website_id": "ontofoundry", "allowed_origins": []}], headers)
        assert caught.value.status_code == 400

    def test_server_side_caller_without_origin_passes_and_registers_user(self, resolve):
        ctx = resolve(
            [{"website_id": "ontofoundry", "allowed_origins": []}],
            self.widget_headers(),
        )
        assert ctx.source == "widget"
        assert ctx.website_id == "ontofoundry"
        assert ctx.auth_method == "widget"
        assert ctx.user is not None and ctx.user.username == "bob"

    def test_browser_caller_with_allowed_origin_binds_existing_user(self, resolve):
        site = {"website_id": "ontofoundry", "allowed_origins": ["https://host.example.com"]}
        ctx = resolve(
            [site],
            self.widget_headers(**{"Origin": "https://host.example.com", "X-DataAgent-User-Id": "alice"}),
        )
        assert ctx.user is not None and ctx.user.id == self.alice.id

    def test_browser_caller_with_disallowed_origin_is_rejected_403(self, resolve):
        site = {"website_id": "ontofoundry", "allowed_origins": ["https://host.example.com"]}
        with pytest.raises(HTTPException) as caught:
            resolve([site], self.widget_headers(**{"Origin": "https://other.example.com"}))
        assert caught.value.status_code == 403

    def test_missing_user_id_returns_401_widget_login_required(self, resolve):
        headers = self.widget_headers()
        headers.pop("X-DataAgent-User-Id")
        with pytest.raises(HTTPException) as caught:
            resolve([{"website_id": "ontofoundry", "allowed_origins": []}], headers)
        assert caught.value.status_code == 401
        assert caught.value.detail["code"] == "WIDGET_LOGIN_REQUIRED"

    @pytest.mark.parametrize("username", ["alice", "admin"])
    def test_widget_credentials_cannot_reach_platform_routes(self, resolve, username):
        site = {"website_id": "ontofoundry", "allowed_origins": ["https://host.example.com"]}
        ctx = resolve([site], self.widget_headers(**{"X-DataAgent-User-Id": username}))
        expected = self.admin if username == "admin" else self.alice
        assert ctx.user_id == expected.id
        req = SimpleNamespace(headers={}, cookies={}, state=SimpleNamespace(_account_context=ctx))
        with pytest.raises(HTTPException) as caught:
            account_service.current_user(req)
        assert caught.value.status_code == 403

    def test_access_key_endpoints_are_removed(self):
        client = TestClient(app)
        headers = bearer_headers(self.admin.id)
        assert client.post("/api/v1/nl2sql-admin/widget-sites/ontofoundry/access-key", headers=headers).status_code == 404
        assert client.delete("/api/v1/nl2sql-admin/widget-sites/ontofoundry/access-key", headers=headers).status_code == 404
