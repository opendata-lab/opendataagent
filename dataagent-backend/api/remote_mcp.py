"""A published assistant as a Streamable HTTP MCP server.

Each tool is a thin shell over the same store and submission path the web chat
uses: the caller's token maps to their platform identity, conversations are
their ordinary conversations, and a question becomes an ordinary task that
simply cannot ask anything back (interactive=False).
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
import hashlib
import re
from time import monotonic
from typing import Annotated, Any, Literal
from urllib.parse import quote, unquote_plus, urlencode

import pymysql
from fastapi import APIRouter, Depends, HTTPException, Request
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route, Router

from api.routes import _require_agent_profile
from api.topic_file_response import topic_file_response
from core import remote_mcp_policy as policy
from core import remote_mcp_tokens as tokens
from core.agent_profile_service import build_agent_snapshot
from core.auth import AuthIdentity, require_identity
from core.task_status import ACTIVE_TASK_STATUSES
from core.task_submission_service import submit_message_task
from core.topic_task_store import get_topic_task_store

router = APIRouter()
# (agent ID, request context, the public origin the caller used)
_caller: ContextVar[tuple[str, dict[str, str], str]] = ContextVar("published_mcp_caller")
Identifier = Annotated[str, Field(min_length=1, max_length=64)]
RequestKey = Annotated[str, Field(min_length=8, max_length=128)]
Question = Annotated[str, Field(min_length=1, max_length=16384)]
Page = Annotated[int, Field(ge=1, le=10000)]
PageSize = Annotated[int, Field(ge=1, le=30)]
Search = Annotated[str, Field(max_length=100)]
WAIT_SECONDS = 8.0
_PUBLIC_STATUS = {"finished": "answered", "error": "failed", "suspended": "cancelled"}
_OAUTH_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_CONSENT_HEADERS = {**_OAUTH_HEADERS, "Referrer-Policy": "no-referrer"}
_MAX_CLIENT_ID_LENGTH = 3072


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": "failed", "error": {"code": code, "message": message}, **extra}


def _context(identity: AuthIdentity) -> dict[str, str]:
    # The same shape the web request context has, with source 'remote' so a
    # conversation started here is labelled MCP.
    return {"source": "remote", "website_id": "", "external_user_id": "", "visitor_id": "",
            "auth_user_id": identity.user_id, "auth_display_name": identity.display_name,
            "auth_role": identity.role}


def _thread(thread_id: str) -> dict[str, Any]:
    agent_id, context, _ = _caller.get()
    topic = get_topic_task_store().get_topic(thread_id, context=context)
    if not topic or topic.get("agent_id") != agent_id:
        raise policy.RemoteMcpError("not_found", "找不到该会话")
    return topic


def _file_url(topic_id: str, rel_path: str) -> str:
    agent_id, _, origin = _caller.get()
    return f"{origin}{policy.PREFIX}/{agent_id}/threads/{topic_id}/files/{quote(rel_path)}"


def _run(run_id: str) -> dict[str, Any]:
    store = get_topic_task_store()
    task = store.get_task(run_id)
    if not task:
        raise policy.RemoteMcpError("not_found", "找不到该任务")
    topic_id = str(task["topic_id"])
    _thread(topic_id)
    ids = {"run_id": run_id, "thread_id": topic_id}
    status = str(task.get("task_status") or "")
    if status in ACTIVE_TASK_STATUSES:
        return {**ids, "status": "in_progress", "retry_after_ms": 2000}
    page = store.list_topic_messages_page(topic_id=topic_id, page=1, page_size=50, order="desc",
                                          context=_caller.get()[1])
    answer = next((m for m in page["items"] if m.get("task_id") == run_id and m.get("sender_type") == "assistant"), {})
    result = {**ids, "status": _PUBLIC_STATUS.get(status, "failed"), "message_id": answer.get("message_id", ""),
              "answer": answer.get("content", "")}
    if status == "finished":
        result["files"] = [{"name": item.get("name"), "rel_path": item.get("rel_path"), "size": item.get("size"),
                            "download_url": _file_url(topic_id, str(item.get("rel_path") or ""))}
                           for item in answer.get("attachments") or []]
    else:
        result["error"] = task.get("error") or {"code": "run_failed", "message": "本次运行未完成，请重新发问。"}
    return result


async def ask(question: Question, request_key: RequestKey, thread_id: Identifier | None = None) -> dict[str, Any]:
    """Ask the connected assistant. Reuse the request key on retry and pass the thread ID for follow-ups."""
    agent_id, context, _ = _caller.get()
    store = get_topic_task_store()
    question = question.strip()
    # Keys are scoped to the caller and assistant; the stored task holds the
    # question and thread, so a retry is recognised without another table.
    key = hashlib.sha256(f"{agent_id}\n{context['auth_user_id']}\n{request_key}".encode()).hexdigest()
    try:
        previous = await asyncio.to_thread(store.find_task_by_request_key, key)
        if previous:
            if previous.get("prompt") != question or (thread_id and previous.get("topic_id") != thread_id):
                return _error("request_conflict", "此请求键已用于其他问题")
            return await asyncio.to_thread(_run, previous["task_id"])
        created = False
        if thread_id:
            topic = await asyncio.to_thread(_thread, thread_id)
            if topic.get("current_task_status") in ACTIVE_TASK_STATUSES:
                return _error("thread_busy", "此会话已有任务在运行，请等待完成后重试",
                              thread_id=thread_id, run_id=topic.get("current_task_id"), retry_after_ms=2000)
        else:
            profile = _require_agent_profile(agent_id, context=context)
            topic = await asyncio.to_thread(store.create_topic, title=question[:60],
                                            agent_snapshot=build_agent_snapshot(profile), context=context)
            created = True
        try:
            submitted = await submit_message_task(topic_id=topic["topic_id"], message_type="text",
                                                  message_content=question, agent_id=agent_id,
                                                  interactive=False, request_key=key)
        except pymysql.err.IntegrityError:
            # A concurrent retry with the same key won; answer with its task.
            if created:
                await asyncio.to_thread(store.delete_topic, topic["topic_id"])
            previous = await asyncio.to_thread(store.find_task_by_request_key, key)
            return await asyncio.to_thread(_run, previous["task_id"])
        run_id = submitted["task_id"]
        until = monotonic() + WAIT_SECONDS
        result = await asyncio.to_thread(_run, run_id)
        while result["status"] == "in_progress" and monotonic() < until:
            await asyncio.sleep(0.5)
            result = await asyncio.to_thread(_run, run_id)
        return result
    except HTTPException:
        return _error("not_found", "找不到可访问的助手")
    except policy.RemoteMcpError as exc:
        return _error(exc.code, str(exc))


async def list_threads(query: Search = "", page: Page = 1, page_size: PageSize = 10) -> dict[str, Any]:
    """Find this user's conversations with this assistant (web, Widget and MCP), optionally by title."""
    agent_id, context, _ = _caller.get()
    topics = await asyncio.to_thread(get_topic_task_store().list_topics, context=context, agent_id=agent_id)
    query = query.strip()
    matched = [t for t in topics if not query or query in str(t.get("title") or "")]
    start = (page - 1) * page_size
    return {"threads": [{"thread_id": t["topic_id"], "title": t.get("title"), "source": t.get("source"),
                         "updated_at": t.get("updated_at")} for t in matched[start:start + page_size]],
            "page": page, "has_more": len(matched) > start + page_size}


