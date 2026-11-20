import asyncio
from types import SimpleNamespace
import time

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest

from api import remote_mcp
from core import remote_mcp_policy


def test_remote_tools_have_direct_structured_output():
    tools = {item.name: item for item in remote_mcp.create_server()._tool_manager.list_tools()}
    assert set(tools) == {"ask", "list_threads", "get_thread", "get_run", "feedback"}
    for tool in tools.values():
        assert tool.fn_metadata.output_schema["type"] == "object"


def test_mcp_token_maps_to_the_platform_identity_of_a_web_login(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    origin, issuer = "https://agent.example", "https://sso.example"
    cfg = SimpleNamespace(remote_mcp_public_origin=origin, remote_mcp_oauth_issuer=issuer,
                          remote_mcp_oauth_jwks_url=f"{issuer}/keys", remote_mcp_oauth_subject_claim="employee")
    monkeypatch.setattr(remote_mcp_policy, "get_settings", lambda: cfg)
    monkeypatch.setattr(remote_mcp_policy, "get_auth_settings",
                        lambda: SimpleNamespace(oauth=SimpleNamespace(provider_name="example")))
    monkeypatch.setattr(remote_mcp_policy, "resolve_oauth_role",
                        lambda provider, subject: "admin" if subject == "Root" else "user")
    monkeypatch.setattr(remote_mcp_policy, "_jwks", lambda _: SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())))
    claims = {"iss": issuer, "sub": "oauth-pairwise-subject", "name": "Alice",
              "aud": remote_mcp_policy.endpoint("agent_one"), "scope": "dataagent:ask openid",
              "employee": "Alice", "exp": int(time.time()) + 60}

    def token(**overrides):
        return "Bearer " + jwt.encode(claims | overrides, key, algorithm="RS256")

    identity = remote_mcp_policy.verify_identity(token(), "agent_one")
    assert (identity.user_id, identity.display_name, identity.role) == ("example:Alice", "Alice", "user")
    assert remote_mcp_policy.verify_identity(token(employee="Root"), "agent_one").role == "admin"
    # An IdP without RFC 8707 issues the shared audience, valid for any assistant URL.
    shared = token(aud="https://agent.example/api/v1/remote-agents")
    assert remote_mcp_policy.verify_identity(shared, "agent_two").user_id == "example:Alice"
    for agent, auth, expected in [
        ("agent_two", token(), "unauthorized"),        # audience is the other assistant's URL
        ("agent_one", token(aud="https://agent.example/elsewhere"), "unauthorized"),
        ("agent_one", token(exp=1), "unauthorized"),
        ("agent_one", token(scope="openid"), "forbidden"),
        ("agent_one", token(employee=None), "unauthorized"),
    ]:
        with pytest.raises(remote_mcp_policy.RemoteMcpError) as exc:
            remote_mcp_policy.verify_identity(auth, agent)
        assert exc.value.code == expected


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
    monkeypatch.setattr(remote_mcp, "get_settings", lambda: SimpleNamespace(remote_mcp_public_origin="https://agent.example"))
    token = remote_mcp._caller.set(("a1", ALICE))
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
