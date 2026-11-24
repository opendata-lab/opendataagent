from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import api.routes as routes
import core.agent_profile_service as agent_profile_service
import core.skill_admin_service as skill_admin_service
from core.accounts import issue_session_token
from core.topic_task_store import TopicTaskStore
from main import app
from tests.account_test_helpers import bearer_headers, install_fake_accounts


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def topic_payload(
    topic_id: str,
    title: str = "Widget Topic",
    *,
    source: str = "widget",
    website_id: str = "demo",
    user_id: int | None = 2,
    external_user_id: str = "",
):
    return {
        "topic_id": topic_id,
        "title": title,
        "chat_topic_id": f"chat_{topic_id}",
        "chat_conversation_id": f"conversation_{topic_id}",
        "current_task_id": "task-1",
        "current_task_status": "waiting",
        "message_count": 0,
        "last_message_preview": "",
        "created_at": "2026-04-29T10:00:00",
        "updated_at": "2026-04-29T10:00:00",
        "source": source,
        "website_id": website_id,
        "user_id": user_id,
        "external_user_id": external_user_id,
        "visitor_id": "",
        "agent_id": "agent_widget",
    }


class FakeTopicStore:
    def __init__(self):
        self.calls = []
        self.widget_event_calls = []
        self.topics: dict[str, dict] = {}

    def init_schema(self):
        self.calls.append(("init_schema", None))

    def create_topic(self, title="新话题", *, agent_snapshot=None, permission_mode=None, context=None, is_agent_preview=False):
        self.calls.append(("create_topic", context, agent_snapshot))
        payload = topic_payload(
            "topic-created",
            title,
            source=(context or {}).get("source", "widget"),
            website_id=(context or {}).get("website_id", "demo"),
            user_id=(context or {}).get("user_id"),
            external_user_id=(context or {}).get("shared_session_key", ""),
        )
        self.topics[payload["topic_id"]] = payload
        return payload

    def list_topics(self, include_messages=False, *, context=None, agent_id=None):
        self.calls.append(("list_topics", context, agent_id))
        return list(self.topics.values()) or [topic_payload("topic-widget")]

    def get_topic(self, topic_id, *, context=None):
        self.calls.append(("get_topic", context))
        if topic_id == "topic-forbidden":
            return None
        if topic_id in self.topics:
            return self.topics[topic_id]
        return topic_payload(topic_id)

    def update_topic(self, topic_id, *, title=None, permission_mode=None, context=None):
        self.calls.append(("update_topic", context))
        return topic_payload(topic_id, title or "新话题")

    def delete_topic(self, topic_id, *, context=None):
        self.calls.append(("delete_topic", context))

    def list_topic_messages_page(self, *, topic_id, page=1, page_size=200, order="asc", context=None):
        self.calls.append(("list_topic_messages_page", context))
        return {
            "topic_id": topic_id,
            "page": page,
            "page_size": page_size,
            "order": order,
            "total": 0,
            "items": [],
        }

    def get_task(self, task_id, *, context=None):
        self.calls.append(("get_task", context))
        if task_id == "task-forbidden":
            return None
        return {
            "task_id": task_id,
            "topic_id": "topic-widget",
            "task_status": "waiting",
            "prompt": "",
            "provider_id": "openrouter",
            "model": "claude-opus-4-6",
            "created_at": "2026-04-29T10:00:00",
            "updated_at": "2026-04-29T10:00:00",
        }

    def request_task_cancel(self, task_id, *, context=None):
        self.calls.append(("request_task_cancel", context))
        if task_id == "task-forbidden":
            return None
        return self.get_task(task_id, context=context)

    def record_widget_events(self, events, context=None):
        self.widget_event_calls.append((events, context))
        return len(events)


def install_fake_store(monkeypatch):
    store = FakeTopicStore()
    monkeypatch.setattr(routes, "get_topic_task_store", lambda: store)
    return store


def install_widget_settings(monkeypatch, *, sites=None):
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: SimpleNamespace(
            llm_provider="openrouter",
            claude_model="claude-opus-4-6",
            skills_output_dir="../.claude/skills/example-business-knowledge",
            redis_host="127.0.0.1",
            redis_port=6379,
            run_events_stream_poll_interval_seconds=1,
            run_events_stream_ping_seconds=10,
        ),
    )
    resolved_sites = sites if sites is not None else [
        {
            "website_id": "demo",
            "allowed_origins": ["https://host.example.com"],
            "project_name": "Demo",
            "project_color": "#4A90A4",
        }
    ]
    payload = {"widget_allowed_sites": resolved_sites}
    monkeypatch.setattr(skill_admin_service, "current_settings_payload", lambda: payload)


