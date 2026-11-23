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
from time import monotonic
from typing import Annotated, Any, Literal
from urllib.parse import quote

import pymysql
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, StringConstraints, field_validator
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse
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
_TOKEN_MANAGEMENT_PATH = "/api/v1/dataagent/mcp-tokens"


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
        identity = await asyncio.to_thread(policy.verify_identity, request.headers.get("authorization", ""))
        policy.endpoint(origin, agent_id)
        return await asyncio.to_thread(_published, agent_id, identity)
    except policy.RemoteMcpError as exc:
        if exc.code == "unauthorized":
            raise HTTPException(401, str(exc), headers={
                "WWW-Authenticate": 'Bearer realm="DataAgent MCP"',
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


class CreateMcpTokenRequest(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    expires_in_days: Literal[30, 90, 180] | None = 90

    @field_validator("expires_in_days", mode="before")
    @classmethod
    def require_integer_days(cls, value: Any) -> Any:
        if value is not None and type(value) is not int:
            raise ValueError("有效期必须是整数天数或 null")
        return value


def _require_mcp_enabled() -> None:
    if not policy.enabled():
        raise HTTPException(404)


def _token_user(
    request: Request,
    _: Annotated[None, Depends(_require_mcp_enabled)],
    identity: Annotated[AuthIdentity, Depends(require_identity)],
) -> AuthIdentity:
    origin = request.headers.get("Origin")
    if (request.method != "GET" or origin is not None) and origin != _origin(request):
        raise HTTPException(403, "请求必须来自 DataAgent 网页")
    return identity


TokenUser = Annotated[AuthIdentity, Depends(_token_user)]


@router.get(_TOKEN_MANAGEMENT_PATH)
def list_mcp_tokens(identity: TokenUser, scope: Literal["mine", "all"] = "mine"):
    if scope == "all" and not identity.is_admin:
        raise HTTPException(403, "Admin role required")
    return {"items": tokens.list_tokens(None if scope == "all" else identity.user_id)}


@router.post(_TOKEN_MANAGEMENT_PATH, status_code=201)
def create_mcp_token(payload: CreateMcpTokenRequest, response: Response, identity: TokenUser):
    try:
        item, token = tokens.create_token(identity, payload.name, payload.expires_in_days)
    except tokens.TokenLimitReached as exc:
        raise HTTPException(409, "每个用户最多可有 10 个有效的访问令牌") from exc
    except TimeoutError as exc:
        raise HTTPException(503, "暂时无法创建访问令牌，请重试") from exc
    response.headers["Cache-Control"] = "no-store"
    return {"item": item, "token": token}


@router.delete(_TOKEN_MANAGEMENT_PATH + "/{token_id}")
def revoke_mcp_token(token_id: int, identity: TokenUser):
    item = tokens.revoke_token(token_id, identity)
    if item is None:
        raise HTTPException(404, "找不到访问令牌")
    return {"item": item}


@router.get(policy.PREFIX + "/{agent_id}/threads/{thread_id}/files/{rel_path:path}")
async def download(agent_id: str, thread_id: str, rel_path: str, request: Request):
    """The same workspace file the web panel serves, behind the MCP token."""
    context = await _authenticate(request, agent_id)
    topic = await asyncio.to_thread(get_topic_task_store().get_topic, thread_id, context=context)
    if not topic or topic.get("agent_id") != agent_id:
        raise HTTPException(404)
    return topic_file_response(thread_id, rel_path, download=True)
