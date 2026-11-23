import asyncio
from base64 import urlsafe_b64encode
import hashlib
from types import SimpleNamespace
import time
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import jwt
import pytest

from api import remote_mcp
from core import auth, remote_mcp_policy, remote_mcp_tokens
from main import app

ORIGIN = "https://agent.example:8443"
OTHER_ORIGIN = "https://other.example"
PREFIX = remote_mcp_policy.PREFIX
OAUTH = remote_mcp_policy.OAUTH_PREFIX
VERIFIER = "test-verifier-" * 4
CHALLENGE = urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
IDENTITY = auth.AuthIdentity("example:Alice", "Alice", "user", "example")
AUTHORIZATION = {"response_type": "code", "client_id": "dataagent", "redirect_uri": "http://127.0.0.1:8765/cb?hello=1",
                 "code_challenge": CHALLENGE, "code_challenge_method": "S256", "state": "state+with&symbols"}
BROWSER_HEADERS = {"X-DataAgent-Client": "dataagent", "Origin": ORIGIN}


class FakeRedis:
    def __init__(self):
        self.entries = {}
        self.expirations = {}

    async def set(self, key, value, *, ex, nx):
        assert nx
        self.entries[key] = value
        self.expirations[key] = ex
        return True

    async def getdel(self, key):
        return self.entries.pop(key, None)


class FakeRefreshStore:
    def __init__(self):
        self.entries = {}

    def save(self, token, grant):
        self.entries[token] = grant

    def rotate(self, token, client_id, replacement):
        grant = self.entries.get(token)
        if grant is None or grant["client_id"] != client_id:
            return None
        del self.entries[token]
        self.entries[replacement] = grant
        return grant


@pytest.fixture
def oauth(monkeypatch):
    settings = auth.AuthSettings(enabled=True, secret_key="test-key-" * 5,
                                 oauth=auth.OAuthSettings(provider_name="example", client_id="web", authorize_url="https://sso.example/auth",
                                                          token_url="https://sso.example/token"))
    fake_redis = FakeRedis()
    refresh_store = FakeRefreshStore()
    monkeypatch.setattr(auth, "get_auth_settings", lambda: settings)
    monkeypatch.setattr(remote_mcp_policy, "get_auth_settings", lambda: settings)
    monkeypatch.setattr(remote_mcp_tokens, "get_task_coordinator", lambda: SimpleNamespace(redis_client=fake_redis))
    monkeypatch.setattr(remote_mcp_tokens, "save_refresh_token", refresh_store.save)
    monkeypatch.setattr(remote_mcp_tokens, "rotate_refresh_token", refresh_store.rotate)
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    client.cookies.set(settings.cookie_name, auth.issue_session_token(IDENTITY))
    yield SimpleNamespace(client=client, redis=fake_redis, settings=settings, refresh=refresh_store)
    client.close()


def decide(oauth, decision="allow", **parameters):
    return oauth.client.post(f"{OAUTH}/authorize", headers=BROWSER_HEADERS,
                             json=AUTHORIZATION | parameters | {"decision": decision})


def authorization_code(oauth, **parameters):
    response = decide(oauth, **parameters)
    assert response.status_code == 200
    query = parse_qs(urlsplit(response.json()["redirect_url"]).query)
    return query["code"][0]


def exchange(oauth, code, **parameters):
    return oauth.client.post(f"{OAUTH}/token", data={"grant_type": "authorization_code", "code": code,
                             "client_id": AUTHORIZATION["client_id"], "redirect_uri": AUTHORIZATION["redirect_uri"],
                             "code_verifier": VERIFIER} | parameters)


def test_remote_tools_have_direct_structured_output():
    tools = {item.name: item for item in remote_mcp.create_server()._tool_manager.list_tools()}
    assert set(tools) == {"ask", "list_threads", "get_thread", "get_run", "feedback"}
    for tool in tools.values():
        assert tool.fn_metadata.output_schema["type"] == "object"


