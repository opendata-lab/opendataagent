from __future__ import annotations

import sys
import types
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import api.admin_routes as admin_routes
import core.agent_profile_service as agent_profile_service
from main import app
from tests.account_test_helpers import bearer_headers, install_fake_accounts


def _admin_client():
    _store, admin, _alice = install_fake_accounts()
    return TestClient(app, headers=bearer_headers(admin.id))


def test_admin_settings_contract(monkeypatch):
    captured = {}

    def _persist(payload):
        captured["payload"] = payload
        return {"updated_at": "2026-03-06T12:00:00"}

    monkeypatch.setattr(
        admin_routes,
        "current_settings_payload",
        lambda: {
            "provider_id": "openrouter",
            "model": "anthropic/claude-sonnet-4.5",
            "anthropic_api_key": "k",
            "anthropic_auth_token": "t",
            "anthropic_base_url": "https://example.com",
            "mysql_host": "127.0.0.1",
            "mysql_port": 3306,
            "mysql_user": "root",
            "mysql_password": "pwd",
            "mysql_database": "example",
            "doris_host": "127.0.0.1",
            "doris_port": 9030,
            "doris_user": "root",
            "doris_password": "pwd",
            "doris_database": "ods",
            "skills_output_dir": "../.claude/skills/example-business-knowledge",
            "session_mysql_database": "dataagent",
        },
    )
    monkeypatch.setattr(admin_routes, "resolve_skills_root_dir", lambda: "/tmp/.claude/skills/example-business-knowledge")
    monkeypatch.setattr(
        admin_routes,
        "_provider_catalog",
        lambda: [
            admin_routes.ProviderConfig(
                provider_id="openrouter",
                display_name="OpenRouter",
                provider_group="聚合路由",
                models=["anthropic/claude-sonnet-4.5"],
                supported_models=["anthropic/claude-sonnet-4.5"],
                default_model="anthropic/claude-sonnet-4.5",
                enabled=True,
                provider_enabled=True,
                validation_status="verified",
                model_detections={
                    "anthropic/claude-sonnet-4.5": {
                        "status": "verified",
                        "message": "模型检测通过",
                        "checked_at": "2026-04-17T10:00:00",
                    }
                },
            )
        ],
    )
    monkeypatch.setattr(
        admin_routes,
        "persist_admin_settings",
        _persist,
    )

    client = _admin_client()

    response = client.get("/api/v1/nl2sql-admin/settings")
    assert response.status_code == 200
    assert response.json()["provider_id"] == "openrouter"
    assert response.json()["providers"][0]["model_detections"]["anthropic/claude-sonnet-4.5"]["status"] == "verified"

    update = client.put(
        "/api/v1/nl2sql-admin/settings",
        json={
            "provider_id": "openrouter",
            "model": "anthropic/claude-sonnet-4.5",
            "providers": [
                {
                    "provider_id": "openrouter",
                    "enabled_models": ["anthropic/claude-sonnet-4.5"],
                }
            ],
        },
    )
    assert update.status_code == 200
    assert update.json()["updated_at"] == "2026-03-06T12:00:00"
    assert captured["payload"]["providers"][0]["enabled_models"] == ["anthropic/claude-sonnet-4.5"]
    assert client.get("/api/v1/dataagent/settings").status_code == 404


