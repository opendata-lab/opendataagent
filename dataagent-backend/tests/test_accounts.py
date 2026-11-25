"""Comprehensive tests for unified user accounts, roles, grants, migration, and trusted Widget."""
from __future__ import annotations

import contextlib
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys

import pymysql
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

import main
from core import accounts
from core.accounts import (
    AccountDisabledError,
    AuthSettings,
    IdentityConflictError,
    ROLE_ADMIN,
    ROLE_USER,
    authenticate_password,
    hash_password,
    issue_session_token,
    link_or_register,
    reset_auth_for_tests,
    set_account_store_for_tests,
    set_auth_settings_for_tests,
    verify_password,
    verify_session_token,
)
from tests.account_test_helpers import (
    TEST_DEFAULT_PASSWORD,
    TEST_SECRET_KEY,
    bearer_headers,
    install_fake_accounts,
)


@pytest.fixture(autouse=True)
def _reset_accounts():
    reset_auth_for_tests()
    set_account_store_for_tests(None)
    yield
    reset_auth_for_tests()
    set_account_store_for_tests(None)


def test_argon2id_hashing():
    argon = hash_password("secret-123")
    assert argon.startswith("$argon2id$")
    assert verify_password("secret-123", argon) is True
    assert verify_password("wrong", argon) is False
    # An account without a password (external login only) never matches.
    assert verify_password("anything", "") is False