async def get_thread(thread_id: Identifier, page: Page = 1, page_size: PageSize = 10) -> dict[str, Any]:
    """Read a conversation's messages, newest first. Pass its thread ID to ask to continue it."""
    try:
        topic = await asyncio.to_thread(_thread, thread_id)
    except policy.RemoteMcpError as exc:
        return _error(exc.code, str(exc))
    data = await asyncio.to_thread(get_topic_task_store().list_topic_messages_page, topic_id=thread_id,
                                   page=page, page_size=page_size, order="desc", context=_caller.get()[1])
    return {"thread_id": thread_id, "title": topic.get("title"), "source": topic.get("source"),
            "messages": [{"message_id": m["message_id"], "role": m["sender_type"], "content": m["content"]}
                         for m in data["items"] if m.get("sender_type") in {"user", "assistant"}],
            "page": page, "has_more": data["total"] > page * page_size}


async def get_run(run_id: Identifier) -> dict[str, Any]:
    """Get the state, answer and file links of a previously accepted question."""
    try:
        return await asyncio.to_thread(_run, run_id)
    except policy.RemoteMcpError as exc:
        return _error(exc.code, str(exc))


async def feedback(thread_id: Identifier, message_id: Identifier, rating: Literal["like", "dislike"]) -> dict[str, Any]:
    """Record the user's explicit rating of a specific answer."""
    try:
        await asyncio.to_thread(_thread, thread_id)
    except policy.RemoteMcpError as exc:
        return _error(exc.code, str(exc))
    updated = await asyncio.to_thread(get_topic_task_store().update_message_feedback, topic_id=thread_id,
                                      message_id=message_id, feedback=rating, context=_caller.get()[1])
    if not updated:
        return _error("not_found", "找不到可评价的回答")
    return {"message_id": message_id, "rating": rating}