def test_model_detection_route_contract(monkeypatch):
    captured = {}

    async def _detect(payload):
        captured["payload"] = payload
        return {
            "provider_id": payload["provider_id"],
            "model": payload["model"],
            "status": "verified",
            "message": "模型检测通过",
            "checked_at": "2026-04-17T10:00:00",
        }

    monkeypatch.setattr(admin_routes, "detect_model_availability", _detect)

    client = _admin_client()
    response = client.post(
        "/api/v1/nl2sql-admin/model-detections",
        json={
            "provider_id": "openrouter",
            "model": "anthropic/claude-sonnet-4.5",
            "auth_token": "token",
            "base_url": "https://openrouter.ai/api",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "verified"
    assert captured["payload"]["base_url"] == "https://openrouter.ai/api"


def test_mcp_registry_routes_contract(monkeypatch):
    captured = {}
    row = {
        "server_id": "srv_demo",
        "name": "demo",
        "source": "configured",
        "transport": "http",
        "url": "https://mcp.example.test/v1",
        "headers": {},
        "command": "",
        "args": [],
        "env": {},
        "enabled": True,
        "oauth_required": False,
        "tool_count": 0,
        "description": "",
    }
    def _create_mcp(payload):
        captured["created"] = payload
        return "srv_demo"

    monkeypatch.setattr(admin_routes, "list_mcp_servers", lambda: {"configured": [row], "plugin": []})
    monkeypatch.setattr(admin_routes, "create_mcp_server", _create_mcp)
    monkeypatch.setattr(
        admin_routes,
        "update_mcp_server",
        lambda server_id, payload: captured.setdefault("updated", (server_id, payload)),
    )
    monkeypatch.setattr(
        admin_routes,
        "delete_mcp_server",
        lambda server_id: captured.setdefault("deleted", server_id),
    )
    monkeypatch.setattr(
        admin_routes,
        "import_mcp_servers",
        lambda payload: captured.setdefault("imported_payload", payload) and 1,
    )
    async def _detect_mcp(server_id):
        captured["detected"] = server_id
        return {
            "server_id": server_id,
            "status": "verified",
            "message": "MCP 检测通过，发现 1 个工具",
            "tool_count": 1,
            "tool_names": ["demo_tool"],
            "latency_ms": 42,
            "checked_at": "2026-09-27T12:00:00+00:00",
        }
    monkeypatch.setattr(admin_routes, "detect_mcp_server", _detect_mcp)
    client = _admin_client()

    assert client.get("/api/v1/dataagent/mcp/servers").json()["configured"][0]["server_id"] == "srv_demo"
    created = client.post(
        "/api/v1/dataagent/mcp/servers",
        json={"name": "demo", "transport": "http", "url": "https://mcp.example.test/v1"},
    )
    assert created.status_code == 200
    assert created.json() == {"server_id": "srv_demo"}
    assert client.patch("/api/v1/dataagent/mcp/servers/srv_demo", json={"enabled": False}).json() == {"ok": True}
    assert client.delete("/api/v1/dataagent/mcp/servers/srv_demo").json() == {"ok": True}
    imported = client.post(
        "/api/v1/dataagent/mcp/servers/import",
        json={"mcpServers": {"demo": {"command": "npx"}}},
    )
    assert imported.json() == {"imported": 1}
    detected = client.post("/api/v1/dataagent/mcp/servers/srv_demo/detections")
    assert detected.status_code == 200
    assert detected.json()["status"] == "verified"
    assert detected.json()["tool_names"] == ["demo_tool"]
    assert captured["updated"] == ("srv_demo", {"enabled": False})
    assert captured["deleted"] == "srv_demo"
    assert captured["detected"] == "srv_demo"


def test_skill_document_routes_contract(monkeypatch):
    summary = {
        "id": 1,
        "folder": "example-platform-tools",
        "relative_path": "reference/40-runtime-metadata.md",
        "file_name": "40-runtime-metadata.md",
        "category": "reference",
        "content_type": "markdown",
        "source": "bundled",
        "current_hash": "hash",
        "current_version_id": 3,
        "version_count": 3,
        "last_change_source": "sync",
        "last_change_summary": "manual sync",
        "created_at": "2026-03-06T10:00:00",
        "updated_at": "2026-03-06T12:00:00",
        "editable": True,
        "enabled": True,
    }
    detail = {
        **summary,
        "current_content": "{\"schema_version\":\"1.0\"}",
        "versions": [
            {
                "id": 3,
                "document_id": 1,
                "version_no": 3,
                "change_source": "sync",
                "change_summary": "manual sync",
                "actor": "ui",
                "content_hash": "hash",
                "file_size": 20,
                "metadata": None,
                "parent_version_id": None,
                "created_at": "2026-03-06T12:00:00",
                "is_current": True,
            }
        ],
    }

    monkeypatch.setattr(admin_routes, "list_documents", lambda: [summary])
    monkeypatch.setattr(admin_routes, "get_document_detail", lambda document_id: detail if document_id == 1 else None)
    monkeypatch.setattr(admin_routes, "save_document_content", lambda document_id, content, change_summary=None: detail)
    monkeypatch.setattr(
        admin_routes,
        "compare_document_versions",
        lambda document_id, left_version_id=None, right_version_id=None: {
            "document_id": document_id,
            "left_label": "V2",
            "right_label": "当前版本",
            "left_content": "{}",
            "right_content": "{\"schema_version\":\"1.0\"}",
            "diff_text": "--- V2\n+++ 当前版本",
            "added_lines": 1,
            "removed_lines": 1,
            "changed_lines": 2,
        },
    )
    monkeypatch.setattr(admin_routes, "rollback_document", lambda document_id, version_id: detail)
    monkeypatch.setattr(
        admin_routes,
        "update_skill_runtime",
        lambda folder, enabled: {
            "skill_id": folder,
            "enabled": enabled,
        },
    )
    monkeypatch.setattr(
        admin_routes,
        "import_skill_from_zip",
        lambda file_name, content: {
            "skill_id": "marketing-insights",
            "source": "managed",
            "enabled": False,
            "imported_documents": [
                {
                    **summary,
                    "id": 2,
                    "folder": "marketing-insights",
                    "relative_path": "SKILL.md",
                    "file_name": "SKILL.md",
                    "source": "managed",
                    "enabled": False,
                }
            ],
            "document_count": 2,
        },
    )
    monkeypatch.setattr(
        admin_routes,
        "uninstall_skill",
        lambda folder: {
            "skill_id": folder,
            "removed_documents": [
                {
                    **summary,
                    "id": 2,
                    "folder": folder,
                    "relative_path": "SKILL.md",
                    "file_name": "SKILL.md",
                    "source": "managed",
                    "enabled": False,
                }
            ],
            "was_enabled": False,
            "document_count": 1,
        },
    )
    client = _admin_client()

    list_response = client.get("/api/v1/dataagent/skills/documents")
    assert list_response.status_code == 200
    assert list_response.json()[0]["folder"] == "example-platform-tools"
    assert list_response.json()[0]["relative_path"] == "reference/40-runtime-metadata.md"
    assert list_response.json()[0]["source"] == "bundled"
    assert list_response.json()[0]["enabled"] is True
    assert list_response.json()[0]["editable"] is True

    detail_response = client.get("/api/v1/dataagent/skills/documents/1")
    assert detail_response.status_code == 200
    assert detail_response.json()["versions"][0]["version_no"] == 3

    save_response = client.put(
        "/api/v1/dataagent/skills/documents/1",
        json={"content": "{\"schema_version\":\"1.0\"}", "change_summary": "save"},
    )
    assert save_response.status_code == 200
    assert save_response.json()["id"] == 1

    compare_response = client.post(
        "/api/v1/dataagent/skills/documents/1/compare",
        json={"left_version_id": 2},
    )
    assert compare_response.status_code == 200
    assert compare_response.json()["changed_lines"] == 2

    rollback_response = client.post("/api/v1/dataagent/skills/documents/1/versions/2/rollback")
    assert rollback_response.status_code == 200
    assert rollback_response.json()["id"] == 1

    runtime_response = client.put("/api/v1/dataagent/skills/runtime/example-business-knowledge", json={"enabled": True})
    assert runtime_response.status_code == 200
    assert runtime_response.json()["skill_id"] == "example-business-knowledge"
    assert runtime_response.json()["enabled"] is True

    runtime_disable_response = client.put("/api/v1/dataagent/skills/runtime/example-business-knowledge", json={"enabled": False})
    assert runtime_disable_response.status_code == 200
    assert runtime_disable_response.json()["enabled"] is False

    import_response = client.post(
        "/api/v1/dataagent/skills/imports",
        files={"file": ("marketing-insights.zip", b"zip-content", "application/zip")},
    )
    assert import_response.status_code == 200
    assert import_response.json()["skill_id"] == "marketing-insights"
    assert import_response.json()["source"] == "managed"
    assert import_response.json()["enabled"] is False
    assert import_response.json()["imported_documents"][0]["source"] == "managed"

    uninstall_response = client.delete("/api/v1/dataagent/skills/marketing-insights")
    assert uninstall_response.status_code == 200
    assert uninstall_response.json()["skill_id"] == "marketing-insights"
    assert uninstall_response.json()["removed_documents"][0]["folder"] == "marketing-insights"

    sync_response = client.post("/api/v1/dataagent/skills/sync")
    assert sync_response.status_code == 405


def test_skill_uninstall_route_rejects_service_errors(monkeypatch):
    def _reject_uninstall(folder):
        raise ValueError("内置 Skill 不支持卸载")

    monkeypatch.setattr(admin_routes, "uninstall_skill", _reject_uninstall)

    client = _admin_client()
    response = client.delete("/api/v1/dataagent/skills/example-business-knowledge")

    assert response.status_code == 400
    assert response.json()["detail"] == "内置 Skill 不支持卸载"


def test_agent_profile_routes_contract(monkeypatch):
    profile = {
        "agent_id": "agent_1",
        "name": "营销分析智能体",
        "description": "营销场景",
        "system_prompt": "只回答营销问题",
        "permission_mode": "inherit",
        "allowed_tools": ["Skill", "Read"],
        "mcp_server_ids": ["web"],
        "skill_folders": ["marketing-insights"],
        "max_turns": 12,
        "env_vars": {"SAFE_FLAG": "1"},
        "is_default": False,
        "is_builtin": False,
        "created_at": "2026-05-21T10:00:00",
        "updated_at": "2026-05-21T10:00:00",
    }
    calls = {}

    monkeypatch.setattr(
        admin_routes,
        "list_documents",
        lambda: [
            {
                "folder": "marketing-insights",
                "relative_path": "SKILL.md",
                "source": "managed",
                "enabled": True,
            }
        ],
    )
    monkeypatch.setattr(admin_routes, "list_agent_profiles", lambda: [profile])
    monkeypatch.setattr(agent_profile_service, "get_agent_profile", lambda agent_id: profile if agent_id == "agent_1" else None)
    monkeypatch.setattr(admin_routes, "agent_capabilities", lambda documents: {
        "tools": ["Skill", "Read"],
        "mcp_servers": [{"id": "web", "name": "Example MCP", "enabled": True, "tool_names": ["example_query_readonly"]}],
        "skills": [{"folder": "marketing-insights", "source": "managed", "enabled": True}],
        "permission_modes": ["inherit", "default", "bypassPermissions"],
    })
    monkeypatch.setattr(admin_routes, "skill_folders_from_documents", lambda documents: {"marketing-insights"})

    def _create(payload, available_skill_folders):
        calls["create"] = {"payload": payload, "available_skill_folders": available_skill_folders}
        return profile

    def _update(agent_id, payload, revision, available_skill_folders):
        calls["update"] = {"agent_id": agent_id, "payload": payload, "available_skill_folders": available_skill_folders}
        return {**profile, **payload}

    monkeypatch.setattr(admin_routes.agent_drafts, "create", _create)
    monkeypatch.setattr(admin_routes.agent_drafts, "save", _update)
    monkeypatch.setattr(admin_routes.agent_drafts, "delete", lambda agent_id: agent_id == "agent_1")

    client = _admin_client()

    capabilities = client.get("/api/v1/dataagent/agents/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["skills"][0]["folder"] == "marketing-insights"
    assert capabilities.json()["mcp_servers"][0]["id"] == "web"

    listed = client.get("/api/v1/dataagent/agents")
    assert listed.status_code == 200
    assert listed.json()[0]["agent_id"] == "agent_1"
    assert listed.json()[0]["is_builtin"] is False

    created = client.post(
        "/api/v1/dataagent/agents",
        json={
            "name": "营销分析智能体",
            "allowed_tools": ["Skill", "Read"],
            "mcp_server_ids": ["web"],
            "skill_folders": ["marketing-insights"],
            "env_vars": {"SAFE_FLAG": "1"},
        },
    )
    assert created.status_code == 200
    assert calls["create"]["payload"]["name"] == "营销分析智能体"
    assert calls["create"]["available_skill_folders"] == {"marketing-insights"}

    detail = client.get("/api/v1/dataagent/agents/agent_1")
    assert detail.status_code == 200

    updated = client.put("/api/v1/dataagent/agents/agent_1", json={"description": "更新后", "expected_revision": 1})
    assert updated.status_code == 200
    assert calls["update"]["agent_id"] == "agent_1"
    assert updated.json()["description"] == "更新后"

    deleted = client.delete("/api/v1/dataagent/agents/agent_1")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "ok"


def test_agent_slash_commands_route_contract(monkeypatch):
    profile = {"agent_id": "agent_1", "name": "x", "skill_folders": ["marketing-insights", "platform-tools"]}
    monkeypatch.setattr(agent_profile_service, "get_agent_profile", lambda agent_id: profile if agent_id == "agent_1" else None)

    client = _admin_client()

    # Cold start: no run reported yet, fall back to the agent's enabled skill folders.
    fallback = client.get("/api/v1/dataagent/agents/agent_1/slash-commands")
    assert fallback.status_code == 200
    assert fallback.json() == {"slash_commands": ["marketing-insights", "platform-tools"], "source": "skills"}

    missing = client.get("/api/v1/dataagent/agents/unknown/slash-commands")
    assert missing.status_code == 404


class _FakeWidgetStore:
    """Records admin store calls so the route contract can be asserted without MySQL."""

    def __init__(self):
        self.list_calls = []
        self.message_calls = []

    def admin_list_topics(self, **kwargs):
        self.list_calls.append(kwargs)
        return {
            "items": [
                {
                    "topic_id": "topic_widget_1",
                    "title": "嵌入站会话",
                    "chat_topic_id": "chat_1",
                    "chat_conversation_id": "conv_1",
                    "agent_id": "agent_default",
                    "current_task_id": None,
                    "current_task_status": None,
                    "message_count": 4,
                    "last_message_preview": "最近一条",
                    "source": "widget",
                    "website_id": "site_a",
                    "external_user_id": "",
                    "visitor_id": "visitor_x",
                    "created_at": "2026-06-01T10:00:00",
                    "updated_at": "2026-06-01T12:00:00",
                }
            ],
            "total": 1,
            "page": kwargs.get("page", 1),
            "page_size": kwargs.get("page_size", 20),
        }

    def get_topic(self, topic_id, context=None):
        return {"topic_id": topic_id} if topic_id == "topic_widget_1" else None

    def list_topic_messages_page(self, *, topic_id, page, page_size, order, context=None):
        self.message_calls.append({"topic_id": topic_id, "context": context, "order": order})
        return {
            "topic_id": topic_id,
            "page": page,
            "page_size": page_size,
            "order": order,
            "total": 1,
            "items": [],
        }


def test_admin_topics_routes_contract(monkeypatch):
    store = _FakeWidgetStore()
    monkeypatch.setattr(admin_routes, "get_topic_task_store", lambda: store)

    client = _admin_client()

    listing = client.get(
        "/api/v1/nl2sql-admin/topics",
        params={"source": "widget", "user_id": 7, "keyword": "嵌入", "page": 1, "page_size": 20},
    )
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["source"] == "widget"
    # Admin listing filters by source and platform user only.
    assert store.list_calls[0]["source"] == "widget"
    assert store.list_calls[0]["user_id"] == 7
    assert store.list_calls[0]["keyword"] == "嵌入"

    messages = client.get("/api/v1/nl2sql-admin/topics/topic_widget_1/messages")
    assert messages.status_code == 200
    assert messages.json()["topic_id"] == "topic_widget_1"
    # Admin message read bypasses owner isolation via context=None.
    assert store.message_calls[0]["context"] is None

    missing = client.get("/api/v1/nl2sql-admin/topics/unknown/messages")
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# 统一认证与权限门禁
# ---------------------------------------------------------------------------


def test_admin_routes_require_admin_when_auth_enabled(monkeypatch):
    _acc_store, admin, alice = install_fake_accounts()
    store = _FakeWidgetStore()
    monkeypatch.setattr(admin_routes, "get_topic_task_store", lambda: store)
    monkeypatch.setattr(admin_routes, "list_documents", lambda: [])
    client = TestClient(app)

    # 未登录 → 401
    assert client.get("/api/v1/nl2sql-admin/settings").status_code == 401
    assert client.get("/api/v1/nl2sql-admin/topics").status_code == 401
    assert client.get("/api/v1/dataagent/skills/documents").status_code == 401
    assert client.post("/api/v1/dataagent/agents", json={"name": "x"}).status_code == 401

    # 普通用户 → 403（读 skills 有 menu:skills 放行，写 skills 需要 skills:manage 被拒）
    user_headers = bearer_headers(alice.id)
    assert client.get("/api/v1/nl2sql-admin/topics", headers=user_headers).status_code == 403
    assert client.get("/api/v1/dataagent/skills/documents", headers=user_headers).status_code == 200
    assert client.put(
        "/api/v1/dataagent/skills/runtime/demo",
        json={"enabled": True},
        headers=user_headers,
    ).status_code == 403

    # admin → 放行
    admin_headers = bearer_headers(admin.id)
    listing = client.get("/api/v1/nl2sql-admin/topics", headers=admin_headers)
    assert listing.status_code == 200
    assert store.list_calls[0]["source"] == ""


def test_agent_responses_are_layered_by_authentication(monkeypatch):
    _acc_store, admin, alice = install_fake_accounts()
    profile = {
        "agent_id": "agent_1",
        "name": "x",
        "description": "readable",
        "system_prompt": "internal instructions",
        "allowed_tools": ["Read"],
        "mcp_server_ids": ["web"],
        "skill_folders": ["demo"],
        "env_vars": {"SECRET_TOKEN": "hidden"},
        "visibility": {"mode": "all"},
    }
    monkeypatch.setattr(admin_routes, "list_agent_profiles", lambda: [profile])
    monkeypatch.setattr(agent_profile_service, "get_agent_profile", lambda agent_id: profile if agent_id == "agent_1" else None)
    client = TestClient(app)

    # 未登录 → 401（统一账号体系下不开放匿名访问）
    assert client.get("/api/v1/dataagent/agents").status_code == 401
    assert client.get("/api/v1/dataagent/agents/agent_1").status_code == 401
    assert client.get("/api/v1/dataagent/agents/agent_1/slash-commands").status_code == 401

    user_headers = bearer_headers(alice.id)
    # 普通用户目录 DTO 不泄露配置
    catalog_list = client.get("/api/v1/dataagent/agents", headers=user_headers)
    assert catalog_list.status_code == 200
    assert set(catalog_list.json()[0]) == {
        "agent_id", "name", "description", "is_default", "is_builtin", "preset_questions"
    }
    catalog_detail = client.get("/api/v1/dataagent/agents/agent_1", headers=user_headers)
    assert catalog_detail.status_code == 200
    assert "system_prompt" not in catalog_detail.json()
    assert "env_vars" not in catalog_detail.json()
    assert client.get("/api/v1/dataagent/agents/agent_1/slash-commands", headers=user_headers).status_code == 200

    # 普通用户读取完整只读配置，但 env_vars 始终排除
    readable = client.get("/api/v1/dataagent/agents/agent_1/profile", headers=user_headers)
    assert readable.status_code == 200
    assert readable.json()["system_prompt"] == "internal instructions"
    assert readable.json()["mcp_server_ids"] == ["web"]
    assert "env_vars" not in readable.json()

    # Removed configuration route returns 404
    assert client.get(
        "/api/v1/dataagent/agents/agent_1/configuration",
        headers=bearer_headers(admin.id),
    ).status_code == 404


def test_agents_capabilities_not_shadowed_by_public_dynamic_route(monkeypatch):
    """路由遮蔽回归：/agents/capabilities（admin 静态路径）必须先于动态 /agents/{agent_id} 匹配。"""
    _acc_store, admin, _alice = install_fake_accounts()
    monkeypatch.setattr(admin_routes, "list_documents", lambda: [])
    monkeypatch.setattr(
        admin_routes,
        "agent_capabilities",
        lambda documents: {"tools": [], "mcp_servers": [], "skills": [], "permission_modes": []},
    )
    monkeypatch.setattr(agent_profile_service, "get_agent_profile", lambda agent_id: None)
    client = TestClient(app)

    assert client.get("/api/v1/dataagent/agents/capabilities").status_code == 401
    response = client.get("/api/v1/dataagent/agents/capabilities", headers=bearer_headers(admin.id))
    assert response.status_code == 200
    assert "tools" not in response.json()


def test_admin_all_topics_forwards_filters(monkeypatch):
    _acc_store, admin, _alice = install_fake_accounts()
    store = _FakeWidgetStore()
    monkeypatch.setattr(admin_routes, "get_topic_task_store", lambda: store)
    client = TestClient(app)

    response = client.get(
        "/api/v1/nl2sql-admin/topics",
        params={"source": "web", "user_id": 42, "keyword": "趋势"},
        headers=bearer_headers(admin.id),
    )
    assert response.status_code == 200
    assert store.list_calls[0]["source"] == "web"
    assert store.list_calls[0]["user_id"] == 42
    assert store.list_calls[0]["keyword"] == "趋势"

    bad_source = client.get(
        "/api/v1/nl2sql-admin/topics",
        params={"source": "evil"},
        headers=bearer_headers(admin.id),
    )
    assert bad_source.status_code == 422


def test_agent_visibility_scope_enforced_across_read_tiers(monkeypatch):
    """可见范围矩阵：登录用户按 all / authenticated / selected（基于 da_agent_grant）过滤；
    admin 全量；不可见详情与 slash-commands 返回与不存在一致的 404。"""
    acc_store, admin, alice = install_fake_accounts()
    bob = acc_store.create_user(username="bob", password_hash="x", display_name="Bob")
    profiles = [
        {"agent_id": "agent_open", "name": "开放", "skill_folders": ["demo"], "visibility": {"mode": "all"}},
        {"agent_id": "agent_auth", "name": "仅登录", "visibility": {"mode": "authenticated"}},
        {"agent_id": "agent_sel", "name": "指定用户", "visibility": {"mode": "selected"}},
        {"agent_id": "agent_sel_other", "name": "指定他人", "visibility": {"mode": "selected"}},
    ]
    by_id = {item["agent_id"]: item for item in profiles}
    acc_store.set_agent_grants("agent_sel", user_ids=[alice.id], role_ids=[])
    acc_store.set_agent_grants("agent_sel_other", user_ids=[bob.id], role_ids=[])
    monkeypatch.setattr(admin_routes, "list_agent_profiles", lambda: list(profiles))
    monkeypatch.setattr(agent_profile_service, "get_agent_profile", lambda agent_id: by_id.get(agent_id))
    client = TestClient(app)

    user_headers = bearer_headers(alice.id)
    admin_headers = bearer_headers(admin.id)

    # 未登录 → 401
    assert client.get("/api/v1/dataagent/agents").status_code == 401

    # 登录普通用户：all + authenticated + selected 命中
    listed = client.get("/api/v1/dataagent/agents", headers=user_headers)
    assert listed.status_code == 200
    assert [item["agent_id"] for item in listed.json()] == ["agent_open", "agent_auth", "agent_sel"]
    assert client.get("/api/v1/dataagent/agents/agent_sel", headers=user_headers).status_code == 200
    assert client.get("/api/v1/dataagent/agents/agent_sel_other", headers=user_headers).status_code == 404
    assert client.get("/api/v1/dataagent/agents/agent_sel_other/slash-commands", headers=user_headers).status_code == 404

    # 登录用户读侧只暴露 visibility_mode 摘要
    readable_list = client.get("/api/v1/dataagent/agents/profiles", headers=user_headers)
    assert readable_list.status_code == 200
    readable_ids = {item["agent_id"]: item for item in readable_list.json()}
    assert set(readable_ids) == {"agent_open", "agent_auth", "agent_sel"}
    assert readable_ids["agent_sel"]["visibility_mode"] == "selected"
    assert "visibility" not in readable_ids["agent_sel"]
    assert client.get("/api/v1/dataagent/agents/agent_sel_other/profile", headers=user_headers).status_code == 404

    # 目录 DTO 不泄露额外字段
    assert set(listed.json()[0]) == {
        "agent_id", "name", "description", "is_default", "is_builtin", "preset_questions"
    }

    # admin：列表全量，profile 可读取他人授权的智能体；已移除的 configuration 返回 404
    admin_listed = client.get("/api/v1/dataagent/agents", headers=admin_headers)
    assert len(admin_listed.json()) == 4
    admin_profile = client.get("/api/v1/dataagent/agents/agent_sel_other/profile", headers=admin_headers)
    assert admin_profile.status_code == 200
    assert admin_profile.json()["visibility_mode"] == "selected"
    assert client.get("/api/v1/dataagent/agents/agent_sel_other/configuration", headers=admin_headers).status_code == 404


def test_admin_conversation_filter_uses_platform_users_route():
    _acc_store, admin, alice = install_fake_accounts()
    client = TestClient(app)

    assert client.get("/api/v1/nl2sql-admin/auth-users", headers=bearer_headers(admin.id)).status_code == 404
    assert client.get("/api/v1/dataagent/users").status_code == 401
    assert client.get("/api/v1/dataagent/users", headers=bearer_headers(alice.id)).status_code == 403

    response = client.get(
        "/api/v1/dataagent/users",
        params={"keyword": "ali", "page": 1, "page_size": 100},
        headers=bearer_headers(admin.id),
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == alice.id
    assert items[0]["username"] == "alice"
    assert items[0]["display_name"] == "Alice"


def test_task_system_prompt_route_contract(monkeypatch):
    record = {
        "task_id": "task_1",
        "prompt_sha256": "a" * 64,
        "byte_length": 12,
        "created_at": "2026-10-22T12:00:00",
        "content": "系统提示",
    }

    class _Store:
        def get_task_system_prompt(self, task_id):
            return record if task_id == "task_1" else None

    monkeypatch.setattr(admin_routes, "get_topic_task_store", lambda: _Store())
    client = _admin_client()

    found = client.get("/api/v1/dataagent/tasks/task_1/system-prompt")
    assert found.status_code == 200
    assert found.json() == record

    missing = client.get("/api/v1/dataagent/tasks/task_missing/system-prompt")
    assert missing.status_code == 404


def test_knowledge_admin_contract(monkeypatch):
    _acc_store, admin, alice = install_fake_accounts()
    calls = []
    base = {
        "experience_id": "exp1",
        "title": "标题",
        "when_text": "条件",
        "body": "内容",
        "scope": "global",
        "agent_id": None,
        "source": "manual",
        "status": "active",
    }

    def listing(**kwargs):
        calls.append(kwargs)
        return {"items": [base], "total": 1, "page": 1, "page_size": 20}

    monkeypatch.setattr(admin_routes.experience, "list_experiences", listing)
    monkeypatch.setattr(admin_routes.experience, "create_experience", lambda **kw: calls.append(kw) or base)
    monkeypatch.setattr(
        admin_routes.experience,
        "update_experience",
        lambda id, **kw: calls.append(kw) or (base if id == "exp1" else None),
    )
    monkeypatch.setattr(admin_routes.experience, "delete_experience", lambda id: id == "exp1")
    monkeypatch.setattr(
        admin_routes.experience,
        "injection_preview",
        lambda agent_id, **kw: {"prompt": "preview", "items": [base], "eligible": True, "reason": "eligible", **kw},
    )
    client = TestClient(app)
    root = "/api/v1/nl2sql-admin/experiences"
    endpoints = [
        ("get", root, None),
        ("get", root + "/injection-preview?agent_id=a", None),
        ("post", root, {"title": "t", "when_text": "w", "body": "b"}),
        ("patch", root + "/exp1", {"status": "disabled"}),
        ("delete", root + "/exp1", None),
    ]
    for method, path, payload in endpoints:
        kwargs = {"json": payload} if payload else {}
        assert getattr(client, method)(path, **kwargs).status_code == 401
        assert getattr(client, method)(path, headers=bearer_headers(alice.id), **kwargs).status_code == 403
    assert calls == []
    headers = bearer_headers(admin.id)
    response = client.get(root, params={"keyword": "标", "scope": "global"}, headers=headers)
    assert response.status_code == 200 and response.json()["items"][0]["body"] == "内容"
    assert response.json()["items"][0]["source"] == "manual"
    assert "kind" not in calls[-1] and calls[-1]["keyword"] == "标"
    assert client.post(root, json={"title": "t" * 41, "when_text": "w", "body": "b"}, headers=headers).status_code == 422
    assert client.post(root, json={"title": "t", "when_text": "w", "body": "b" * 20001}, headers=headers).status_code == 422
    assert client.post(root, json={"title": "t", "when_text": "w", "body": "b" * 20000}, headers=headers).status_code == 200
    assert calls[-1]["status"] == "active" and "kind" not in calls[-1] and "inject_mode" not in calls[-1]
    assert client.patch(root + "/exp1", json={"scope": "global", "title": "edited"}, headers=headers).status_code == 200
    assert set(calls[-1]) == {"scope", "title", "user_id"}
    assert client.patch(root + "/exp1", json={"status": "disabled"}, headers=headers).status_code == 200
    assert set(calls[-1]) == {"status", "user_id"}
    assert client.patch(root + "/missing", json={"status": "disabled"}, headers=headers).status_code == 404
    preview = client.get(root + "/injection-preview?agent_id=a", headers=headers).json()
    assert preview["prompt"] == "preview" and preview["eligible"] is True
    assert client.delete(root + "/exp1", headers=headers).status_code == 200
    assert client.delete(root + "/missing", headers=headers).status_code == 404


def test_admin_can_download_other_conversation_files_with_read_only_routes(monkeypatch, tmp_path):
    from core import topic_files

    _acc_store, admin, alice = install_fake_accounts()
    monkeypatch.setattr(admin_routes, "get_topic_task_store", lambda: _FakeWidgetStore())
    monkeypatch.setattr(topic_files, "_workspace_root", lambda topic_id: tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    (output / "report.html").write_text("<p>report</p>")
    (tmp_path / ".private").write_text("private")
    client = TestClient(app)
    url = "/api/v1/nl2sql-admin/topics/topic_widget_1/files"
    admin_headers = bearer_headers(admin.id)

    assert client.get(url).status_code == 401
    assert client.get(url + "/output/report.html", headers=bearer_headers(alice.id)).status_code == 403
    listing = client.get(url, headers=admin_headers)
    assert listing.status_code == 200
    assert "output/report.html" in {file["rel_path"] for file in listing.json()["files"]}
    response = client.get(url + "/output/report.html", headers=admin_headers)
    assert response.text == "<p>report</p>"
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(url + "/.private", headers=admin_headers).status_code == 404
    assert client.post(url, headers=admin_headers).status_code == 405
