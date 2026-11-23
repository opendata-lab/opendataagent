import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta
import hashlib
import secrets
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient
from starlette.responses import JSONResponse
import pytest

from api import admin_routes, remote_mcp
from core import auth, remote_mcp_policy, remote_mcp_tokens
from main import app

ORIGIN = "http://agent.example:8443"
OTHER_ORIGIN = "https://other.example"
MANAGEMENT = "/api/v1/dataagent/mcp-tokens"
MCP = f"{remote_mcp_policy.PREFIX}/a1/mcp"
DOWNLOAD = f"{remote_mcp_policy.PREFIX}/a1/threads/thread-1/files/output/report.csv"
IDENTITY = auth.AuthIdentity("example:Alice", "Alice", "user", "example")
ADMIN = auth.AuthIdentity("local:root", "Root", "admin", "local")
OTHER_USER = auth.AuthIdentity("example:alice", "Another user", "user", "example")
BROWSER_HEADERS = {"Origin": ORIGIN}


class TokenCursor(sqlite3.Cursor):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def execute(self, sql, parameters=()):
        if sql.startswith("SELECT GET_LOCK("):
            return super().execute("SELECT 1 AS acquired")
        parameters = tuple(value.isoformat(sep=" ") if isinstance(value, datetime) else value
                           for value in parameters)
        return super().execute(sql.replace("%s", "?"), parameters)


def token_row(cursor, values):
    return {column[0]: datetime.fromisoformat(value) if column[0].endswith("_at") and value else value
            for column, value in zip(cursor.description, values)}


@pytest.fixture
def token_db(tmp_path, monkeypatch):
    database = tmp_path / "mcp-tokens.db"
    clock = SimpleNamespace(now=datetime(2026, 11, 22, 12))
    statements = []

    @contextmanager
    def connect():
        connection = sqlite3.connect(database)
        connection.row_factory = token_row
        connection.set_trace_callback(statements.append)
        try:
            yield SimpleNamespace(cursor=lambda: connection.cursor(factory=TokenCursor), commit=connection.commit)
        finally:
            connection.close()

    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """CREATE TABLE da_mcp_access_token (
               id INTEGER PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, token_prefix TEXT NOT NULL,
               user_id TEXT NOT NULL, display_name TEXT NOT NULL, name TEXT NOT NULL,
               created_at TEXT NOT NULL, last_used_at TEXT, expires_at TEXT, revoked_at TEXT, oauth_role TEXT)"""
        )
        connection.commit()
    monkeypatch.setattr(remote_mcp_tokens, "_connect", connect)
    monkeypatch.setattr(remote_mcp_tokens, "_now", lambda: clock.now)
    return SimpleNamespace(connect=connect, clock=clock, statements=statements)


@pytest.fixture
def tokens_app(monkeypatch, token_db):
    settings = auth.AuthSettings(
        enabled=True, secret_key="test-key-" * 5, local_admins=[{"username": "root"}],
        oauth=auth.OAuthSettings(provider_name="example", client_id="web",
                                 authorize_url="https://sso.example/auth", token_url="https://sso.example/token"),
    )
    monkeypatch.setattr(auth, "get_auth_settings", lambda: settings)
    monkeypatch.setattr(remote_mcp_policy, "get_auth_settings", lambda: settings)
    monkeypatch.setattr(remote_mcp, "_require_agent_profile",
                        lambda agent_id, context: {"remote_mcp_enabled": True})
    server = remote_mcp.create_server()

    @asynccontextmanager
    async def lifespan(_):
        async with server.session_manager.run():
            yield

    monkeypatch.setattr(app.router, "lifespan_context", lifespan)
    monkeypatch.setattr(app.state, "remote_mcp", server, raising=False)
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        def login(identity):
            client.cookies.clear()
            if identity is not None:
                client.cookies.set(settings.cookie_name, auth.issue_session_token(identity))

        login(IDENTITY)
        yield SimpleNamespace(client=client, login=login, settings=settings, db=token_db)


