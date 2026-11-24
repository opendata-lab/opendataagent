from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.accounts import ROLE_ADMIN, ROLE_USER, reset_auth_for_tests, set_account_store_for_tests
from core.agent_visibility import (
    agent_visible_to,
    default_agent_visibility,
    filter_visible_agent_profiles,
    normalize_agent_visibility,
)
from tests.account_test_helpers import install_fake_accounts


@pytest.fixture(autouse=True)
def _reset():
    reset_auth_for_tests()
    set_account_store_for_tests(None)
    yield
    reset_auth_for_tests()
    set_account_store_for_tests(None)


def test_normalize_defaults_and_strips_legacy_lists():
    expected = {"mode": "all"}
    assert normalize_agent_visibility(None) == expected
    assert normalize_agent_visibility("") == expected
    assert normalize_agent_visibility({}) == expected
    assert default_agent_visibility() == expected
    assert normalize_agent_visibility(
        {"mode": "selected", "allowed_users": ["local:alice"], "allowed_groups": ["ops"]}
    ) == {"mode": "selected"}


def test_normalize_accepts_json_string_and_known_modes():
    assert normalize_agent_visibility('{"mode": "authenticated"}') == {"mode": "authenticated"}
    assert normalize_agent_visibility({"mode": " Selected "}) == {"mode": "selected"}


def test_normalize_tolerates_dirty_db_values_and_strict_rejects_invalid():
    assert normalize_agent_visibility("{not json") == {"mode": "all"}
    assert normalize_agent_visibility({"mode": "unknown"}) == {"mode": "all"}
    assert normalize_agent_visibility(["not", "a", "dict"]) == {"mode": "all"}

    with pytest.raises(ValueError, match="invalid visibility mode"):
        normalize_agent_visibility({"mode": "unknown"}, strict=True)
    with pytest.raises(ValueError, match="must be an object"):
        normalize_agent_visibility(["not", "a", "dict"], strict=True)


def _profile(agent_id: str, mode: str) -> dict:
    return {"agent_id": agent_id, "visibility": {"mode": mode}}


def test_visibility_matrix_with_user_and_role_grants():
    store, admin, alice = install_fake_accounts(
        valid_agent_ids={"a_all", "a_auth", "a_user", "a_role", "a_none"}
    )
    analyst = store.create_role(role_key="analyst", name="分析师")
    store.set_role_users(analyst["id"], [alice.id])
    store.set_agent_grants("a_user", user_ids=[alice.id])
    store.set_agent_grants("a_role", role_ids=[analyst["id"]])

    alice = store.get_user(alice.id)
    assert alice is not None

    profiles = [
        _profile("a_all", "all"),
        _profile("a_auth", "authenticated"),
        _profile("a_user", "selected"),
        _profile("a_role", "selected"),
        _profile("a_none", "selected"),
    ]

    # Anonymous / None sees nothing.
    assert filter_visible_agent_profiles(profiles, None) == []
    assert agent_visible_to(profiles[0], None) is False

    # Ordinary user sees all, authenticated, direct user grant, and role grant.
    visible_alice = filter_visible_agent_profiles(profiles, alice)
    assert [p["agent_id"] for p in visible_alice] == ["a_all", "a_auth", "a_user", "a_role"]

    # Admin sees every agent regardless of mode or grants.
    visible_admin = filter_visible_agent_profiles(profiles, admin)
    assert len(visible_admin) == 5

    # Disabled user sees nothing.
    store.update_user(alice.id, is_active=False)
    disabled_alice = store.get_user(alice.id)
    assert filter_visible_agent_profiles(profiles, disabled_alice) == []
