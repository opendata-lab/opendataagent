"""Server-side access keys and trusted account resolution for widget sites."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core.accounts import current_account_context, reset_auth_for_tests, set_account_store_for_tests
from core.accounts import service as account_service
from core.skill_admin_service import (
    _normalize_widget_allowed_sites,
    _preserve_access_key_hashes,
)
from tests.account_test_helpers import install_fake_accounts

DIGEST = hashlib.sha256(b"super-secret").hexdigest()


def sites(*items):
    return _normalize_widget_allowed_sites(list(items))


class TestNormalisation:
    def test_a_site_without_server_side_defaults_to_disabled(self):
        [site] = sites({"website_id": "web"})
        assert site["server_side"] == {
            "enabled": False,
            "access_key_hash": "",
            "trusted_username_namespace": False,
            "provider_mappings": {},
        }

    def test_server_side_survives_normalisation(self):
        [site] = sites({
            "website_id": "ontofoundry",
            "server_side": {
                "enabled": True,
                "access_key_hash": DIGEST,
                "trusted_username_namespace": True,
                "provider_mappings": {"sso": "SSO"},
            },
        })
        assert site["server_side"] == {
            "enabled": True,
            "access_key_hash": DIGEST,
            "trusted_username_namespace": True,
            "provider_mappings": {"sso": "SSO"},
        }

    def test_a_non_dict_server_side_is_ignored_rather_than_raising(self):
        [site] = sites({"website_id": "web", "server_side": "yes please"})
        assert site["server_side"]["enabled"] is False


class TestRoundTripPreservesTheKey:
    def test_saving_masked_settings_keeps_the_stored_digest(self):
        stored = sites({"website_id": "ontofoundry", "server_side": {"enabled": True, "access_key_hash": DIGEST}})
        incoming = sites({"website_id": "ontofoundry", "server_side": {"enabled": True}})
        _preserve_access_key_hashes(incoming, stored)
        assert incoming[0]["server_side"]["access_key_hash"] == DIGEST

    def test_an_unrelated_site_is_unaffected(self):
        stored = sites({"website_id": "ontofoundry", "server_side": {"enabled": True, "access_key_hash": DIGEST}})
        incoming = sites({"website_id": "web"})
        _preserve_access_key_hashes(incoming, stored)
        assert incoming[0]["server_side"]["access_key_hash"] == ""

    def test_an_explicit_digest_wins_so_rotation_is_possible(self):
        rotated = hashlib.sha256(b"rotated").hexdigest()
        stored = sites({"website_id": "ontofoundry", "server_side": {"enabled": True, "access_key_hash": DIGEST}})
        incoming = sites({"website_id": "ontofoundry", "server_side": {"enabled": True, "access_key_hash": rotated}})
        _preserve_access_key_hashes(incoming, stored)
        assert incoming[0]["server_side"]["access_key_hash"] == rotated


class TestRequestContext:
    @pytest.fixture(autouse=True)
    def _accounts(self):
        self.store, self.admin, self.alice = install_fake_accounts()
        yield
        reset_auth_for_tests()
        set_account_store_for_tests(None)

    @pytest.fixture
    def resolve(self, monkeypatch):
        def _resolve(site, headers):
            monkeypatch.setattr(account_service, "_allowed_widget_sites", lambda: [site])
            req = SimpleNamespace(
                headers=headers,
                cookies={},
                url=SimpleNamespace(scheme="http", netloc="testserver"),
                state=SimpleNamespace(),
            )
            return current_account_context(req)

        return _resolve

    @staticmethod
    def trusted_headers(**extra):
        return {
            "X-DataAgent-Client": "widget",
            "X-DataAgent-Website-Id": "ontofoundry",
            "X-DataAgent-User-Id": "local:bob",
            **extra,
        }

    def test_a_site_without_server_side_rejects_anonymous_host_id(self, resolve):
        with pytest.raises(HTTPException) as caught:
            resolve({"website_id": "ontofoundry", "allowed_origins": []}, self.trusted_headers())
        assert caught.value.status_code == 401

    def test_an_enabled_site_rejects_a_caller_with_no_key(self, resolve):
        with pytest.raises(HTTPException) as caught:
            resolve(
                {"website_id": "ontofoundry", "server_side": {"enabled": True, "access_key_hash": DIGEST}},
                self.trusted_headers(),
            )
        assert caught.value.status_code == 403

    def test_an_enabled_site_rejects_a_wrong_key(self, resolve):
        with pytest.raises(HTTPException) as caught:
            resolve(
                {"website_id": "ontofoundry", "server_side": {"enabled": True, "access_key_hash": DIGEST}},
                self.trusted_headers(**{"X-DataAgent-Access-Key": "not-it"}),
            )
        assert caught.value.status_code == 403

    def test_a_correct_key_passes_and_registers_the_forwarded_user(self, resolve):
        ctx = resolve(
            {"website_id": "ontofoundry", "server_side": {"enabled": True, "access_key_hash": DIGEST}},
            self.trusted_headers(**{"X-DataAgent-Access-Key": "super-secret"}),
        )
        assert ctx.source == "widget"
        assert ctx.website_id == "ontofoundry"
        assert ctx.user is not None and ctx.user.username == "bob"

    def test_a_correct_key_without_a_user_is_rejected(self, resolve):
        headers = self.trusted_headers(**{"X-DataAgent-Access-Key": "super-secret"})
        headers.pop("X-DataAgent-User-Id")
        with pytest.raises(HTTPException) as caught:
            resolve({"website_id": "ontofoundry", "server_side": {"enabled": True, "access_key_hash": DIGEST}}, headers)
        assert caught.value.status_code == 401

    def test_forwarded_widget_credentials_cannot_reach_platform_routes(self, resolve):
        # Bind the forwarded identity to the platform admin: the site key must still not
        # unlock account, admin, eval or MCP token routes (they depend on current_user).
        self.store.add_identity(self.admin.id, "ontofoundry", "boss")
        site = {"website_id": "ontofoundry", "server_side": {"enabled": True, "access_key_hash": DIGEST}}
        ctx = resolve(site, self.trusted_headers(**{"X-DataAgent-Access-Key": "super-secret", "X-DataAgent-User-Id": "boss"}))
        assert ctx.user is not None and ctx.user.is_admin
        req = SimpleNamespace(headers={}, cookies={}, state=SimpleNamespace(_account_context=ctx))
        with pytest.raises(HTTPException) as caught:
            account_service.current_user(req)
        assert caught.value.status_code == 403

    def test_a_correct_key_bypasses_the_origin_allowlist(self, resolve):
        ctx = resolve(
            {
                "website_id": "ontofoundry",
                "allowed_origins": ["https://somewhere.else"],
                "server_side": {"enabled": True, "access_key_hash": DIGEST},
            },
            self.trusted_headers(**{"X-DataAgent-Access-Key": "super-secret", "Origin": "https://not.on.the.list"}),
        )
        assert ctx.source == "widget"

    def test_an_enabled_site_with_no_stored_digest_rejects_everyone(self, resolve):
        with pytest.raises(HTTPException):
            resolve(
                {"website_id": "ontofoundry", "server_side": {"enabled": True, "access_key_hash": ""}},
                self.trusted_headers(**{"X-DataAgent-Access-Key": "anything"}),
            )


class TestRevocationActuallyRevokes:
    def test_a_disabled_site_does_not_get_its_old_digest_back(self):
        stored = sites({"website_id": "ontofoundry", "server_side": {"enabled": True, "access_key_hash": DIGEST}})
        revoked = sites({"website_id": "ontofoundry", "server_side": {"enabled": False, "access_key_hash": ""}})
        _preserve_access_key_hashes(revoked, stored)
        assert revoked[0]["server_side"]["access_key_hash"] == ""

    def test_re_enabling_after_revocation_does_not_resurrect_the_key(self):
        stored = sites({"website_id": "ontofoundry", "server_side": {"enabled": False, "access_key_hash": ""}})
        reenabled = sites({"website_id": "ontofoundry", "server_side": {"enabled": True, "access_key_hash": ""}})
        _preserve_access_key_hashes(reenabled, stored)
        assert reenabled[0]["server_side"]["access_key_hash"] == ""

    def test_an_enabled_site_still_keeps_its_digest_across_a_plain_save(self):
        stored = sites({"website_id": "ontofoundry", "server_side": {"enabled": True, "access_key_hash": DIGEST}})
        saved = sites({"website_id": "ontofoundry", "server_side": {"enabled": True, "access_key_hash": ""}})
        _preserve_access_key_hashes(saved, stored)
        assert saved[0]["server_side"]["access_key_hash"] == DIGEST