def test_mcp_token_maps_to_web_identity_with_current_role(oauth):
    token = exchange(oauth, authorization_code(oauth)).json()["access_token"]
    assert remote_mcp_policy.verify_identity(f"Bearer {token}", "agent_one", ORIGIN) == IDENTITY
    oauth.settings.admin_users.append(IDENTITY.user_id)
    assert remote_mcp_policy.verify_identity(f"Bearer {token}", "agent_two", ORIGIN).role == "admin"
    assert auth.verify_session_token(token) is None
    oauth.settings.oauth = auth.OAuthSettings()
    oauth.settings.local_admins = [{"username": "root"}]
    assert remote_mcp_policy.enabled()
    local = remote_mcp_policy.issue_access_token(ORIGIN, "local:root", "root", "dataagent", "dataagent:ask")
    assert remote_mcp_policy.verify_identity(f"Bearer {local}", "agent_one", ORIGIN).role == "admin"


def test_access_token_audience_issuer_expiry_and_scope(oauth):
    claims = {"iss": ORIGIN, "sub": IDENTITY.user_id, "name": IDENTITY.display_name, "client_id": "dataagent",
              "aud": remote_mcp_policy.endpoint(ORIGIN, "agent_one"), "scope": "dataagent:ask",
              "exp": int(time.time()) + 60}

    def token(**overrides):
        return "Bearer " + jwt.encode(claims | overrides, remote_mcp_policy._key("access"), algorithm="HS256")

    assert remote_mcp_policy.verify_identity(token(), "agent_one", ORIGIN) == IDENTITY
    for agent, bearer, expected in [
        ("agent_two", token(), "unauthorized"),
        ("agent_one", token(aud=f"{ORIGIN}/elsewhere"), "unauthorized"),
        ("agent_one", token(exp=1), "unauthorized"),
        ("agent_one", token(scope=None), "forbidden"),
        ("agent_one", token(iss=OTHER_ORIGIN), "unauthorized"),
        ("agent_one", token(sub=None), "unauthorized"),
    ]:
        with pytest.raises(remote_mcp_policy.RemoteMcpError) as exc:
            remote_mcp_policy.verify_identity(bearer, agent, ORIGIN)
        assert exc.value.code == expected
    no_scope = jwt.encode({key: value for key, value in claims.items() if key != "scope"},
                          remote_mcp_policy._key("access"), algorithm="HS256")
    with pytest.raises(remote_mcp_policy.RemoteMcpError) as exc:
        remote_mcp_policy.verify_identity(f"Bearer {no_scope}", "agent_one", ORIGIN)
    assert exc.value.code == "forbidden"
    with pytest.raises(remote_mcp_policy.RemoteMcpError):
        remote_mcp_policy.verify_identity(token(), "agent_one", OTHER_ORIGIN)


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


def test_metadata_documents_use_request_origin(oauth):
    oauth.client.cookies.clear()
    protected = oauth.client.get(f"/.well-known/oauth-protected-resource{PREFIX}/agent_one/mcp")
    assert protected.json() == {"resource": f"{ORIGIN}{PREFIX}/agent_one/mcp", "authorization_servers": [ORIGIN],
                                "scopes_supported": ["dataagent:ask"], "bearer_methods_supported": ["header"]}
    unauthorized = oauth.client.post(f"{PREFIX}/agent_one/mcp")
    assert unauthorized.status_code == 401
    assert str(protected.request.url) in unauthorized.headers["www-authenticate"]
    discovery = oauth.client.get("/.well-known/oauth-authorization-server")
    assert discovery.json() == oauth.client.get("/.well-known/openid-configuration").json() == {
        "issuer": ORIGIN, "authorization_endpoint": f"{ORIGIN}{OAUTH}/authorize", "token_endpoint": f"{ORIGIN}{OAUTH}/token",
        "registration_endpoint": f"{ORIGIN}{OAUTH}/register", "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"], "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"], "scopes_supported": ["dataagent:ask"],
    }
    oauth.settings.enabled = False
    assert oauth.client.get("/.well-known/oauth-authorization-server").status_code == 404