def install_agent_profile(monkeypatch, agent_id="agent_widget", *, visibility=None):
    profile = {
        "agent_id": agent_id,
        "name": "Widget Agent",
        "description": "",
        "system_prompt": "",
        "permission_mode": "inherit",
        "allowed_tools": ["Read"],
        "mcp_server_ids": [],
        "skill_folders": [],
        "max_turns": 0,
        "env_vars": {},
        "visibility": visibility or {"mode": "all"},
    }
    monkeypatch.setattr(
        agent_profile_service,
        "get_agent_profile",
        lambda requested_agent_id: profile if requested_agent_id == agent_id else None,
    )
    return profile


def platform_widget_headers(*, origin="https://host.example.com", fetch_site="same-origin"):
    return {
        "Origin": origin,
        "Sec-Fetch-Site": fetch_site,
        "X-DataAgent-Client": "widget",
        "X-DataAgent-Website-Id": "demo",
        "X-DataAgent-Widget-Identity": "platform",
    }


def test_web_requests_require_authenticated_session(monkeypatch):
    install_fake_accounts()
    store = install_fake_store(monkeypatch)
    client = TestClient(app)

    assert client.get("/api/v1/nl2sql/topics").status_code == 401

    resp = client.get("/api/v1/nl2sql/topics", headers=bearer_headers(2))
    assert resp.status_code == 200
    context = store.calls[-1][1]
    assert context["source"] == "web"
    assert context["user_id"] == 2
    assert context["website_id"] == ""
    assert context["shared_session_key"] == ""


def test_platform_widget_uses_verified_session_and_requires_same_origin(monkeypatch):
    install_fake_accounts()
    install_widget_settings(monkeypatch)
    store = install_fake_store(monkeypatch)
    client = TestClient(app, base_url="https://host.example.com")
    headers = platform_widget_headers()

    unauth = client.get("/api/v1/nl2sql/topics", headers=headers)
    assert unauth.status_code == 401
    assert unauth.json()["detail"]["code"] == "WIDGET_LOGIN_REQUIRED"

    client.cookies.set("da_session", issue_session_token(2))
    ok = client.get("/api/v1/nl2sql/topics", headers=headers)
    assert ok.status_code == 200
    context = store.calls[-1][1]
    assert (context["source"], context["website_id"], context["user_id"]) == ("widget", "demo", 2)
    assert context["shared_session_key"] == ""

    denied = client.get("/api/v1/nl2sql/topics", headers=platform_widget_headers(fetch_site="same-site"))
    assert denied.status_code == 403