def test_password_login_and_disabled_user():
    store, admin, alice = install_fake_accounts()

    logged = authenticate_password("alice", "alice123")
    assert logged is not None
    assert logged.id == alice.id

    # Disabling immediately blocks password login and existing JWT sessions.
    store.update_user(alice.id, is_active=False)
    assert authenticate_password("alice", "alice123") is None

    token = issue_session_token(alice.id)
    assert verify_session_token(token) == alice.id
    client = TestClient(main.app)
    resp = client.get("/api/v1/nl2sql/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_jwt_contains_no_role_claims_and_role_revocation_is_immediate():
    import jwt

    store, admin, alice = install_fake_accounts()
    # Promote alice to admin, issue token, then revoke admin role.
    admin_rid = store.role_id_for(ROLE_ADMIN)
    user_rid = store.role_id_for(ROLE_USER)
    store.update_user(alice.id, role_ids=[admin_rid, user_rid])

    token = issue_session_token(alice.id)
    raw_claims = jwt.decode(token, TEST_SECRET_KEY, algorithms=["HS256"])
    assert "role" not in raw_claims
    assert "oauth_role" not in raw_claims
    assert raw_claims["uid"] == alice.id

    client = TestClient(main.app)
    assert client.get("/api/v1/dataagent/users", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    # Revoke admin role; the very next request with the same token is 403.
    store.update_user(alice.id, role_ids=[user_rid])
    assert client.get("/api/v1/dataagent/users", headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_link_or_register_auto_registration_and_conflict():
    store, admin, alice = install_fake_accounts()

    # 1. Brand new external user auto-registers with default password and 'user' role.
    bob = link_or_register("SSO", "u-bob", {"preferred_username": "bob", "display_name": "鲍勃"})
    assert bob.username == "bob"
    assert bob.display_name == "鲍勃"
    assert bob.role_keys == frozenset({ROLE_USER})
    # Default password works on main-site login immediately.
    assert authenticate_password("bob", TEST_DEFAULT_PASSWORD) is not None

    # 3. Unbound external identity colliding with existing username fails without trusted namespace.
    with pytest.raises(IdentityConflictError):
        link_or_register("SSO", "u-alice", {"preferred_username": "alice"}, trusted_username_namespace=False)

    # 4. With trusted_username_namespace=True, non-admin 'alice' is bound and reused.
    bound_alice = link_or_register("odw", "alice", {"username": "alice"}, trusted_username_namespace=True)
    assert bound_alice.id == alice.id

    # 5. The trusted host's "admin" is the platform admin (user decision 2026-11-24).
    assert link_or_register("odw", "admin", {"username": "admin"}, trusted_username_namespace=True).id == admin.id

    # 6. Disabled user is rejected on subsequent external login.
    store.update_user(bob.id, is_active=False)
    with pytest.raises(AccountDisabledError):
        link_or_register("SSO", "u-bob", {"preferred_username": "bob"})


def test_user_role_profile_and_grant_apis(monkeypatch):
    store, admin, alice = install_fake_accounts(valid_agent_ids={"agent_default", "agent_sales"})
    monkeypatch.setattr(
        "api.account_routes.list_agent_profiles",
        lambda: [
            {"agent_id": "agent_default", "name": "默认助手", "description": "", "visibility": {"mode": "all"}},
            {"agent_id": "agent_sales", "name": "销售助手", "description": "", "visibility": {"mode": "selected"}},
        ],
    )
    client = TestClient(main.app)

    # Create user via admin API (gets default password, user role; no DELETE user endpoint).
    created = client.post(
        "/api/v1/dataagent/users",
        json={"username": "dave", "display_name": "大卫"},
        headers=bearer_headers(admin.id),
    )
    assert created.status_code == 201
    dave_id = created.json()["id"]
    assert isinstance(dave_id, int)
    assert client.delete(f"/api/v1/dataagent/users/{dave_id}", headers=bearer_headers(admin.id)).status_code == 405

    # Dave logs in with default password, updates profile (email saved unverified), changes password.
    login_resp = client.post("/api/v1/nl2sql/auth/login", json={"username": "dave", "password": TEST_DEFAULT_PASSWORD})
    assert login_resp.status_code == 200
    assert login_resp.json()["data"]["user_id"] == dave_id
    client.cookies.clear()

    prof_upd = client.put(
        "/api/v1/dataagent/profile",
        json={"display_name": "大卫王"},
        headers=bearer_headers(dave_id),
    )
    assert prof_upd.status_code == 200
    assert prof_upd.json()["display_name"] == "大卫王"

    pw_bad = client.put(
        "/api/v1/dataagent/profile/password",
        json={"current_password": "wrong", "new_password": "new-password-999"},
        headers=bearer_headers(dave_id),
    )
    assert pw_bad.status_code == 400
    pw_ok = client.put(
        "/api/v1/dataagent/profile/password",
        json={"current_password": TEST_DEFAULT_PASSWORD, "new_password": "new-password-999"},
        headers=bearer_headers(dave_id),
    )
    assert pw_ok.status_code == 200
    assert authenticate_password("dave", TEST_DEFAULT_PASSWORD) is None
    assert authenticate_password("dave", "new-password-999") is not None

    # Admin resets dave's password back to current DEFAULT_USER_PASSWORD.
    reset_resp = client.post(
        f"/api/v1/dataagent/users/{dave_id}/reset-password",
        headers=bearer_headers(admin.id),
    )
    assert reset_resp.status_code == 200
    assert authenticate_password("dave", TEST_DEFAULT_PASSWORD) is not None

    # Custom role + agent grant shared between role API and workbench grant API.
    role_resp = client.post(
        "/api/v1/dataagent/roles",
        json={"role_key": "sales_analyst", "name": "销售分析", "description": "销售组"},
        headers=bearer_headers(admin.id),
    )
    assert role_resp.status_code == 201
    role_id = role_resp.json()["id"]
    assert role_resp.json()["permissions"] == []  # Custom roles have no static menu/admin permissions.

    # Built-in role cannot be deleted.
    admin_role_id = store.role_id_for(ROLE_ADMIN)
    assert client.delete(f"/api/v1/dataagent/roles/{admin_role_id}", headers=bearer_headers(admin.id)).status_code == 400

    # Assign dave to custom role & grant agent_sales to that role.
    assert client.put(
        f"/api/v1/dataagent/roles/{role_id}/users",
        json={"user_ids": [dave_id]},
        headers=bearer_headers(admin.id),
    ).status_code == 200
    assert client.put(
        f"/api/v1/dataagent/roles/{role_id}/agents",
        json={"agent_ids": ["agent_sales"]},
        headers=bearer_headers(admin.id),
    ).status_code == 200

    # Workbench grants endpoint reads the exact same da_agent_grant table.
    grants = client.get("/api/v1/dataagent/agents/agent_sales/grants", headers=bearer_headers(admin.id))
    assert grants.status_code == 200
    assert grants.json()["role_ids"] == [role_id]

    # Dave's profile now lists agent_sales as accessible; alice does not have it.
    dave_profile = client.get("/api/v1/dataagent/profile", headers=bearer_headers(dave_id)).json()
    alice_profile = client.get("/api/v1/dataagent/profile", headers=bearer_headers(alice.id)).json()
    assert [a["agent_id"] for a in dave_profile["accessible_agents"]] == ["agent_default", "agent_sales"]
    assert [a["agent_id"] for a in alice_profile["accessible_agents"]] == ["agent_default"]

    # Deleting the custom role cleans up its grants in the same transaction.
    assert client.delete(f"/api/v1/dataagent/roles/{role_id}", headers=bearer_headers(admin.id)).status_code == 200
    grants_after = client.get("/api/v1/dataagent/agents/agent_sales/grants", headers=bearer_headers(admin.id)).json()
    assert grants_after["role_ids"] == []


def _run_sql_script(conn, path):
    """Run a multi-statement SQL file the way the mysql client would."""
    sql = "\n".join(line for line in open(path, encoding="utf-8").read().splitlines() if not line.lstrip().startswith("--"))
    with conn.cursor() as cur:
        cur.execute(sql)
        while cur.nextset():
            pass


def test_real_mysql_cutover_script_and_concurrent_register(tmp_path, monkeypatch):
    """Alembic builds the schema only; scripts/migrate-accounts.sql moves the legacy data.

    Runs against a throwaway schema da_accounts_check on the local Docker MySQL.
    """
    try:
        root_conn = pymysql.connect(host="127.0.0.1", port=3326, user="root", password="root123", autocommit=True)
    except Exception:
        pytest.skip("Local Docker MySQL on 127.0.0.1:3326 not available")

    schema = "da_accounts_check"
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(os.path.dirname(backend_dir), "scripts", "migrate-accounts.sql")
    env = os.environ.copy()
    env.update(
        {
            "MYSQL_HOST": "127.0.0.1",
            "MYSQL_PORT": "3326",
            "MYSQL_USER": "root",
            "MYSQL_PASSWORD": "root123",
            "MYSQL_DATABASE": schema,
            "SESSION_MYSQL_DATABASE": schema,
        }
    )
    conn = None
    try:
        with root_conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {schema}")
            cur.execute(f"CREATE DATABASE {schema} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        root_conn.close()

        # 1. Legacy data before the cutover.
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "20261122_000041"], cwd=backend_dir, env=env, check=True)
        conn = pymysql.connect(host="127.0.0.1", port=3326, user="root", password="root123", database=schema,
                               cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                               client_flag=pymysql.constants.CLIENT.MULTI_STATEMENTS)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO da_agent_topic
                   (topic_id, title, chat_topic_id, chat_conversation_id, agent_id, source, website_id, external_user_id, visitor_id, auth_user_id, auth_username, is_agent_preview)
                   VALUES
                   ('t_admin', 'Admin', 'ct1', 'cc1', 'agent_default', 'web', '', '', '', 'local:admin', 'admin', 0),
                   ('t_zhang', 'Zhang', 'ct2', 'cc2', 'agent_default', 'web', '', '', '', 'SSO:u123', 'zhangsan', 0),
                   ('t_case', 'CaseDiff', 'ct3', 'cc3', 'agent_default', 'web', '', '', '', 'SSO:U123', 'zhangsan', 0),
                   ('t_shared_old', 'OldShared', 'ct4', 'cc4', 'agent_default', 'widget', 'ontofoundry', 'ontofoundry:ws1:s1', '', '', '', 0)
                """
            )
            cur.execute(
                """INSERT INTO da_mcp_access_token
                   (token_hash, token_prefix, user_id, display_name, name, created_at, oauth_role)
                   VALUES (%s, 'dak_old123', 'local:admin', 'admin', 'old', NOW(), 'admin')""",
                (hashlib.sha256(b"old-token").hexdigest(),),
            )
            cur.execute(
                "UPDATE da_agent_profile SET visibility_json = %s WHERE agent_id = 'agent_default'",
                (json.dumps({"mode": "selected", "allowed_users": ["SSO:u123", "SSO:lisi"], "allowed_groups": ["g1"]}),),
            )
            pub_blob = json.dumps({"agent_id": "agent_default", "visibility": {"mode": "selected", "allowed_users": ["SSO:u123"]}})
            draft_blob = json.dumps({"agent_id": "agent_default", "visibility": {"mode": "authenticated", "allowed_users": ["SSO:draft_only"]}})
            cur.execute(
                "INSERT INTO da_agent_draft (agent_id, draft_json, published_json, published_version) VALUES ('agent_default', %s, %s, 1)",
                (draft_blob, pub_blob),
            )

        # 2. Alembic: schema and built-in roles only; no legacy data is touched.
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=backend_dir, env=env, check=True)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM da_user")
            assert cur.fetchone()["n"] == 0
            cur.execute("SELECT role_key FROM da_role ORDER BY role_key")
            assert [r["role_key"] for r in cur.fetchall()] == ["admin", "user"]
            cur.execute("SELECT COUNT(*) AS n FROM da_mcp_access_token")
            assert cur.fetchone()["n"] == 0

        # 3. The admin may be created before or after the script; here before.
        from config import update_settings
        from core.accounts.store import AccountStore

        update_settings(
            {
                "mysql_host": "127.0.0.1",
                "mysql_port": 3326,
                "mysql_user": "root",
                "mysql_password": "root123",
                "session_mysql_database": schema,
            }
        )
        set_auth_settings_for_tests(
            AuthSettings(secret_key=TEST_SECRET_KEY, default_user_password=TEST_DEFAULT_PASSWORD)
        )
        real_store = AccountStore()
        set_account_store_for_tests(real_store)
        admin = accounts.ensure_local_user(username="admin", password="admin123", is_admin=True)

        # 4. The script, twice: it is idempotent.
        _run_sql_script(conn, script)
        _run_sql_script(conn, script)

        with conn.cursor() as cur:
            cur.execute("SELECT source, subject, user_id FROM da_user_identity")
            identity = {(r["source"], r["subject"]): int(r["user_id"]) for r in cur.fetchall()}
            # local:admin is the admin account created above.
            assert identity[("local", "admin")] == admin.id
            # SSO:u123 and SSO:U123 differ only in case: one user, one identity.
            zhang_keys = [k for k in identity if k[0] == "SSO" and k[1].lower() == "u123"]
            assert len(zhang_keys) == 1
            zhang_uid = identity[zhang_keys[0]]
            lisi_uid = identity[("SSO", "lisi")]
            assert len({admin.id, zhang_uid, lisi_uid}) == 3
            assert ("SSO", "draft_only") not in identity

            cur.execute("SELECT id, username, password_hash FROM da_user")
            users = {int(r["id"]): r for r in cur.fetchall()}
            # The old user name is kept.
            assert users[zhang_uid]["username"] == "zhangsan"
            assert users[lisi_uid]["username"] == "lisi"
            # No shared password before the owner signs in.
            assert users[lisi_uid]["password_hash"] == ""
            cur.execute("SELECT r.role_key FROM da_user_role ur JOIN da_role r ON r.id = ur.role_id WHERE ur.user_id = %s", (lisi_uid,))
            assert [r["role_key"] for r in cur.fetchall()] == ["user"]

            cur.execute("SELECT topic_id, user_id FROM da_agent_topic ORDER BY topic_id")
            topics = {r["topic_id"]: r["user_id"] for r in cur.fetchall()}
            assert topics == {"t_admin": admin.id, "t_zhang": zhang_uid, "t_case": zhang_uid, "t_shared_old": None}

            cur.execute("SELECT principal_id FROM da_agent_grant WHERE agent_id = 'agent_default' ORDER BY principal_id")
            assert [int(r["principal_id"]) for r in cur.fetchall()] == sorted([zhang_uid, lisi_uid])
            cur.execute("SELECT visibility_json FROM da_agent_profile WHERE agent_id = 'agent_default'")
            assert json.loads(cur.fetchone()["visibility_json"]) == {"mode": "selected"}
            cur.execute("SELECT draft_json, published_json FROM da_agent_draft WHERE agent_id = 'agent_default'")
            draft_row = cur.fetchone()
            assert json.loads(draft_row["draft_json"])["visibility"] == {"mode": "all"}
            assert json.loads(draft_row["published_json"])["visibility"] == {"mode": "selected"}

        # admin keeps the password set with create-user.
        logged_admin = authenticate_password("admin", "admin123")
        assert logged_admin is not None and logged_admin.is_admin

        # Concurrent first-time registration for the same (source, subject) creates exactly ONE account.
        def _register(_idx: int) -> int:
            u = link_or_register("odw", "concurrent_alice", {"username": "concurrent_alice", "display_name": "并发用户"})
            return u.id

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(_register, range(4)))
        assert len(set(ids)) == 1

        # The cutover is one-way: downgrade refuses and points at the backup restore.
        refused = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "20261122_000041"],
            cwd=backend_dir, env=env, capture_output=True, text=True,
        )
        assert refused.returncode != 0 and "restore-db.sh" in refused.stderr
    finally:
        with contextlib.suppress(Exception):
            conn.close()
        cleanup = pymysql.connect(host="127.0.0.1", port=3326, user="root", password="root123", autocommit=True)
        with cleanup.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {schema}")
        cleanup.close()


def test_agent_grants_independent_of_draft_save_and_publish(monkeypatch):
    """Unpublished agent grants survive draft save and draft publish; publish only writes visibility.mode."""
    import core.agent_profile_service as profile_svc
    from core.accounts import require_agent_access
    from main import app

    store, admin, alice = install_fake_accounts()
    # Configure grants directly via API for an unpublished draft agent
    draft_row = {
        "agent_id": "agent_unpub",
        "name": "未发布助手",
        "description": "",
        "system_prompt": "",
        "permission_mode": "inherit",
        "allowed_tools": ["Read"],
        "mcp_server_ids": [],
        "skill_folders": [],
        "max_turns": 0,
        "env_vars": {},
        "visibility": {"mode": "selected"},
        "revision": 1,
        "has_unpublished_changes": True,
        "published_version": 0,
    }
    store.valid_agent_ids = {"agent_unpub"}
    published_profiles: dict[str, dict] = {}
    monkeypatch.setattr(profile_svc, "get_agent_profile", lambda aid: published_profiles.get(aid))
    client = TestClient(app)
    admin_headers = bearer_headers(admin.id)

    # 1. Set grants before first publish
    resp = client.put(
        "/api/v1/dataagent/agents/agent_unpub/grants",
        headers=admin_headers,
        json={"user_ids": [alice.id], "role_ids": []},
    )
    assert resp.status_code == 200
    assert [u["id"] for u in resp.json()["users"]] == [alice.id]

    # 2. Simulate draft save & publish writing ONLY visibility: {"mode": "selected"}
    saved_payload = profile_svc.normalize_agent_profile_payload(
        {**draft_row, "visibility": {"mode": "selected", "allowed_users": ["legacy:ignored"], "allowed_groups": ["g1"]}},
        available_skill_folders=set(),
        available_mcp_server_ids=set(),
    )
    assert saved_payload["visibility"] == {"mode": "selected"}
    published_profiles["agent_unpub"] = {**draft_row, **saved_payload}

    # 3. Grants are intact after save & publish, and alice can now access the published agent
    grants_after = client.get("/api/v1/dataagent/agents/agent_unpub/grants", headers=admin_headers)
    assert grants_after.status_code == 200
    assert [u["id"] for u in grants_after.json()["users"]] == [alice.id]
    assert require_agent_access(alice.id, "agent_unpub")["agent_id"] == "agent_unpub"



def test_admin_role_cannot_be_granted_through_the_api():
    """Admins come only from the CLI (create-user --admin) or the cutover import."""
    store, admin, alice = install_fake_accounts()
    client = TestClient(main.app)
    headers = bearer_headers(admin.id)
    admin_role_id = next(r["id"] for r in store.list_roles() if r["role_key"] == "admin")
    user_role_id = next(r["id"] for r in store.list_roles() if r["role_key"] == "user")

    created = client.post(
        "/api/v1/dataagent/users",
        json={"username": "eve", "display_name": "Eve", "role_ids": [admin_role_id]},
        headers=headers,
    )
    assert created.status_code == 400
    promote = client.put(
        f"/api/v1/dataagent/users/{alice.id}", json={"role_ids": [admin_role_id, user_role_id]}, headers=headers
    )
    assert promote.status_code == 400
    via_role = client.put(
        f"/api/v1/dataagent/roles/{admin_role_id}/users", json={"user_ids": [admin.id, alice.id]}, headers=headers
    )
    assert via_role.status_code == 400
    assert not store.get_user(alice.id).is_admin

    # Keeping the existing admin in the role is fine; an admin is never reset to the default password.
    keep = client.put(f"/api/v1/dataagent/roles/{admin_role_id}/users", json={"user_ids": [admin.id]}, headers=headers)
    assert keep.status_code == 200
    assert client.post(f"/api/v1/dataagent/users/{admin.id}/reset-password", headers=headers).status_code == 400
    assert client.post(f"/api/v1/dataagent/users/{alice.id}/reset-password", headers=headers).status_code == 200


def test_draft_preview_runs_for_an_agent_manager_without_a_published_profile(monkeypatch):
    import core.agent_profile_service as profile_svc
    from core.accounts.models import AgentAccessDeniedError
    from core.task_coordinator import _verify_topic_execution_access

    store, admin, alice = install_fake_accounts()
    monkeypatch.setattr(profile_svc, "get_agent_profile", lambda aid: None)
    preview = {"user_id": admin.id, "agent_id": "agent_draft", "is_agent_preview": True}
    assert _verify_topic_execution_access(preview, "agent_draft") == admin.id

    with pytest.raises(AgentAccessDeniedError):
        _verify_topic_execution_access({**preview, "user_id": alice.id}, "agent_draft")
    with pytest.raises(AgentAccessDeniedError):
        _verify_topic_execution_access({**preview, "is_agent_preview": False}, "agent_draft")
