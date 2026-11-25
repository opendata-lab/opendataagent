import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta
import hashlib
import json
import secrets
import sqlite3
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient
from mcp.types import CallToolResult
from starlette.responses import JSONResponse
import pytest

from api import admin_routes, remote_mcp
from core import accounts, remote_mcp_policy, remote_mcp_tokens
from core.accounts import ROLE_ADMIN, ROLE_USER, hash_password
from main import app
from tests.account_test_helpers import TEST_DEFAULT_PASSWORD, TEST_SECRET_KEY, FakeAccountStore

ORIGIN = "http://agent.example:8443"
OTHER_ORIGIN = "https://other.example"
MANAGEMENT = "/api/v1/dataagent/mcp-tokens"
MCP = f"{remote_mcp_policy.PREFIX}/a1/mcp"
DOWNLOAD = f"{remote_mcp_policy.PREFIX}/a1/threads/thread-1/files/output/report.csv"
BROWSER_HEADERS = {"Origin": ORIGIN}


class TokenCursor(sqlite3.Cursor):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def execute(self, sql, parameters=()):
        if sql.startswith("SELECT GET_LOCK("):
            return super().execute("SELECT 1 AS acquired")
        parameters = tuple(
            value.isoformat(sep=" ") if isinstance(value, datetime) else value
            for value in parameters
        )
        return super().execute(sql.replace("%s", "?"), parameters)


def token_row(cursor, values):
    return {
        column[0]: datetime.fromisoformat(value) if column[0].endswith("_at") and value else value
        for column, value in zip(cursor.description, values)
    }


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
            """CREATE TABLE da_user (
               id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL)"""
        )
        cursor.execute(
            """CREATE TABLE da_mcp_access_token (
               id INTEGER PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, token_prefix TEXT NOT NULL,
               user_id INTEGER NOT NULL, name TEXT NOT NULL,
               created_at TEXT NOT NULL, last_used_at TEXT, expires_at TEXT, revoked_at TEXT)"""
        )
        connection.commit()
    monkeypatch.setattr(remote_mcp_tokens, "_connect", connect)
    monkeypatch.setattr(remote_mcp_tokens, "_now", lambda: clock.now)
    return SimpleNamespace(connect=connect, clock=clock, statements=statements)


@pytest.fixture
def tokens_app(monkeypatch, token_db):
    settings = accounts.AuthSettings(
        secret_key=TEST_SECRET_KEY,
        default_user_password=TEST_DEFAULT_PASSWORD,
    )
    accounts.set_auth_settings_for_tests(settings)
    acct_store = FakeAccountStore(valid_agent_ids={"a1", "a2"})
    alice = acct_store.create_user(username="alice", display_name="Alice", password_hash=hash_password("p1"), role_keys=[ROLE_USER])
    admin = acct_store.create_user(username="root", display_name="Root", password_hash=hash_password("p2"), role_keys=[ROLE_ADMIN, ROLE_USER])
    other = acct_store.create_user(username="bob", display_name="Another user", password_hash=hash_password("p3"), role_keys=[ROLE_USER])
    accounts.set_account_store_for_tests(acct_store)

    with token_db.connect() as connection, connection.cursor() as cursor:
        for u in (alice, admin, other):
            cursor.execute("INSERT INTO da_user (id, username, display_name) VALUES (%s, %s, %s)", (u.id, u.username, u.display_name))
        connection.commit()

    monkeypatch.setattr(
        remote_mcp,
        "require_agent_access",
        lambda user_id, agent_id: {"agent_id": agent_id, "remote_mcp_enabled": True},
    )
    server = remote_mcp.create_server()

    @asynccontextmanager
    async def lifespan(_):
        async with server.session_manager.run():
            yield

    monkeypatch.setattr(app.router, "lifespan_context", lifespan)
    monkeypatch.setattr(app.state, "remote_mcp", server, raising=False)
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        def login(user):
            client.cookies.clear()
            if user is not None:
                client.cookies.set(settings.cookie_name, accounts.issue_session_token(user.id))

        login(alice)
        yield SimpleNamespace(
            client=client,
            login=login,
            settings=settings,
            db=token_db,
            acct_store=acct_store,
            alice=alice,
            admin=admin,
            other=other,
        )
    accounts.reset_auth_for_tests()
    accounts.set_account_store_for_tests(None)


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