def create_server() -> FastMCP:
    # Origin is checked against the address the caller used, in _authenticate.
    server = FastMCP("DataAgent assistant", stateless_http=True, json_response=True,
                     transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    server.add_tool(ask, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
    server.add_tool(list_threads, annotations=ToolAnnotations(readOnlyHint=True))
    server.add_tool(get_thread, annotations=ToolAnnotations(readOnlyHint=True))
    server.add_tool(get_run, annotations=ToolAnnotations(readOnlyHint=True))
    server.add_tool(feedback, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
    server.streamable_http_app()
    return server


def _origin(request: StarletteRequest) -> str:
    # The address the caller used; the proxy keeps Host and X-Forwarded-Proto.
    return f"{request.url.scheme}://{request.url.netloc}"


def _metadata_url(origin: str, agent_id: str) -> str:
    return f"{origin}/.well-known/oauth-protected-resource{policy.PREFIX}/{agent_id}/mcp"


def _published(agent_id: str, identity: AuthIdentity) -> dict[str, str]:
    """The caller's request context, if this assistant is published over MCP and visible to them."""
    context = _context(identity)
    try:
        profile = _require_agent_profile(agent_id, context=context)
    except HTTPException as exc:
        raise policy.RemoteMcpError("not_found", "找不到可访问的 MCP 助手") from exc
    if not profile.get("remote_mcp_enabled"):
        raise policy.RemoteMcpError("not_found", "找不到可访问的 MCP 助手")
    return context


async def _authenticate(request: StarletteRequest, agent_id: str) -> dict[str, str]:
    if not policy.enabled():
        raise HTTPException(404)
    origin = _origin(request)
    if request.headers.get("origin", origin) != origin:
        raise HTTPException(403)
    try:
        identity = await asyncio.to_thread(policy.verify_identity, request.headers.get("authorization", ""),
                                           agent_id, origin)
        return await asyncio.to_thread(_published, agent_id, identity)
    except policy.RemoteMcpError as exc:
        if exc.code == "unauthorized":
            raise HTTPException(401, str(exc), headers={
                "WWW-Authenticate": f'Bearer resource_metadata="{_metadata_url(origin, agent_id)}", scope="{policy.SCOPE}"',
            }) from exc
        if exc.code == "forbidden":
            raise HTTPException(403, str(exc), headers={
                "WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{policy.SCOPE}"',
            }) from exc
        raise HTTPException(404, str(exc)) from exc


class RemoteTransport:
    async def __call__(self, scope, receive, send) -> None:
        request = StarletteRequest(scope)
        agent_id = request.path_params["agent_id"]
        try:
            context = await _authenticate(request, agent_id)
        except (HTTPException, policy.RemoteMcpError) as exc:
            status = exc.status_code if isinstance(exc, HTTPException) else 404
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            headers = exc.headers if isinstance(exc, HTTPException) else None
            await JSONResponse({"detail": detail}, status, headers)(scope, receive, send)
            return
        token = _caller.set((agent_id, context, _origin(request)))
        try:
            await scope["app"].state.remote_mcp.session_manager.handle_request(scope, receive, send)
        finally:
            _caller.reset(token)


transport = Router(routes=[Route("/{agent_id}/mcp", RemoteTransport())], redirect_slashes=False)


@router.get("/.well-known/oauth-protected-resource" + policy.PREFIX + "/{agent_id}/mcp")
def resource_metadata(agent_id: str, request: Request):
    origin = _origin(request)
    resource = f"{origin}{policy.PREFIX}/{agent_id}/mcp"
    if not policy.enabled() or not policy.valid_resource(origin, resource):
        raise HTTPException(404)
    return {"resource": resource, "authorization_servers": [origin],
            "scopes_supported": [policy.SCOPE], "bearer_methods_supported": ["header"]}


def _require_oauth_enabled() -> None:
    if not policy.enabled():
        raise HTTPException(404)


def _oauth_error(exc: policy.RemoteMcpError, *, browser: bool = False, auth_scheme: str = "") -> JSONResponse:
    headers = _CONSENT_HEADERS if browser else _OAUTH_HEADERS
    status = 503 if exc.code == "temporarily_unavailable" else 400
    if auth_scheme:
        headers = {**headers, "WWW-Authenticate": f'{auth_scheme} realm="DataAgent MCP"'}
        status = 401
    return JSONResponse({"error": exc.code, "error_description": str(exc)}, status_code=status, headers=headers)


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/openid-configuration")
def authorization_metadata(request: Request):
    _require_oauth_enabled()
    origin = _origin(request)
    base = origin + policy.OAUTH_PREFIX
    return {"issuer": origin, "authorization_endpoint": f"{base}/authorize", "token_endpoint": f"{base}/token",
            "registration_endpoint": f"{base}/register", "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": [policy.SCOPE]}


async def _json_object(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError as exc:
        raise policy.RemoteMcpError("invalid_request", "Expected a JSON object.") from exc
    if not isinstance(data, dict):
        raise policy.RemoteMcpError("invalid_request", "Expected a JSON object.")
    return data


@router.post(policy.OAUTH_PREFIX + "/register")
async def register_client(request: Request):
    _require_oauth_enabled()
    try:
        data = await _json_object(request)
        redirect_uris = data.get("redirect_uris")
        if not isinstance(redirect_uris, list) or not redirect_uris or not all(map(policy.valid_redirect_uri, redirect_uris)):
            raise policy.RemoteMcpError("invalid_redirect_uri", "Registration requires valid redirect URIs.")
        name = data.get("client_name", policy.PUBLIC_CLIENT_ID)
        grant_types = data.get("grant_types", ["authorization_code", "refresh_token"])
        if (not isinstance(name, str) or not name.strip() or len(name) > 255
                or data.get("token_endpoint_auth_method", "none") != "none"
                or data.get("response_types", ["code"]) != ["code"]
                or not isinstance(grant_types, list) or "authorization_code" not in grant_types
                or any(grant not in ("authorization_code", "refresh_token") for grant in grant_types)):
            raise policy.RemoteMcpError("invalid_client_metadata", "Only public authorization code clients are supported.")
        client_id = policy.register_client(name, redirect_uris)
        if len(client_id) > _MAX_CLIENT_ID_LENGTH:
            raise policy.RemoteMcpError("invalid_client_metadata", "Client registration is too long.")
    except policy.RemoteMcpError as exc:
        return _oauth_error(exc)
    return JSONResponse({"client_id": client_id, "client_name": name, "redirect_uris": redirect_uris,
                         "token_endpoint_auth_method": "none", "response_types": ["code"],
                         "grant_types": grant_types}, status_code=201, headers=_OAUTH_HEADERS)


def _parameter(data, name: str, *, default: str = "", limit: int = 2048) -> str:
    value = data.get(name, default)
    if not isinstance(value, str) or len(value) > limit:
        raise policy.RemoteMcpError("invalid_request", f"Invalid {name} parameter.")
    return value


def _single_values(data) -> dict:
    values = dict(data)
    if len(data.multi_items()) != len(values):
        raise policy.RemoteMcpError("invalid_request", "Request contains duplicate parameters.")
    return values


def _callback_url(redirect_uri: str, state: str, **result: str) -> str:
    base, _, query = redirect_uri.partition("?")
    reserved = {"code", "error", "error_description", "error_uri", "state"}
    existing = [part for part in query.split("&") if part and unquote_plus(part.partition("=")[0]) not in reserved]
    return base + "?" + "&".join([*existing, urlencode({**result, "state": state})])


def _validate_resource(data: dict, origin: str) -> None:
    if "resource" in data and not policy.valid_resource(origin, _parameter(data, "resource")):
        raise policy.RemoteMcpError("invalid_target", "Invalid resource URI.")


def _authorization_grant(data: dict, origin: str) -> dict:
    client_id = _parameter(data, "client_id", limit=_MAX_CLIENT_ID_LENGTH)
    redirect_uri = _parameter(data, "redirect_uri")
    policy.validate_client(client_id, redirect_uri)
    if _parameter(data, "response_type") != "code":
        raise policy.RemoteMcpError("unsupported_response_type", "Only response_type=code is supported.")
    challenge = _parameter(data, "code_challenge")
    if not policy.valid_challenge(challenge, _parameter(data, "code_challenge_method")):
        raise policy.RemoteMcpError("invalid_request", "An S256 PKCE challenge is required.")
    state = _parameter(data, "state")
    if not state.strip() or not re.fullmatch(r"[\x20-\x7e]+", state):
        raise policy.RemoteMcpError("invalid_request", "A valid state parameter is required.")
    scope = _parameter(data, "scope", default=policy.SCOPE)
    if scope != policy.SCOPE:
        raise policy.RemoteMcpError("invalid_scope", "Unknown scope.")
    _validate_resource(data, origin)
    return {"client_id": client_id, "redirect_uri": redirect_uri, "challenge": challenge,
            "state": state, "scope": scope, "origin": origin}


@router.get(policy.OAUTH_PREFIX + "/authorize")
def authorize_client(request: Request):
    _require_oauth_enabled()
    try:
        _authorization_grant(_single_values(request.query_params), _origin(request))
    except policy.RemoteMcpError as exc:
        return _oauth_error(exc, browser=True)
    return RedirectResponse(f"/mcp-authorize?{request.url.query}", status_code=302, headers=_CONSENT_HEADERS)


@router.post(policy.OAUTH_PREFIX + "/authorize", dependencies=[Depends(_require_oauth_enabled)])
async def decide_authorization(request: Request, identity: Annotated[AuthIdentity, Depends(require_identity)]):
    origin = _origin(request)
    if (request.headers.get("X-DataAgent-Client") != "dataagent"
            or request.headers.get("Origin", origin) != origin):
        raise HTTPException(403, "请求必须来自 DataAgent 网页")
    try:
        data = await _json_object(request)
        grant = _authorization_grant(data, origin)
        decision = _parameter(data, "decision")
        state = grant.pop("state")
        if decision == "allow":
            code = await tokens.create_code({**grant, "user_id": identity.user_id, "display_name": identity.display_name})
            url = _callback_url(grant["redirect_uri"], state, code=code)
        elif decision == "deny":
            url = _callback_url(grant["redirect_uri"], state, error="access_denied")
        else:
            raise policy.RemoteMcpError("invalid_request", "Invalid consent decision.")
    except policy.RemoteMcpError as exc:
        return _oauth_error(exc, browser=True)
    return JSONResponse({"redirect_url": url}, headers=_CONSENT_HEADERS)


@router.post(policy.OAUTH_PREFIX + "/token")
async def exchange_token(request: Request):
    _require_oauth_enabled()
    origin = _origin(request)
    if "authorization" in request.headers:
        scheme = request.headers["authorization"].split(" ", 1)[0]
        return _oauth_error(policy.RemoteMcpError("invalid_client", "Client authentication is not supported."),
                            auth_scheme=scheme if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", scheme) else "Basic")
    try:
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
            raise policy.RemoteMcpError("invalid_request", "Expected a URL-encoded form.")
        async with request.form() as form:
            data = _single_values(form)
        grant_type = _parameter(data, "grant_type")
        client_id = _parameter(data, "client_id", limit=_MAX_CLIENT_ID_LENGTH)
        if not grant_type or not client_id:
            raise policy.RemoteMcpError("invalid_request", "The grant type and client ID are required.")
        if "client_secret" in data:
            raise policy.RemoteMcpError("invalid_client", "Client authentication is not supported.")
        policy.client_registration(client_id)
        _validate_resource(data, origin)
        refresh = policy.issue_refresh_token(origin)
        if grant_type == "authorization_code":
            code = _parameter(data, "code")
            redirect_uri = _parameter(data, "redirect_uri")
            verifier = _parameter(data, "code_verifier")
            if not code or not redirect_uri or not verifier:
                raise policy.RemoteMcpError("invalid_request", "The code, redirect URI and PKCE verifier are required.")
            grant = await tokens.consume_code(code)
            if (grant is None or grant["origin"] != origin or grant["client_id"] != client_id
                    or grant["redirect_uri"] != redirect_uri or not policy.verify_pkce(verifier, grant["challenge"])):
                raise policy.RemoteMcpError("invalid_grant", "Authorization code is invalid or already used.")
            await asyncio.to_thread(tokens.save_refresh_token, refresh, grant)
        elif grant_type == "refresh_token":
            previous = _parameter(data, "refresh_token")
            if not previous:
                raise policy.RemoteMcpError("invalid_request", "A refresh token is required.")
            if _parameter(data, "scope", default=policy.SCOPE) != policy.SCOPE:
                raise policy.RemoteMcpError("invalid_scope", "The refresh token scope cannot be changed.")
            grant = None
            if policy.valid_refresh_token(previous, origin):
                grant = await asyncio.to_thread(tokens.rotate_refresh_token, previous, client_id, refresh)
            if grant is None:
                raise policy.RemoteMcpError("invalid_grant", "Refresh token is invalid or already used.")
        else:
            raise policy.RemoteMcpError("unsupported_grant_type", "Unsupported grant type.")
    except policy.RemoteMcpError as exc:
        return _oauth_error(exc)
    except pymysql.MySQLError:
        return _oauth_error(policy.RemoteMcpError("temporarily_unavailable", "Refresh token storage is unavailable."))
    access = policy.issue_access_token(origin, grant["user_id"], grant["display_name"], client_id, grant["scope"])
    return JSONResponse({"access_token": access, "token_type": "Bearer", "expires_in": policy.ACCESS_TTL,
                         "refresh_token": refresh, "scope": grant["scope"]}, headers=_OAUTH_HEADERS)


@router.get(policy.PREFIX + "/{agent_id}/threads/{thread_id}/files/{rel_path:path}")
async def download(agent_id: str, thread_id: str, rel_path: str, request: Request):
    """The same workspace file the web panel serves, behind the MCP token."""
    context = await _authenticate(request, agent_id)
    topic = await asyncio.to_thread(get_topic_task_store().get_topic, thread_id, context=context)
    if not topic or topic.get("agent_id") != agent_id:
        raise HTTPException(404)
    return topic_file_response(thread_id, rel_path, download=True)