def test_browser_widget_without_platform_identity_or_server_key_is_rejected(monkeypatch):
    install_fake_accounts()
    install_widget_settings(monkeypatch)
    store = install_fake_store(monkeypatch)
    client = TestClient(app)

    # Host-supplied X-DataAgent-User-Id or visitor_id alone is never trusted.
    for extra in (
        {"X-DataAgent-User-Id": "user-123"},
        {"X-DataAgent-Visitor-Id": "visitor-abc"},
        {},
    ):
        resp = client.get(
            "/api/v1/nl2sql/topics",
            headers={
                "Origin": "https://host.example.com",
                "X-DataAgent-Client": "widget",
                "X-DataAgent-Website-Id": "demo",
                **extra,
            },
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "WIDGET_LOGIN_REQUIRED"
    assert store.calls == [("init_schema", None)] * 3


def test_widget_topic_list_and_create_agent_validation(monkeypatch):
    install_fake_accounts()
    install_widget_settings(monkeypatch)
    profile = install_agent_profile(monkeypatch, "agent_widget")
    store = install_fake_store(monkeypatch)
    client = TestClient(app, base_url="https://host.example.com")
    client.cookies.set("da_session", issue_session_token(2))
    headers = platform_widget_headers()

    listed = client.get("/api/v1/nl2sql/topics?agent_id=agent_widget", headers=headers)
    assert listed.status_code == 200
    assert store.calls[-1][0] == "list_topics"
    assert store.calls[-1][2] == "agent_widget"

    missing_agent = client.post("/api/v1/nl2sql/topics", headers=headers, json={"title": "Widget 会话"})
    assert missing_agent.status_code == 400
    assert "agent_id" in missing_agent.json()["detail"]

    created = client.post(
        "/api/v1/nl2sql/topics",
        headers=headers,
        json={"title": "Widget 会话", "agent_id": "agent_widget"},
    )
    assert created.status_code == 200
    assert store.calls[-1][0] == "create_topic"
    assert store.calls[-1][2]["agent_id"] == profile["agent_id"]


def test_trusted_server_side_personal_widget_links_user_and_maps_provider(monkeypatch):
    acc_store, _admin, _alice = install_fake_accounts()
    raw_key = "odw-secret-access-key"
    install_widget_settings(
        monkeypatch,
        sites=[
            {
                "website_id": "odw",
                "allowed_origins": [],
                "project_name": "OpenDataWorks",
                "server_side": {
                    "enabled": True,
                    "access_key_hash": _hash_key(raw_key),
                    "account_mode": "personal",
                    "trusted_username_namespace": True,
                    "provider_mappings": {"sso-corp": "CorpSSO"},
                },
            }
        ],
    )
    install_agent_profile(monkeypatch, "agent_widget")
    store = install_fake_store(monkeypatch)
    client = TestClient(app)

    # Bad key rejected
    bad = client.get(
        "/api/v1/nl2sql/topics",
        headers={
            "X-DataAgent-Client": "widget",
            "X-DataAgent-Website-Id": "odw",
            "X-DataAgent-Access-Key": "wrong-key",
            "X-DataAgent-User-Id": "alice",
        },
    )
    assert bad.status_code == 403

    # Bare username `alice` links to existing user #2 (trusted_username_namespace=True)
    ok_bare = client.get(
        "/api/v1/nl2sql/topics",
        headers={
            "X-DataAgent-Client": "widget",
            "X-DataAgent-Website-Id": "odw",
            "X-DataAgent-Access-Key": raw_key,
            "X-DataAgent-User-Id": "alice",
        },
    )
    assert ok_bare.status_code == 200
    assert store.calls[-1][1]["user_id"] == 2
    assert store.calls[-1][1]["shared_session_key"] == ""

    # `local:alice` resolves to the same user #2
    ok_local = client.get(
        "/api/v1/nl2sql/topics",
        headers={
            "X-DataAgent-Client": "widget",
            "X-DataAgent-Website-Id": "odw",
            "X-DataAgent-Access-Key": raw_key,
            "X-DataAgent-User-Id": "local:alice",
        },
    )
    assert ok_local.status_code == 200
    assert store.calls[-1][1]["user_id"] == 2

    # Mapped OAuth `<reg>:<sub>` creates/links under provider `CorpSSO`
    ok_oauth = client.get(
        "/api/v1/nl2sql/topics",
        headers={
            "X-DataAgent-Client": "widget",
            "X-DataAgent-Website-Id": "odw",
            "X-DataAgent-Access-Key": raw_key,
            "X-DataAgent-User-Id": "sso-corp:emp99",
        },
    )
    assert ok_oauth.status_code == 200
    emp_uid = store.calls[-1][1]["user_id"]
    assert emp_uid is not None and emp_uid != 2
    assert any(
        ident["source"] == "CorpSSO" and ident["subject"] == "emp99"
        for ident in acc_store.identities.values()
    )


def test_trusted_server_side_shared_widget_collaborative_session_and_readonly_legacy(monkeypatch):
    acc_store, _admin, _alice = install_fake_accounts()
    # Create second member `bob`
    bob = acc_store.create_user(username="bob", password_hash="x", display_name="Bob")
    raw_key = "onto-secret-access-key"
    install_widget_settings(
        monkeypatch,
        sites=[
            {
                "website_id": "onto",
                "allowed_origins": [],
                "project_name": "OntoFoundry",
                "server_side": {
                    "enabled": True,
                    "access_key_hash": _hash_key(raw_key),
                    "account_mode": "shared",
                    "trusted_username_namespace": True,
                    "provider_mappings": {},
                },
            }
        ],
    )
    install_agent_profile(monkeypatch, "agent_widget")
    store = install_fake_store(monkeypatch)
    client = TestClient(app)

    base = {
        "X-DataAgent-Client": "widget",
        "X-DataAgent-Website-Id": "onto",
        "X-DataAgent-Access-Key": raw_key,
    }

    # Missing X-DataAgent-Session-Key rejected
    assert client.get("/api/v1/nl2sql/topics", headers=base).status_code == 400

    # Background read works with session key and WITHOUT X-DataAgent-User-Id
    bg_headers = {**base, "X-DataAgent-Session-Key": "session-100"}
    assert client.get("/api/v1/nl2sql/topics", headers=bg_headers).status_code == 200
    assert store.calls[-1][1]["shared_session_key"] == "session-100"
    assert store.calls[-1][1]["user_id"] is None

    # Write WITHOUT X-DataAgent-User-Id is rejected (401)
    assert (
        client.post(
            "/api/v1/nl2sql/topics",
            headers=bg_headers,
            json={"title": "共享分析", "agent_id": "agent_widget"},
        ).status_code
        == 401
    )

    # Member A (`alice`) creates shared topic
    alice_headers = {**bg_headers, "X-DataAgent-User-Id": "alice"}
    created = client.post(
        "/api/v1/nl2sql/topics",
        headers=alice_headers,
        json={"title": "共享分析", "agent_id": "agent_widget"},
    )
    assert created.status_code == 200
    assert store.calls[-1][1]["user_id"] == 2
    assert store.calls[-1][1]["shared_session_key"] == "session-100"

    # Member B (`bob`) reads and updates the same shared topic
    bob_headers = {**bg_headers, "X-DataAgent-User-Id": "bob"}
    assert client.get("/api/v1/nl2sql/topics/topic-created", headers=bob_headers).status_code == 200
    patched = client.put(
        "/api/v1/nl2sql/topics/topic-created",
        headers=bob_headers,
        json={"title": "Bob 更新标题"},
    )
    assert patched.status_code == 200
    assert store.calls[-1][1]["user_id"] == bob.id

    # Legacy shared topic (user_id is None) is read-only: GET ok, write returns 403
    store.topics["legacy-shared"] = topic_payload(
        "legacy-shared",
        source="widget",
        website_id="onto",
        user_id=None,
        external_user_id="session-100",
    )
    assert client.get("/api/v1/nl2sql/topics/legacy-shared", headers=bg_headers).status_code == 200
    denied_write = client.put(
        "/api/v1/nl2sql/topics/legacy-shared",
        headers=alice_headers,
        json={"title": "不能修改"},
    )
    assert denied_write.status_code == 403


def test_widget_origin_site_and_task_isolation(monkeypatch):
    install_fake_accounts()
    install_widget_settings(monkeypatch)
    install_fake_store(monkeypatch)
    client = TestClient(app, base_url="https://host.example.com")
    client.cookies.set("da_session", issue_session_token(2))

    bad_site = client.get(
        "/api/v1/nl2sql/topics",
        headers={**platform_widget_headers(), "X-DataAgent-Website-Id": "unknown"},
    )
    assert bad_site.status_code == 403

    bad_origin = client.get(
        "/api/v1/nl2sql/topics",
        headers=platform_widget_headers(origin="https://evil.example.com", fetch_site="cross-site"),
    )
    assert bad_origin.status_code == 403

    missing_task = client.get("/api/v1/nl2sql/tasks/task-forbidden", headers=platform_widget_headers())
    assert missing_task.status_code == 404


def test_store_internal_calls_without_request_context_are_unscoped():
    store = TopicTaskStore()
    sql, params = store._topic_context_predicate(None, alias="topic")
    assert sql == "1 = 1"
    assert params == []


def test_widget_events_and_runtime_config(monkeypatch):
    install_fake_accounts()
    install_widget_settings(monkeypatch)
    store = install_fake_store(monkeypatch)
    monkeypatch.setattr(
        routes,
        "resolved_chat_settings_payload",
        lambda: {
            "default_provider_id": "openrouter",
            "default_model": "claude-opus-4-6",
            "providers": [
                {
                    "provider_id": "openrouter",
                    "display_name": "OpenRouter",
                    "provider_group": "聚合路由",
                    "models": ["claude-opus-4-6"],
                    "default_model": "claude-opus-4-6",
                    "enabled": True,
                    "provider_enabled": True,
                    "supports_partial_messages": True,
                    "api_key_set": True,
                    "auth_token_set": True,
                    "base_url": "https://openrouter.ai/api",
                }
            ],
            "mysql_host": "127.0.0.1",
            "mysql_database": "example",
        },
    )
    client = TestClient(app, base_url="https://host.example.com")
    client.cookies.set("da_session", issue_session_token(2))

    resp = client.post(
        "/api/v1/nl2sql/widget-events",
        headers=platform_widget_headers(),
        json={"events": [{"event_type": "widget_open"}, {"event_type": "message_send", "payload": {"length": 5}}]},
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 2
    _, ctx = store.widget_event_calls[0]
    assert (ctx["source"], ctx["website_id"], ctx["user_id"]) == ("widget", "demo", 2)

    empty = client.post("/api/v1/nl2sql/widget-events", headers=platform_widget_headers(), json={"events": []})
    assert empty.status_code == 200
    assert empty.json()["accepted"] == 0

    bad = client.post(
        "/api/v1/nl2sql/widget-events",
        headers=platform_widget_headers(origin="https://evil.example.com", fetch_site="cross-site"),
        json={"events": [{"event_type": "widget_open"}]},
    )
    assert bad.status_code == 403

    cfg = client.get("/api/v1/nl2sql/runtime-config")
    assert cfg.status_code == 200
    payload = cfg.json()
    assert payload["default_provider_id"] == "openrouter"
    assert "api_key_set" not in payload["providers"][0]
    assert "mysql_host" not in payload