def rpc_messages(response):
    assert response.headers["content-type"].startswith("text/event-stream")
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data: {")]


def test_create_and_list_own_tokens(tokens_app):
    client = tokens_app.client
    first = create(client, "内网客户端 ")
    item, token = first["item"], first["token"]
    assert set(first) == {"item", "token"}
    assert token.startswith("dak_") and len(token) == 47
    # Tokens never expire (user decision 2026-11-24).
    assert item == {
        "id": 1, "name": "内网客户端", "token_prefix": token[:10],
        "user_id": tokens_app.alice.id, "status": "active",
        "created_at": "2026-11-22T12:00:00Z", "last_used_at": None, "revoked_at": None,
    }
    with tokens_app.db.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT token_hash FROM da_mcp_access_token WHERE id = %s", (item["id"],))
        assert cursor.fetchone()["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    second = create(client)["item"]
    tokens_app.login(tokens_app.other)
    other = create(client)["item"]
    assert client.get(MANAGEMENT, headers=BROWSER_HEADERS).json() == {"items": [other]}
    # Administrators manage only their own tokens too.
    tokens_app.login(tokens_app.admin)
    admin_item = create(client, user_id=tokens_app.alice.id)["item"]
    assert admin_item["user_id"] == tokens_app.admin.id
    assert client.get(MANAGEMENT, headers=BROWSER_HEADERS).json() == {"items": [admin_item]}
    tokens_app.login(tokens_app.alice)
    assert client.get(MANAGEMENT, headers=BROWSER_HEADERS).json() == {"items": [second, item]}


def test_no_cap_on_tokens(tokens_app):
    client = tokens_app.client
    for index in range(12):
        create(client, f"token-{index}")
    assert len(client.get(MANAGEMENT, headers=BROWSER_HEADERS).json()["items"]) == 12


@pytest.mark.parametrize("payload", [{}, {"name": ""}, {"name": " \t "}, {"name": "字" * 65}, {"name": 123}])
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


def test_users_revoke_only_their_own_tokens(tokens_app):
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
    tokens_app.login(tokens_app.other)
    assert client.delete(other_url, headers=BROWSER_HEADERS).status_code == 404
    tokens_app.login(tokens_app.admin)
    assert client.delete(other_url, headers=BROWSER_HEADERS).status_code == 404
    assert client.delete(f"{MANAGEMENT}/999", headers=BROWSER_HEADERS).status_code == 404


def test_lists_active_and_recently_revoked_tokens(tokens_app):
    client = tokens_app.client
    revoked = create(client, "revoked")["item"]
    client.delete(f"{MANAGEMENT}/{revoked['id']}", headers=BROWSER_HEADERS)
    active = create(client, "active")["item"]
    items = client.get(MANAGEMENT, headers=BROWSER_HEADERS).json()["items"]
    assert [(item["id"], item["status"]) for item in items] == [(active["id"], "active"), (revoked["id"], "revoked")]
    tokens_app.db.clock.now += timedelta(days=30, seconds=1)
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
    assert "ask" in {tool["name"] for tool in rpc_messages(response)[-1]["result"]["tools"]}
    assert client.get(DOWNLOAD, headers=bearer(token)).json() == {"file": "report"}
    wrong_origin = mcp_call(client, token, Origin=OTHER_ORIGIN)
    assert wrong_origin.status_code == 403
    assert "重新复制" in wrong_origin.json()["detail"]
    client.delete(f"{MANAGEMENT}/{created['item']['id']}", headers=BROWSER_HEADERS)
    for unauthorized in (mcp_call(client, token), client.get(DOWNLOAD, headers=bearer(token))):
        assert unauthorized.status_code == 401
        assert unauthorized.headers["WWW-Authenticate"] == 'Bearer realm="DataAgent MCP"'


def test_browser_download_requires_the_owner_session_and_preserves_bearer_checks(tokens_app, monkeypatch):
    client = tokens_app.client
    issued = create(client)
    token = issued["token"]
    topic = {"agent_id": "a1"}
    monkeypatch.setattr(remote_mcp, "get_topic_task_store", lambda: SimpleNamespace(
        get_topic=lambda _id, context: topic if context["user_id"] == tokens_app.alice.id else None,
    ))
    monkeypatch.setattr(remote_mcp, "topic_file_response", lambda *_args, **_kwargs: JSONResponse({"file": "report"}))

    assert client.get(DOWNLOAD).json() == {"file": "report"}
    assert client.get(DOWNLOAD, headers={"Origin": OTHER_ORIGIN}).status_code == 403
    assert client.get(DOWNLOAD.replace("/a1/", "/a2/")).status_code == 404
    assert mcp_call(client, "").status_code == 401
    assert client.post(MCP, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401

    for user in (tokens_app.other, tokens_app.admin):
        tokens_app.login(user)
        assert client.get(DOWNLOAD).status_code == 404

    tokens_app.login(None)
    assert client.get(DOWNLOAD).status_code == 401
    assert client.get(DOWNLOAD, headers=bearer(token)).json() == {"file": "report"}

    tokens_app.login(tokens_app.alice)
    assert client.delete(f"{MANAGEMENT}/{issued['item']['id']}", headers=BROWSER_HEADERS).status_code == 200
    assert client.get(DOWNLOAD, headers=bearer(token)).status_code == 401
    assert client.get(DOWNLOAD).json() == {"file": "report"}


def test_mcp_rejects_unknown_and_session_tokens(tokens_app):
    client = tokens_app.client
    unknown = "dak_" + secrets.token_urlsafe(32)
    for token in (unknown, accounts.issue_session_token(tokens_app.alice.id), ""):
        response = mcp_call(client, token)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == 'Bearer realm="DataAgent MCP"'
        assert "更新客户端配置" in response.json()["detail"]


def test_mcp_rejects_disabled_user_and_reflects_live_role_changes(tokens_app):
    client = tokens_app.client
    token = create(client)["token"]
    assert accounts.authenticate_mcp_token(f"Bearer {token}").is_admin is False

    # Promote alice to admin -> live token reflects admin immediately.
    admin_rid = tokens_app.acct_store.role_id_for(ROLE_ADMIN)
    user_rid = tokens_app.acct_store.role_id_for(ROLE_USER)
    tokens_app.acct_store.update_user(tokens_app.alice.id, role_ids=[admin_rid, user_rid])
    assert accounts.authenticate_mcp_token(f"Bearer {token}").is_admin is True

    # Revoke admin -> immediate demotion.
    tokens_app.acct_store.update_user(tokens_app.alice.id, role_ids=[user_rid])
    assert accounts.authenticate_mcp_token(f"Bearer {token}").is_admin is False

    # Disable user -> token immediately returns 401.
    tokens_app.acct_store.update_user(tokens_app.alice.id, is_active=False)
    assert mcp_call(client, token).status_code == 401


def test_mcp_throttles_last_used(tokens_app):
    client = tokens_app.client
    token = create(client)["token"]
    assert accounts.authenticate_mcp_token(f"Bearer {token}").id == tokens_app.alice.id
    tokens_app.db.clock.now += timedelta(seconds=59)
    assert accounts.authenticate_mcp_token(f"Bearer {token}").id == tokens_app.alice.id
    assert client.get(MANAGEMENT, headers=BROWSER_HEADERS).json()["items"][0]["last_used_at"] == "2026-11-22T12:00:00Z"
    updates = [statement for statement in tokens_app.db.statements if "SET last_used_at" in statement]
    assert len(updates) == 1
    tokens_app.db.clock.now += timedelta(seconds=1)
    assert mcp_call(client, token).status_code == 200
    assert client.get(MANAGEMENT, headers=BROWSER_HEADERS).json()["items"][0]["last_used_at"] == "2026-11-22T12:01:00Z"


def test_readable_assistant_exposes_only_its_enabled_mcp_path(tokens_app, monkeypatch):
    profile = {"agent_id": "a1", "name": "Assistant", "remote_mcp_enabled": True, "visibility": {"mode": "all"}}
    monkeypatch.setattr("core.agent_profile_service.get_agent_profile", lambda _: profile)
    url = "/api/v1/dataagent/agents/a1/profile"
    assert tokens_app.client.get(url).json()["remote_mcp_path"] == MCP
    profile["remote_mcp_enabled"] = False
    assert tokens_app.client.get(url).json()["remote_mcp_path"] == ""
    profile["visibility"] = {"mode": "selected"}
    assert tokens_app.client.get(url).status_code == 404
    assert tokens_app.client.put("/api/v1/dataagent/agents/a1/remote-mcp", json={"enabled": True}).status_code == 403


def test_foreground_ask_returns_the_final_result_on_its_http_stream(tokens_app, monkeypatch):
    store = SimpleNamespace(
        find_task_by_request_key=lambda _key: {"task_id": "task-1", "topic_id": "thread-1", "prompt": "销售额"},
        get_task=lambda _id, context: {"task_id": "task-1", "topic_id": "thread-1", "agent_id": "a1",
                                       "task_status": "finished"},
        list_topic_messages_page=lambda **_kwargs: {"items": [
            {"task_id": "task-1", "sender_type": "assistant", "message_id": "m1", "content": "42"},
        ]},
    )
    monkeypatch.setattr(remote_mcp, "get_topic_task_store", lambda: store)
    token = create(tokens_app.client)["token"]
    response = tokens_app.client.post(
        MCP, headers={**bearer(token), "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": "ask", "arguments": {"question": "销售额", "request_key": "key-sse-test"}}},
    )
    messages = rpc_messages(response)
    assert len(messages) == 1 and messages[0]["id"] == 4
    assert messages[0]["result"]["structuredContent"]["answer"] == "42"


@pytest.mark.parametrize(
    ("method", "arguments", "code", "next_step"),
    [
        ("ask", {"question": "   ", "request_key": "valid-key"}, "invalid_question", "输入要查询的问题"),
        ("get_thread", {"thread_id": "missing"}, "not_found", "会话列表"),
        ("get_run", {"run_id": "missing"}, "not_found", "任务编号"),
        ("feedback", {"thread_id": "t-alice", "message_id": "missing", "rating": "like"},
         "not_found", "助手回答重新选择"),
    ],
)
def test_tool_errors_are_structured_and_actionable(tokens_app, monkeypatch, method, arguments, code, next_step):
    store = SimpleNamespace(
        get_topic=lambda topic_id, **_kwargs: {"agent_id": "a1"} if topic_id == "t-alice" else None,
        get_task=lambda *_args, **_kwargs: None,
        update_message_feedback=lambda **_kwargs: False,
    )
    monkeypatch.setattr(remote_mcp, "get_topic_task_store", lambda: store)
    token = create(tokens_app.client)["token"]
    response = tokens_app.client.post(
        MCP, headers={**bearer(token), "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 5, "method": "tools/call",
              "params": {"name": method, "arguments": arguments}},
    )
    result = rpc_messages(response)[-1]["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == code
    assert next_step in result["structuredContent"]["error"]["next_step"]
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


def test_tool_does_not_expose_unexpected_backend_exception(tokens_app, monkeypatch):
    def unavailable(**_kwargs):
        raise RuntimeError("internal connection diagnostic")

    monkeypatch.setattr(remote_mcp, "get_topic_task_store", lambda: SimpleNamespace(list_topics=unavailable))
    token = create(tokens_app.client)["token"]
    response = tokens_app.client.post(
        MCP, headers={**bearer(token), "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 6, "method": "tools/call",
              "params": {"name": "list_threads", "arguments": {}}},
    )
    result = rpc_messages(response)[-1]["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "service_error"
    assert "管理员" in result["content"][0]["text"]
    assert "internal connection diagnostic" not in response.text


def test_remote_tools_have_direct_structured_output():
    tools = {item.name: item for item in remote_mcp.create_server()._tool_manager.list_tools()}
    assert set(tools) == {"ask", "list_threads", "get_thread", "get_run", "feedback"}
    properties = tools["ask"].parameters["properties"]
    assert "ctx" not in properties
    assert properties["background"]["default"] is False
    for tool in tools.values():
        assert tool.fn_metadata.output_schema["type"] == "object"


def run(coro):
    result = asyncio.run(coro)
    assert isinstance(result, CallToolResult)
    return result.structuredContent


ALICE_CTX = {"source": "remote", "website_id": "", "user_id": 10, "is_admin": False}


class FakeStore:
    def __init__(self):
        self.topics = {
            "t-alice": {"topic_id": "t-alice", "agent_id": "a1", "user_id": 10, "title": "Sales",
                        "source": "web", "current_task_status": "finished"},
            "t-bob": {"topic_id": "t-bob", "agent_id": "a1", "user_id": 20, "title": "Bob's",
                      "source": "remote", "current_task_status": "finished"},
        }
        self.tasks = {}

    def get_topic(self, topic_id, context=None):
        topic = self.topics.get(topic_id)
        return topic if topic and topic["user_id"] == context["user_id"] else None

    def create_topic(self, *, title, agent_snapshot, context):
        topic = {"topic_id": "t-new", "agent_id": "a1", "user_id": context["user_id"], "title": title,
                 "source": context["source"], "current_task_status": None}
        self.topics["t-new"] = topic
        return topic

    def find_task_by_request_key(self, key):
        return next((t for t in self.tasks.values() if t["request_key"] == key), None)

    def get_task(self, task_id, context=None):
        task = self.tasks.get(task_id)
        return task if task and self.get_topic(task["topic_id"], context=context) else None

    def list_topic_messages_page(self, **kwargs):
        task = next(iter(self.tasks.values()))
        return {"total": 1, "items": [{"message_id": "m1", "task_id": task["task_id"], "sender_type": "assistant",
                                       "content": "42", "attachments": [{"name": "r.csv", "rel_path": "output/r.csv", "size": 3}]}]}


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    submitted = []
    progress = []
    fixture = SimpleNamespace(status="finished")

    async def submit(**kwargs):
        submitted.append(kwargs)
        fake.tasks["task-1"] = {"task_id": "task-1", "topic_id": kwargs["topic_id"], "prompt": kwargs["message_content"],
                                "request_key": kwargs["request_key"], "task_status": fixture.status, "agent_id": "a1"}
        return {"task_id": "task-1"}

    monkeypatch.setattr(remote_mcp, "get_topic_task_store", lambda: fake)
    monkeypatch.setattr(remote_mcp, "submit_message_task", submit)
    monkeypatch.setattr(remote_mcp, "require_agent_access", lambda user_id, agent_id: {"agent_id": agent_id})
    monkeypatch.setattr(remote_mcp, "build_agent_snapshot", lambda profile: profile)
    monkeypatch.setattr(remote_mcp, "WAIT_POLL_SECONDS", 0.001)

    async def report_progress(elapsed, *, message):
        progress.append((elapsed, message))

    async def authorize(*_args):
        return ALICE_CTX

    monkeypatch.setattr(remote_mcp, "_authenticate", authorize)
    fixture.ctx = SimpleNamespace(request_context=SimpleNamespace(request=None), report_progress=report_progress)
    fixture.fake, fixture.submitted, fixture.progress = fake, submitted, progress
    token = remote_mcp._caller.set(("a1", ALICE_CTX, "https://agent.example"))
    yield fixture
    remote_mcp._caller.reset(token)


def test_ask_starts_an_ordinary_non_interactive_task_and_retries_are_idempotent(store):
    first = run(remote_mcp.ask("本月销售额", "key-00000001", ctx=store.ctx))
    assert first["status"] == "answered" and first["answer"] == "42" and first["thread_id"] == "t-new"
    assert first["files"][0]["download_url"] == "https://agent.example/api/v1/remote-agents/a1/threads/t-new/files/output/r.csv"
    assert store.fake.topics["t-new"]["source"] == "remote"
    assert store.submitted[0]["interactive"] is False
    again = run(remote_mcp.ask("本月销售额", "key-00000001", ctx=store.ctx))
    assert again["run_id"] == "task-1" and len(store.submitted) == 1
    conflict = run(remote_mcp.ask("上月销售额", "key-00000001", ctx=store.ctx))
    assert conflict["error"] == {
        "code": "request_conflict",
        "message": "请求异常。",
        "next_step": "请重新发起请求。如仍失败，请联系管理员。",
    }


def test_threads_are_the_callers_own(store):
    assert (run(remote_mcp.ask("继续", "key-00000002", ctx=store.ctx, thread_id="t-alice")))["thread_id"] == "t-alice"
    assert (run(remote_mcp.ask("偷看", "key-00000003", ctx=store.ctx, thread_id="t-bob")))["error"]["code"] == "not_found"
    assert (run(remote_mcp.get_thread("t-bob")))["error"]["code"] == "not_found"


def test_a_busy_thread_is_reported_with_its_running_task(store):
    store.fake.topics["t-alice"].update(current_task_status="running", current_task_id="task-9")
    busy = run(remote_mcp.ask("继续", "key-00000004", ctx=store.ctx, thread_id="t-alice"))
    assert busy["error"]["code"] == "thread_busy" and busy["run_id"] == "task-9"
    assert "查看当前查询" in busy["error"]["next_step"]
    assert store.submitted == []


def test_background_returns_unfinished_handle_without_waiting(store):
    store.status = "waiting"
    result = run(remote_mcp.ask("本月销售额", "key-background", background=True, ctx=store.ctx))
    assert result == {"run_id": "task-1", "thread_id": "t-new", "status": "in_progress", "retry_after_ms": 2000}
    assert store.progress == []
    store.fake.tasks["task-1"]["task_status"] = "finished"
    assert run(remote_mcp.get_run("task-1"))["answer"] == "42"


def test_foreground_reports_progress_and_returns_one_terminal_result(store):
    store.status = "waiting"

    async def report(elapsed, *, message):
        store.progress.append((elapsed, message))
        store.fake.tasks["task-1"]["task_status"] = "running" if len(store.progress) == 1 else "finished"

    store.ctx.report_progress = report
    result = run(remote_mcp.ask("本月销售额", "key-foreground", ctx=store.ctx))
    assert result["status"] == "answered"
    assert [message for _, message in store.progress] == ["任务排队中", "任务执行中"]
    assert store.progress[1][0] > store.progress[0][0]


def test_foreground_wait_deadline_returns_the_existing_run(store, monkeypatch):
    monkeypatch.setattr(remote_mcp, "WAIT_SECONDS", 0.005)
    store.status = "running"
    result = run(remote_mcp.ask("本月销售额", "key-deadline", ctx=store.ctx))
    resumed = run(remote_mcp.ask("本月销售额", "key-deadline", background=True, ctx=store.ctx))
    assert result == resumed
    assert result["status"] == "in_progress" and len(store.submitted) == 1


def test_foreground_rechecks_authorization_before_releasing_result(store, monkeypatch):
    async def revoke(*_args):
        raise HTTPException(401)

    monkeypatch.setattr(remote_mcp, "_authenticate", revoke)
    result = asyncio.run(remote_mcp.ask("本月销售额", "key-revoked", ctx=store.ctx))
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "unauthorized"
    assert "重新连接" in result.content[0].text
    assert store.fake.tasks["task-1"]["task_status"] == "finished"


def test_busy_thread_reuses_same_key_inserted_after_first_lookup(store, monkeypatch):
    from unittest.mock import Mock

    previous = {"task_id": "task-1", "topic_id": "t-alice", "prompt": "继续", "agent_id": "a1",
                "task_status": "running"}
    store.fake.tasks["task-1"] = previous
    store.fake.topics["t-alice"].update(current_task_status="running", current_task_id="task-1")
    monkeypatch.setattr(store.fake, "find_task_by_request_key", Mock(side_effect=[None, previous]))
    result = run(remote_mcp.ask("继续", "key-concurrent", thread_id="t-alice", background=True, ctx=store.ctx))
    assert result["run_id"] == "task-1" and store.submitted == []


@pytest.mark.parametrize(("code", "next_step"), [
    ("PI_RUN_TIMEOUT", "缩小时间范围"),
    ("PI_RUN_IDLE_TIMEOUT", "请重试一次"),
    ("run_interrupted", "请先核对操作结果"),
    ("task_execution_failed", "请稍后重试"),
])
def test_failed_ask_is_an_error_but_reading_its_terminal_state_succeeds(store, monkeypatch, code, next_step):
    store.status = "error"
    original_submit = remote_mcp.submit_message_task

    async def submit(**kwargs):
        result = await original_submit(**kwargs)
        store.fake.tasks[result["task_id"]]["error"] = {
            "code": code, "message": "internal diagnostic", "detail": "private service URL",
        }
        return result

    monkeypatch.setattr(remote_mcp, "submit_message_task", submit)
    failed = asyncio.run(remote_mcp.ask("本月销售额", "key-failed", ctx=store.ctx))
    observed = asyncio.run(remote_mcp.get_run("task-1"))
    assert failed.isError is True and observed.isError is False
    assert failed.structuredContent == observed.structuredContent
    assert observed.structuredContent["status"] == "failed"
    assert observed.structuredContent["answer"] == ""
    assert observed.structuredContent["error"]["code"] == code
    assert next_step in failed.content[0].text
    assert "private service URL" not in failed.model_dump_json()