def create(client, name="ZCode", **options):
    response = client.post(MANAGEMENT, headers=BROWSER_HEADERS, json={"name": name, **options})
    assert response.status_code == 201, response.text
    assert response.headers["Cache-Control"] == "no-store"
    return response.json()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def mcp_call(client, token, **headers):
    return client.post(
        MCP, headers={**bearer(token), "Accept": "application/json, text/event-stream", **headers},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )


def test_create_and_list_mine_or_all(tokens_app):
    client = tokens_app.client
    first = create(client, "内网客户端 ")
    item, token = first["item"], first["token"]
    assert set(first) == {"item", "token"}
    assert token.startswith("dak_") and len(token) == 47
    assert item == {
        "id": 1, "name": "内网客户端", "token_prefix": token[:10],
        "user_id": IDENTITY.user_id, "display_name": IDENTITY.display_name, "status": "active",
        "created_at": "2026-11-22T12:00:00Z", "expires_at": "2027-02-20T12:00:00Z",
        "last_used_at": None, "revoked_at": None,
    }
    with tokens_app.db.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT token_hash FROM da_mcp_access_token WHERE id = %s", (item["id"],))
        assert cursor.fetchone()["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    second = create(client, expires_in_days=None)["item"]
    assert second["expires_at"] is None
    tokens_app.login(OTHER_USER)
    other = create(client, expires_in_days=30)["item"]
    assert client.get(MANAGEMENT, headers=BROWSER_HEADERS).json() == {"items": [other]}
    assert client.get(f"{MANAGEMENT}?scope=all", headers=BROWSER_HEADERS).status_code == 403
    tokens_app.login(ADMIN)
    admin_item = create(client, expires_in_days=180, user_id=IDENTITY.user_id)["item"]
    assert admin_item["user_id"] == ADMIN.user_id
    assert client.get(f"{MANAGEMENT}?scope=mine", headers=BROWSER_HEADERS).json() == {"items": [admin_item]}
    assert client.get(f"{MANAGEMENT}?scope=all", headers=BROWSER_HEADERS).json() == {
        "items": [admin_item, other, second, item],
    }


def test_active_limit_frees_slots_after_expiry_and_revocation(tokens_app):
    client = tokens_app.client
    items = [create(client, f"token-{index}", expires_in_days=30)["item"] for index in range(10)]
    assert client.post(MANAGEMENT, headers=BROWSER_HEADERS, json={"name": "extra"}).status_code == 409
    assert client.delete(f"{MANAGEMENT}/{items[0]['id']}", headers=BROWSER_HEADERS).status_code == 200
    create(client, expires_in_days=None)
    assert client.post(MANAGEMENT, headers=BROWSER_HEADERS, json={"name": "extra"}).status_code == 409
    tokens_app.db.clock.now += timedelta(days=30)
    create(client)


@pytest.mark.parametrize("payload", [
    {}, {"name": ""}, {"name": " \t "}, {"name": "字" * 65}, {"name": 123},
    {"name": "client", "expires_in_days": 31}, {"name": "client", "expires_in_days": "90"},
    {"name": "client", "expires_in_days": 90.0}, {"name": "client", "expires_in_days": True},
])
def test_token_create_validates_request(tokens_app, payload):
    assert tokens_app.client.post(MANAGEMENT, headers=BROWSER_HEADERS, json=payload).status_code == 422


def test_management_requires_a_session_and_full_same_origin(tokens_app):
    client = tokens_app.client
    created = create(client)
    path = f"{MANAGEMENT}/{created['item']['id']}"
    calls = [("GET", MANAGEMENT), ("POST", MANAGEMENT), ("DELETE", path)]
    for method, url in calls:
        options = {"json": {"name": "client"}} if method == "POST" else {}
        assert client.request(method, url, **options).status_code == (200 if method == "GET" else 403)
        for origin in (OTHER_ORIGIN, "http://agent.example:8444", "https://agent.example:8443", "null"):
            assert client.request(method, url, headers={"Origin": origin}, **options).status_code == 403
    tokens_app.login(None)
    for method, url in calls:
        options = {"json": {"name": "client"}} if method == "POST" else {}
        headers = {**BROWSER_HEADERS, **bearer(created["token"])}
        assert client.request(method, url, headers=headers, **options).status_code == 401
        tokens_app.settings.enabled = False
        assert client.request(method, url, headers=headers, **options).status_code == 404
        tokens_app.settings.enabled = True
    assert client.get(f"{MANAGEMENT}?scope=someone", headers=BROWSER_HEADERS).status_code == 401
    tokens_app.login(IDENTITY)
    assert client.get(f"{MANAGEMENT}?scope=someone", headers=BROWSER_HEADERS).status_code == 422


def test_user_can_revoke_own_token_and_admin_can_revoke_others(tokens_app):
    client = tokens_app.client
    first = create(client)["item"]
    second = create(client)["item"]
    own_url = f"{MANAGEMENT}/{first['id']}"
    other_url = f"{MANAGEMENT}/{second['id']}"
    revoked = client.delete(own_url, headers=BROWSER_HEADERS).json()["item"]
    assert revoked["status"] == "revoked" and revoked["revoked_at"] == revoked["created_at"]
    tokens_app.db.clock.now += timedelta(minutes=2)
    repeated = client.delete(own_url, headers=BROWSER_HEADERS)
    assert repeated.status_code == 200 and repeated.json() == {"item": revoked}
    tokens_app.login(OTHER_USER)
    assert client.delete(other_url, headers=BROWSER_HEADERS).status_code == 404
    tokens_app.login(ADMIN)
    response = client.delete(other_url, headers=BROWSER_HEADERS)
    assert response.status_code == 200 and response.json()["item"]["status"] == "revoked"
    assert client.delete(f"{MANAGEMENT}/999", headers=BROWSER_HEADERS).status_code == 404


def test_lists_only_active_and_recently_invalid_tokens(tokens_app):
    client = tokens_app.client
    revoked = create(client, "revoked", expires_in_days=None)["item"]
    client.delete(f"{MANAGEMENT}/{revoked['id']}", headers=BROWSER_HEADERS)
    expired = create(client, "expired", expires_in_days=30)["item"]
    tokens_app.db.clock.now += timedelta(days=30, seconds=1)
    active = create(client, "active", expires_in_days=None)["item"]
    items = client.get(MANAGEMENT, headers=BROWSER_HEADERS).json()["items"]
    assert [(item["id"], item["status"]) for item in items] == [
        (active["id"], "active"), (expired["id"], "expired"),
    ]
    tokens_app.db.clock.now += timedelta(days=30)
    assert client.get(MANAGEMENT, headers=BROWSER_HEADERS).json() == {"items": [active]}


def test_mcp_and_download_accept_valid_bearer_and_reject_revocation(tokens_app, monkeypatch):
    client = tokens_app.client
    created = create(client)
    token = created["token"]
    topic = {"agent_id": "a1"}
    monkeypatch.setattr(remote_mcp, "get_topic_task_store", lambda: SimpleNamespace(get_topic=lambda *args, **kwargs: topic))
    monkeypatch.setattr(remote_mcp, "topic_file_response", lambda *args, **kwargs: JSONResponse({"file": "report"}))
    response = mcp_call(client, token)
    assert response.status_code == 200
    assert "ask" in {tool["name"] for tool in response.json()["result"]["tools"]}
    assert client.get(DOWNLOAD, headers=bearer(token)).json() == {"file": "report"}
    assert mcp_call(client, token, Origin=OTHER_ORIGIN).status_code == 403
    client.delete(f"{MANAGEMENT}/{created['item']['id']}", headers=BROWSER_HEADERS)
    for unauthorized in (mcp_call(client, token), client.get(DOWNLOAD, headers=bearer(token))):
        assert unauthorized.status_code == 401
        assert unauthorized.headers["WWW-Authenticate"] == 'Bearer realm="DataAgent MCP"'


def test_mcp_rejects_expired_unknown_and_session_tokens(tokens_app):
    client = tokens_app.client
    expiring = create(client, expires_in_days=30)["token"]
    tokens_app.db.clock.now += timedelta(days=30)
    unknown = "dak_" + secrets.token_urlsafe(32)
    for token in (expiring, unknown, auth.issue_session_token(IDENTITY), ""):
        response = mcp_call(client, token)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == 'Bearer realm="DataAgent MCP"'


def test_mcp_rejects_a_local_admin_removed_from_settings(tokens_app):
    tokens_app.login(ADMIN)
    token = create(tokens_app.client)["token"]
    assert mcp_call(tokens_app.client, token).status_code == 200
    tokens_app.settings.local_admins.clear()
    assert mcp_call(tokens_app.client, token).status_code == 401


def test_mcp_respects_web_role_and_current_configured_admins(tokens_app):
    tokens_app.settings.admin_users.append(IDENTITY.user_id)
    tokens_app.login(auth.AuthIdentity(IDENTITY.user_id, "Alice", "user", "example", oauth_role="user"))
    limited = create(tokens_app.client)["token"]
    assert not remote_mcp_policy.verify_identity(f"Bearer {limited}").is_admin

    tokens_app.login(auth.AuthIdentity(IDENTITY.user_id, "Alice", "admin", "example"))
    configured = create(tokens_app.client)["token"]
    assert remote_mcp_policy.verify_identity(f"Bearer {configured}").is_admin
    tokens_app.settings.admin_users.clear()
    assert not remote_mcp_policy.verify_identity(f"Bearer {configured}").is_admin

    tokens_app.login(auth.AuthIdentity(IDENTITY.user_id, "Alice", "admin", "example", oauth_role="admin"))
    mapped = create(tokens_app.client)["token"]
    assert remote_mcp_policy.verify_identity(f"Bearer {mapped}").is_admin


def test_mcp_throttles_last_used(tokens_app):
    client = tokens_app.client
    token = create(client)["token"]
    assert remote_mcp_policy.verify_identity(f"Bearer {token}") == IDENTITY
    tokens_app.db.clock.now += timedelta(seconds=59)
    assert not remote_mcp_policy.verify_identity(f"Bearer {token}").is_admin
    assert client.get(MANAGEMENT, headers=BROWSER_HEADERS).json()["items"][0]["last_used_at"] == "2026-11-22T12:00:00Z"
    updates = [statement for statement in tokens_app.db.statements if "SET last_used_at" in statement]
    assert len(updates) == 1
    tokens_app.db.clock.now += timedelta(seconds=1)
    assert mcp_call(client, token).status_code == 200
    assert client.get(MANAGEMENT, headers=BROWSER_HEADERS).json()["items"][0]["last_used_at"] == "2026-11-22T12:01:00Z"


def test_readable_assistant_exposes_only_its_enabled_mcp_path(tokens_app, monkeypatch):
    profile = {"agent_id": "a1", "name": "Assistant", "remote_mcp_enabled": True}
    monkeypatch.setattr(admin_routes, "get_agent_profile", lambda _: profile)
    url = "/api/v1/dataagent/agents/a1/profile"
    assert tokens_app.client.get(url).json()["remote_mcp_path"] == MCP
    profile["remote_mcp_enabled"] = False
    assert tokens_app.client.get(url).json()["remote_mcp_path"] == ""
    profile["visibility"] = {"mode": "selected", "allowed_users": ["example:another"]}
    assert tokens_app.client.get(url).status_code == 404
    assert tokens_app.client.put("/api/v1/dataagent/agents/a1/remote-mcp", json={"enabled": True}).status_code == 403


def test_remote_tools_have_direct_structured_output():
    tools = {item.name: item for item in remote_mcp.create_server()._tool_manager.list_tools()}
    assert set(tools) == {"ask", "list_threads", "get_thread", "get_run", "feedback"}
    for tool in tools.values():
        assert tool.fn_metadata.output_schema["type"] == "object"


def run(coro):
    return asyncio.run(coro)


ALICE = remote_mcp._context(SimpleNamespace(user_id="sso:alice", display_name="Alice", role="user"))


class FakeStore:
    """Topics owned by alice or bob; get_topic applies the caller's context like the real predicate."""

    def __init__(self):
        self.topics = {
            "t-alice": {"topic_id": "t-alice", "agent_id": "a1", "owner": "sso:alice", "title": "Sales",
                        "source": "web", "current_task_status": "finished"},
            "t-bob": {"topic_id": "t-bob", "agent_id": "a1", "owner": "sso:bob", "title": "Bob's",
                      "source": "remote", "current_task_status": "finished"},
        }
        self.tasks = {}

    def get_topic(self, topic_id, context=None):
        topic = self.topics.get(topic_id)
        return topic if topic and topic["owner"] == context["auth_user_id"] else None

    def create_topic(self, *, title, agent_snapshot, context):
        topic = {"topic_id": "t-new", "agent_id": "a1", "owner": context["auth_user_id"], "title": title,
                 "source": context["source"], "current_task_status": None}
        self.topics["t-new"] = topic
        return topic

    def find_task_by_request_key(self, key):
        return next((t for t in self.tasks.values() if t["request_key"] == key), None)

    def get_task(self, task_id):
        return self.tasks.get(task_id)

    def list_topic_messages_page(self, **kwargs):
        task = next(iter(self.tasks.values()))
        return {"total": 1, "items": [{"message_id": "m1", "task_id": task["task_id"], "sender_type": "assistant",
                                       "content": "42", "attachments": [{"name": "r.csv", "rel_path": "output/r.csv", "size": 3}]}]}


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    submitted = []

    async def submit(**kwargs):
        submitted.append(kwargs)
        fake.tasks["task-1"] = {"task_id": "task-1", "topic_id": kwargs["topic_id"], "prompt": kwargs["message_content"],
                                "request_key": kwargs["request_key"], "task_status": "finished"}
        return {"task_id": "task-1"}

    monkeypatch.setattr(remote_mcp, "get_topic_task_store", lambda: fake)
    monkeypatch.setattr(remote_mcp, "submit_message_task", submit)
    monkeypatch.setattr(remote_mcp, "_require_agent_profile", lambda agent_id, context: {"agent_id": agent_id})
    monkeypatch.setattr(remote_mcp, "build_agent_snapshot", lambda profile: profile)
    token = remote_mcp._caller.set(("a1", ALICE, "https://agent.example"))
    yield SimpleNamespace(fake=fake, submitted=submitted)
    remote_mcp._caller.reset(token)


def test_ask_starts_an_ordinary_non_interactive_task_and_retries_are_idempotent(store):
    first = run(remote_mcp.ask("本月销售额", "key-00000001"))
    assert first["status"] == "answered" and first["answer"] == "42" and first["thread_id"] == "t-new"
    assert first["files"][0]["download_url"] == "https://agent.example/api/v1/remote-agents/a1/threads/t-new/files/output/r.csv"
    assert store.fake.topics["t-new"]["source"] == "remote"
    assert store.submitted[0]["interactive"] is False
    again = run(remote_mcp.ask("本月销售额", "key-00000001"))
    assert again["run_id"] == "task-1" and len(store.submitted) == 1
    conflict = run(remote_mcp.ask("上月销售额", "key-00000001"))
    assert conflict["error"]["code"] == "request_conflict"


def test_threads_are_the_callers_own(store):
    assert (run(remote_mcp.ask("继续", "key-00000002", thread_id="t-alice")))["thread_id"] == "t-alice"
    assert (run(remote_mcp.ask("偷看", "key-00000003", thread_id="t-bob")))["error"]["code"] == "not_found"
    assert (run(remote_mcp.get_thread("t-bob")))["error"]["code"] == "not_found"


def test_a_busy_thread_is_reported_with_its_running_task(store):
    store.fake.topics["t-alice"].update(current_task_status="running", current_task_id="task-9")
    busy = run(remote_mcp.ask("继续", "key-00000004", thread_id="t-alice"))
    assert busy["error"]["code"] == "thread_busy" and busy["run_id"] == "task-9"
    assert store.submitted == []