def test_stateless_public_registration_and_authorization(oauth):
    uris = ["http://localhost:7890/cb", "http://127.0.0.1:5678/cb", "http://[::1]:8765/cb",
            "https://client.example/cb", "example.desktop:/oauth/cb"]
    response = oauth.client.post(f"{OAUTH}/register", json={"client_name": "客户端", "redirect_uris": uris})
    assert response.status_code == 201
    registration = response.json()
    assert registration["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in registration
    assert remote_mcp_policy.client_registration(registration["client_id"]) == {"client_name": "客户端", "redirect_uris": uris}
    grant = AUTHORIZATION | {"client_id": registration["client_id"], "redirect_uri": uris[-1],
                             "resource": f"{ORIGIN}{PREFIX}/agent_one/mcp"}
    oauth.client.cookies.clear()
    response = oauth.client.get(f"{OAUTH}/authorize", params=grant)
    assert response.status_code == 302
    assert urlsplit(response.headers["location"]).path == "/mcp-authorize"
    assert parse_qs(urlsplit(response.headers["location"]).query) == {key: [value] for key, value in grant.items()}
    wrong_redirect = oauth.client.get(f"{OAUTH}/authorize", params=grant | {"redirect_uri": "https://other.example/cb"})
    assert wrong_redirect.status_code == 400 and "location" not in wrong_redirect.headers


@pytest.mark.parametrize("uri", ["javascript:alert(1)", "http://evil.example/cb", "file:///tmp/cb", "data:text/html,hi",
                                 "http://localhost@evil.example/cb", "https://client.example/cb#fragment"])
def test_register_rejects_invalid_redirects(oauth, uri):
    response = oauth.client.post(f"{OAUTH}/register", json={"redirect_uris": [uri]})
    assert response.status_code == 400 and response.json()["error"] == "invalid_redirect_uri"


@pytest.mark.parametrize(("overrides", "error"), [
    ({"client_id": "unknown"}, "invalid_client"),
    ({"redirect_uri": "http://evil.example/cb"}, "invalid_request"),
    ({"code_challenge": ""}, "invalid_request"),
    ({"code_challenge_method": "plain"}, "invalid_request"),
    ({"state": ""}, "invalid_request"),
    ({"scope": "openid"}, "invalid_scope"),
    ({"response_type": "token"}, "unsupported_response_type"),
    ({"resource": f"{OTHER_ORIGIN}{PREFIX}/agent_one/mcp"}, "invalid_target"),
])
def test_authorize_get_rejects_invalid_parameters(oauth, overrides, error):
    response = oauth.client.get(f"{OAUTH}/authorize", params=AUTHORIZATION | overrides)
    assert response.status_code == 400 and response.json()["error"] == error
    assert "location" not in response.headers


def test_authorize_post_requires_session_header_and_same_origin(oauth):
    data = AUTHORIZATION | {"decision": "allow"}
    assert oauth.client.post(f"{OAUTH}/authorize", json=data).status_code == 403
    assert oauth.client.post(f"{OAUTH}/authorize", json=data,
                             headers=BROWSER_HEADERS | {"Origin": OTHER_ORIGIN}).status_code == 403
    oauth.client.cookies.clear()
    assert oauth.client.post(f"{OAUTH}/authorize", json=data, headers=BROWSER_HEADERS).status_code == 401
    assert not oauth.redis.entries


def test_authorize_post_revalidates_and_returns_denial_or_single_use_code(oauth):
    invalid = decide(oauth, redirect_uri="javascript:alert(1)")
    assert invalid.status_code == 400
    denied = decide(oauth, "deny")
    assert denied.status_code == 200
    assert parse_qs(urlsplit(denied.json()["redirect_url"]).query) == {
        "hello": ["1"], "error": ["access_denied"], "state": [AUTHORIZATION["state"]],
    }
    assert not oauth.redis.entries
    allowed = decide(oauth)
    assert allowed.status_code == 200
    callback = urlsplit(allowed.json()["redirect_url"])
    query = parse_qs(callback.query)
    assert callback.netloc == "127.0.0.1:8765"
    assert query["hello"] == ["1"] and query["state"] == [AUTHORIZATION["state"]]
    assert oauth.redis.expirations == {f"dataagent:mcp:code:{query['code'][0]}": 300}


def test_token_exchange_and_reused_code(oauth):
    code = authorization_code(oauth)
    response = exchange(oauth, code)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    result = response.json()
    claims = jwt.decode(result["access_token"], remote_mcp_policy._key("access"), algorithms=["HS256"],
                        issuer=ORIGIN, audience=f"{ORIGIN}{PREFIX}")
    assert result["token_type"] == "Bearer" and result["expires_in"] == 3600 and result["scope"] == "dataagent:ask"
    assert claims["sub"] == IDENTITY.user_id and claims["name"] == IDENTITY.display_name
    assert claims["client_id"] == "dataagent" and "role" not in claims
    assert result["refresh_token"] in oauth.refresh.entries
    replay = exchange(oauth, code)
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"


def test_token_endpoint_rejects_json_and_http_client_authentication(oauth):
    malformed = oauth.client.post(f"{OAUTH}/token", json={"grant_type": "authorization_code"})
    assert malformed.status_code == 400 and malformed.json()["error"] == "invalid_request"
    authenticated = oauth.client.post(f"{OAUTH}/token", headers={"Authorization": "Basic ZGF0YWFnZW50Og=="},
                                      data={"grant_type": "authorization_code"})
    assert authenticated.status_code == 401 and authenticated.json()["error"] == "invalid_client"
    assert authenticated.headers["www-authenticate"].startswith("Basic ")


@pytest.mark.parametrize("overrides", [{"code_verifier": "x" * 43}, {"redirect_uri": "http://localhost/cb"}])
def test_token_exchange_rejects_wrong_verifier_or_redirect(oauth, overrides):
    response = exchange(oauth, authorization_code(oauth), **overrides)
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"
    assert not oauth.refresh.entries


def test_refresh_rotates_and_cannot_be_used_on_another_origin(oauth):
    first = exchange(oauth, authorization_code(oauth)).json()
    form = {"grant_type": "refresh_token", "client_id": "dataagent", "refresh_token": first["refresh_token"]}
    foreign = oauth.client.post(f"{OTHER_ORIGIN}{OAUTH}/token", data=form)
    assert foreign.status_code == 400 and foreign.json()["error"] == "invalid_grant"
    rotated = oauth.client.post(f"{OAUTH}/token", data=form)
    assert rotated.status_code == 200 and rotated.json()["refresh_token"] != first["refresh_token"]
    assert rotated.json()["access_token"] != first["access_token"]
    assert remote_mcp_policy.verify_identity(f"Bearer {rotated.json()['access_token']}", "agent_one", ORIGIN) == IDENTITY
    rejected = oauth.client.post(f"{OAUTH}/token", data=form)
    assert rejected.status_code == 400 and rejected.json()["error"] == "invalid_grant"


def test_legacy_discovery_setting_is_ignored():
    settings = auth._parse_oauth_providers([{"name": "example", "remote_app": {
        "client_id": "web", "client_secret": "web-secret", "authorize_url": "https://sso.example/auth",
        "access_token_url": "https://sso.example/token", "api_base_url": "https://sso.example/",
        "redirect_uri": "https://agent.example/oauth-authorized/example", "server_metadata_url": "not a URL",
    }}])
    assert settings.configured and not hasattr(settings, "server_metadata_url")
