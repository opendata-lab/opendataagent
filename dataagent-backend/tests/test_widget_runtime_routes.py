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
            external_user_id="",
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
            "server_side": {"enabled": True, "access_key_hash": _hash_key(DEMO_KEY)},
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


DEMO_KEY = "demo-secret-access-key"


def widget_headers(user="alice", *, website_id="demo", key=DEMO_KEY):
    """What a host backend forwards: site, access key and its signed-in user name."""
    return {
        "X-DataAgent-Client": "widget",
        "X-DataAgent-Website-Id": website_id,
        "X-DataAgent-Access-Key": key,
        "X-DataAgent-User-Id": user,
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


def test_browser_widget_without_server_key_is_rejected(monkeypatch):
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
        # The site requires the host backend's access key.
        assert resp.status_code == 403
    assert store.calls == [("init_schema", None)] * 3


def test_widget_topic_list_and_create_agent_validation(monkeypatch):
    install_fake_accounts()
    install_widget_settings(monkeypatch)
    profile = install_agent_profile(monkeypatch, "agent_widget")
    store = install_fake_store(monkeypatch)
    client = TestClient(app)
    headers = widget_headers()

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


def test_trusted_widget_user_name_is_the_platform_user(monkeypatch):
    acc_store, _admin, alice = install_fake_accounts()
    install_widget_settings(monkeypatch)
    install_agent_profile(monkeypatch, "agent_widget")
    store = install_fake_store(monkeypatch)
    client = TestClient(app)

    assert client.get("/api/v1/nl2sql/topics", headers=widget_headers(key="wrong-key")).status_code == 403

    # The host's user name binds to the same-named platform account.
    assert client.get("/api/v1/nl2sql/topics", headers=widget_headers("alice")).status_code == 200
    assert store.calls[-1][1]["user_id"] == alice.id

    # An unknown user name registers an ordinary account.
    assert client.get("/api/v1/nl2sql/topics", headers=widget_headers("emp99")).status_code == 200
    emp_uid = store.calls[-1][1]["user_id"]
    assert emp_uid not in (None, alice.id)
    assert not acc_store.get_user(emp_uid).is_admin

    # The admin account is never reachable through a Widget host.
    assert client.get("/api/v1/nl2sql/topics", headers=widget_headers("admin")).status_code == 409


def test_widget_origin_site_and_task_isolation(monkeypatch):
    install_fake_accounts()
    install_widget_settings(monkeypatch)
    install_fake_store(monkeypatch)
    client = TestClient(app)

    bad_site = client.get("/api/v1/nl2sql/topics", headers=widget_headers(website_id="unknown"))
    assert bad_site.status_code == 403

    missing_task = client.get("/api/v1/nl2sql/tasks/task-forbidden", headers=widget_headers())
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
    client = TestClient(app)

    resp = client.post(
        "/api/v1/nl2sql/widget-events",
        headers=widget_headers(),
        json={"events": [{"event_type": "widget_open"}, {"event_type": "message_send", "payload": {"length": 5}}]},
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 2
    _, ctx = store.widget_event_calls[0]
    assert (ctx["source"], ctx["website_id"], ctx["user_id"]) == ("widget", "demo", 2)

    empty = client.post("/api/v1/nl2sql/widget-events", headers=widget_headers(), json={"events": []})
    assert empty.status_code == 200
    assert empty.json()["accepted"] == 0

    cfg = client.get("/api/v1/nl2sql/runtime-config", headers=widget_headers())
    assert cfg.status_code == 200
    payload = cfg.json()
    assert payload["default_provider_id"] == "openrouter"
    assert "api_key_set" not in payload["providers"][0]
    assert "mysql_host" not in payload
