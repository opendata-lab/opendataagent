"""Execution identity overrides configured headers on every HTTP MCP."""
from types import SimpleNamespace
from core import agent_identity, mcp_admin_service


def test_all_remote_transports_receive_trusted_identity(monkeypatch):
    rows = [
        {"server_id": name, "enabled": True, "transport": kind, "url": "https://example.test/mcp", "headers": {
            "x-agent-user-id": "spoof", "X-AGENT-ID": "spoof", "Authorization": "Bearer configured",
        }} for name, kind in [("third-party", "http"), ("events", "sse")]
    ]
    monkeypatch.setattr(mcp_admin_service, "get_runtime_registry_store", lambda: SimpleNamespace(list_mcp_servers=lambda: rows))
    token = agent_identity.set_execution_identity(SimpleNamespace(auth_user_id="local:alice", agent_snapshot={"agent_id": "agent_custom"}))
    try:
        servers = mcp_admin_service.resolve_runtime_mcp_servers(["third-party", "events"])
        for config in servers.values():
            assert config["url"] == "https://example.test/mcp"
            assert config["headers"] == {"Authorization": "Bearer configured", "X-Agent-User-Id": "local:alice", "X-Agent-Id": "agent_custom"}
    finally:
        agent_identity._identity.reset(token)
