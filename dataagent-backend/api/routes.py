from __future__ import annotations

import asyncio
import logging
import json
from typing import Any, AsyncIterator

import anyio
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from api.topic_file_response import topic_file_response
from config import get_settings
from core.accounts import (
    AccountContext,
    AgentAccessDeniedError,
    User,
    current_account_context,
    require_agent_access,
)
from core.agent_profile_service import DEFAULT_AGENT_ID, build_agent_snapshot
from core.provider_runtime import normalize_api_format
from core import live_events
from core.task_status import TERMINAL_TASK_STATUSES
from core.followup_suggestions import generate_followup_suggestions
from core.skill_admin_service import resolved_chat_settings_payload
from core.permission_gate import normalize_permission_decision
from core.task_coordinator import get_task_coordinator
from core.task_submission_service import compute_next_run_at, current_utc_naive, submit_message_task
from core.topic_files import TopicFileError, list_files, save_upload
from core.topic_task_store import get_topic_task_store
from models.schemas import (
    CancelTaskResponse,
    CreateTaskRequest,
    PermissionDecisionRequest,
    PermissionDecisionResponse,
    QuestionAnswerRequest,
    QuestionAnswerResponse,
    CreateTopicRequest,
    DeliverMessageRequest,
    FollowupSuggestionsResponse,
    MessageQueuePageResponse,
    MessageQueueQueryRequest,
    MessageQueueRecord,
    MessageQueueUpsertRequest,
    MessageScheduleLogPageResponse,
    MessageScheduleLogsQueryRequest,
    MessageSchedulePageResponse,
    MessageScheduleQueryRequest,
    MessageScheduleRecord,
    MessageScheduleUpsertRequest,
    RuntimeConfigResponse,
    RuntimeProviderConfig,
    SdkEventPageResponse,
    SdkEventRecord,
    TaskStatusResponse,
    TaskSubmissionResponse,
    TopicDetail,
    TopicMessage,
    TopicMessagePageResponse,
    TopicSummary,
    UpdateMessageFeedbackRequest,
    UpdateTopicRequest,
    WidgetEventBatchRequest,
    WidgetEventIngestResponse,
    WorkspaceFile,
    WorkspaceFileListResponse,
)

logger = logging.getLogger(__name__)

# Message-level wording, deliberately broader than the task vocabulary: it also
# accepts statuses persisted by earlier versions.
SUCCESS_MESSAGE_STATUSES = {"finished", "success", "completed"}

router = APIRouter(prefix="/api/v1/nl2sql")
topic_router = APIRouter(prefix="/topics")
task_router = APIRouter(prefix="/tasks")
queue_router = APIRouter(prefix="/message-queue")
schedule_router = APIRouter(prefix="/message-schedule")


def _encode_sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _message_answer_text(message: dict[str, Any]) -> str:
    blocks = message.get("blocks")
    if isinstance(blocks, list):
        parts = [
            str(block.get("text") or "").strip()
            for block in blocks
            if isinstance(block, dict) and str(block.get("type") or "") == "main_text" and str(block.get("text") or "").strip()
        ]
        if parts:
            return "\n\n".join(parts).strip()
    return str(message.get("content") or "").strip()


def _message_result_summary(message: dict[str, Any]) -> str:
    blocks = message.get("blocks")
    if not isinstance(blocks, list):
        return ""
    summaries: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").strip()
        if block_type not in {"tool", "chart_spec"}:
            continue
        output = block.get("output")
        if output is None and isinstance(block.get("payload"), dict):
            output = block["payload"].get("output") or block["payload"].get("content")
        text = str(output or "").strip()
        if text:
            summaries.append(text[:500])
    return "\n\n".join(summaries)[:1200]


def _previous_user_question(messages: list[dict[str, Any]], assistant_message: dict[str, Any]) -> str:
    assistant_seq = int(assistant_message.get("seq_id") or 0)
    for message in reversed(messages):
        if str(message.get("sender_type") or "") != "user":
            continue
        if assistant_seq and int(message.get("seq_id") or 0) >= assistant_seq:
            continue
        content = str(message.get("content") or "").strip()
        if content:
            return content
    return ""


@router.get("/health")
async def api_health():
    cfg = get_settings()
    return {
        "status": "ok",
        "provider_id": cfg.llm_provider,
        "model": cfg.claude_model,
        "skills_output_dir": cfg.skills_output_dir,
        "redis_host": cfg.redis_host,
        "redis_port": cfg.redis_port,
    }


@router.get("/runtime-config", response_model=RuntimeConfigResponse)
def api_runtime_config(_ctx: AccountContext = Depends(current_account_context)):
    payload = resolved_chat_settings_payload()
    providers = []
    for item in payload.get("providers") or []:
        providers.append(
            RuntimeProviderConfig(
                provider_id=str(item.get("provider_id") or ""),
                api_format=normalize_api_format(item.get("api_format")),
                display_name=str(item.get("display_name") or ""),
                provider_group=str(item.get("provider_group") or ""),
                models=list(item.get("models") or []),
                default_model=str(item.get("default_model") or ""),
                enabled=bool(item.get("enabled")),
                provider_enabled=bool(item.get("provider_enabled")),
                supports_partial_messages=bool(item.get("supports_partial_messages", True)),
                validation_status=str(item.get("validation_status") or "unverified"),
                validation_message=str(item.get("validation_message") or ""),
            )
        )

    return RuntimeConfigResponse(
        default_provider_id=str(payload.get("default_provider_id") or ""),
        default_model=str(payload.get("default_model") or ""),
        providers=providers,
    )


@router.post("/widget-events", response_model=WidgetEventIngestResponse)
def api_widget_events(payload: WidgetEventBatchRequest, http_request: Request):
    ctx = current_account_context(http_request)
    store = _get_store()
    events = [item.model_dump() for item in payload.events]
    accepted = store.record_widget_events(events, ctx.to_store_context())
    return WidgetEventIngestResponse(accepted=accepted)


@topic_router.post("", response_model=TopicDetail)
def api_create_topic(http_request: Request, request: CreateTopicRequest | None = None):
    store = _get_store()
    ctx = current_account_context(http_request)
    user = _require_writable_user(ctx)
    payload = request or CreateTopicRequest()
    if ctx.source == "widget" and not str(payload.agent_id or "").strip():
        raise HTTPException(status_code=400, detail="agent_id is required for widget requests")
    profile = _require_agent_profile(payload.agent_id, user)
    topic = store.create_topic(
        title=str(payload.title or "").strip() or "新话题",
        agent_snapshot=build_agent_snapshot(profile),
        permission_mode=payload.permission_mode,
        context=ctx.to_store_context(),
    )
    return TopicDetail.model_validate(topic)


@topic_router.get("", response_model=list[TopicSummary])
def api_list_topics(request: Request, agent_id: str | None = Query(default=None)):
    store = _get_store()
    ctx = current_account_context(request)
    topics = store.list_topics(include_messages=False, context=ctx.to_store_context(), agent_id=agent_id)
    return [TopicSummary.model_validate(item) for item in topics]


@topic_router.get("/{topic_id}", response_model=TopicDetail)
def api_get_topic(topic_id: str, request: Request):
    ctx = current_account_context(request)
    topic = _require_topic(topic_id, ctx.to_store_context())
    return TopicDetail.model_validate(topic)


@topic_router.put("/{topic_id}", response_model=TopicDetail)
def api_update_topic(topic_id: str, payload: UpdateTopicRequest, request: Request):
    ctx = current_account_context(request)
    title: str | None = None
    if payload.title is not None:
        title = str(payload.title).strip()
        if not title:
            raise HTTPException(status_code=400, detail="title is required")
    permission_mode = payload.permission_mode
    if title is None and permission_mode is None:
        raise HTTPException(status_code=400, detail="title or permission_mode is required")
    _require_writable_topic(topic_id, ctx)
    topic = _get_store().update_topic(
        topic_id,
        title=title,
        permission_mode=permission_mode,
        context=ctx.to_store_context(),
    )
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    return TopicDetail.model_validate(topic)


@topic_router.delete("/{topic_id}")
def api_delete_topic(topic_id: str, request: Request):
    ctx = current_account_context(request)
    store = _get_store()
    _require_writable_topic(topic_id, ctx)
    store.delete_topic(topic_id, context=ctx.to_store_context())
    return JSONResponse({"status": "ok"})


@topic_router.get("/{topic_id}/messages", response_model=TopicMessagePageResponse)
def api_list_topic_messages(
    topic_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=200, ge=1, le=500),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
):
    ctx = current_account_context(request)
    store_ctx = ctx.to_store_context()
    _require_topic(topic_id, store_ctx)
    payload = _get_store().list_topic_messages_page(topic_id=topic_id, page=page, page_size=page_size, order=order, context=store_ctx)
    return TopicMessagePageResponse.model_validate(payload)


@topic_router.post("/{topic_id}/files", response_model=WorkspaceFile)
async def api_upload_topic_file(topic_id: str, request: Request, file: UploadFile = File(...)):
    _require_writable_topic(topic_id, current_account_context(request))
    data = await file.read()
    cap = int(get_settings().dataagent_upload_max_bytes or 0)
    if cap and len(data) > cap:
        raise HTTPException(status_code=413, detail=f"file exceeds the {cap} byte upload limit")
    try:
        meta = save_upload(topic_id, file.filename or "file", data)
    except TopicFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return WorkspaceFile.model_validate(meta)


@topic_router.get("/{topic_id}/files", response_model=WorkspaceFileListResponse)
def api_list_topic_files(topic_id: str, request: Request):
    _require_topic(topic_id, current_account_context(request).to_store_context())
    return WorkspaceFileListResponse(files=[WorkspaceFile.model_validate(item) for item in list_files(topic_id)])


@topic_router.get("/{topic_id}/files/{rel_path:path}")
def api_download_topic_file(
    topic_id: str,
    rel_path: str,
    request: Request,
    download: int = Query(default=0),
):
    _require_topic(topic_id, current_account_context(request).to_store_context())
    return topic_file_response(topic_id, rel_path, download=bool(download))


@topic_router.put("/{topic_id}/messages/{message_id}/feedback", response_model=TopicMessage)
def api_update_message_feedback(
    topic_id: str,
    message_id: str,
    payload: UpdateMessageFeedbackRequest,
    request: Request,
):
    ctx = current_account_context(request)
    store_ctx = ctx.to_store_context()
    _require_writable_topic(topic_id, ctx)

    feedback = str(payload.feedback or "").strip().lower()
    if feedback not in {"", "like", "dislike"}:
        raise HTTPException(status_code=400, detail="feedback must be like, dislike, or empty")

    store = _get_store()
    message = store.get_message(message_id, context=store_ctx)
    if not message or str(message.get("topic_id") or "") != topic_id or not message.get("show_in_ui", True):
        raise HTTPException(status_code=404, detail="Message not found")
    if str(message.get("sender_type") or "") != "assistant":
        raise HTTPException(status_code=400, detail="feedback is only supported for assistant messages")

    updated = store.update_message_feedback(topic_id=topic_id, message_id=message_id, feedback=feedback, context=store_ctx)
    if not updated:
        raise HTTPException(status_code=404, detail="Message not found")
    return TopicMessage.model_validate(updated)


@topic_router.post("/{topic_id}/messages/{message_id}/followup-suggestions", response_model=FollowupSuggestionsResponse)
async def api_generate_followup_suggestions(topic_id: str, message_id: str, request: Request):
    ctx = current_account_context(request)
    store_ctx = ctx.to_store_context()
    _require_topic(topic_id, store_ctx)
    store = _get_store()
    message = store.get_message(message_id, context=store_ctx)
    if not message or str(message.get("topic_id") or "") != topic_id or not bool(message.get("show_in_ui", True)):
        raise HTTPException(status_code=404, detail="Message not found")
    if str(message.get("sender_type") or "") != "assistant":
        raise HTTPException(status_code=400, detail="follow-up suggestions are only supported for assistant messages")
    if str(message.get("status") or "").strip().lower() not in SUCCESS_MESSAGE_STATUSES:
        raise HTTPException(status_code=400, detail="follow-up suggestions require a finished assistant message")

    answer_text = _message_answer_text(message)
    if not answer_text:
        raise HTTPException(status_code=400, detail="assistant message content is empty")

    topic_messages = store.list_topic_messages(topic_id)
    previous_question = _previous_user_question(topic_messages, message)
    task = store.get_task(str(message.get("task_id") or ""), context=store_ctx) if message.get("task_id") else {}
    generated = await generate_followup_suggestions(
        previous_question=previous_question,
        answer_text=answer_text,
        result_summary=_message_result_summary(message),
        provider_id=str((task or {}).get("provider_id") or ""),
        model=str((task or {}).get("model") or ""),
        timeout_seconds=int(getattr(get_settings(), "followup_suggestions_timeout_seconds", 20) or 20),
    )
    return FollowupSuggestionsResponse.model_validate(
        {
            "topic_id": topic_id,
            "message_id": message_id,
            "suggestions": list(generated.get("suggestions") or []),
            "source": str(generated.get("source") or "empty"),
        }
    )


@task_router.post("/deliver-message", response_model=TaskSubmissionResponse)
async def api_deliver_message(payload: DeliverMessageRequest, request: Request):
    ctx = current_account_context(request)
    topic_id = str(payload.topic_id or "").strip()
    content = str(payload.content or "").strip()
    if not topic_id:
        raise HTTPException(status_code=400, detail="topic_id is required")
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    _require_writable_topic(topic_id, ctx, check_agent_access=True)
    if payload.permission_mode is not None:
        _get_store().update_topic(
            topic_id,
            permission_mode=payload.permission_mode,
            context=ctx.to_store_context(),
        )
    try:
        submitted = await submit_message_task(
            topic_id=topic_id,
            message_type="text",
            message_content=content,
            provider_id=payload.provider_id,
            model=payload.model,
            database_hint=payload.database,
            debug=bool(payload.debug),
            agent_id=payload.agent_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TaskSubmissionResponse.model_validate(submitted)


@task_router.post("", response_model=TaskSubmissionResponse)
async def api_create_task(payload: CreateTaskRequest, request: Request):
    ctx = current_account_context(request)
    topic_id = str(payload.topic_id or "").strip()
    if not topic_id:
        raise HTTPException(status_code=400, detail="topic_id is required")
    _require_writable_topic(topic_id, ctx, check_agent_access=True)
    try:
        submitted = await submit_message_task(
            topic_id=topic_id,
            message_type=payload.message_type,
            message_content=payload.message_content,
            provider_id=payload.provider_id,
            model=payload.model,
            database_hint=payload.database,
            debug=bool(payload.debug),
            agent_id=payload.agent_id,
            source_queue_id=payload.source_queue_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TaskSubmissionResponse.model_validate(submitted)


@task_router.get("/{task_id}", response_model=TaskStatusResponse)
def api_get_task(task_id: str, request: Request):
    store = _get_store()
    task = store.get_task(task_id, context=current_account_context(request).to_store_context())
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse.model_validate(task)


@task_router.get("/{task_id}/message", response_model=TopicMessage)
def api_get_task_message(task_id: str, request: Request):
    store = _get_store()
    task = store.get_task(task_id, context=current_account_context(request).to_store_context())
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    message = store.get_assistant_message(task_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    return TopicMessage.model_validate(message)


@task_router.get("/{task_id}/sdk-events", response_model=SdkEventPageResponse)
def api_list_sdk_events(
    task_id: str,
    request: Request,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
):
    store = _get_store()
    store_ctx = current_account_context(request).to_store_context()
    task = store.get_task(task_id, context=store_ctx)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    records = store.list_sdk_records(task_id=task_id, after_id=after_id, limit=limit + 1)
    has_more = len(records) > limit
    page = records[:limit]
    next_after_id = int(page[-1]["seq_id"]) if page else after_id
    return SdkEventPageResponse(
        task_id=task_id,
        task_status=str(task.get("task_status") or "waiting"),
        after_id=after_id,
        next_after_id=next_after_id,
        has_more=has_more,
        records=[SdkEventRecord.model_validate(r) for r in page],
    )


@task_router.get("/{task_id}/sdk-events/stream")
async def api_stream_sdk_events(task_id: str, request: Request, after_id: int = Query(default=0, ge=0)):
    store = _get_store()
    store_ctx = current_account_context(request).to_store_context()
    task = store.get_task(task_id, context=store_ctx)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    logger.info(
        "sdk_events.stream.open task_id=%s topic_id=%s after_id=%s task_status=%s",
        task_id,
        str(task.get("topic_id") or ""),
        after_id,
        str(task.get("task_status") or ""),
    )
    return StreamingResponse(
        _stream_sdk_events(task_id=task_id, after_id=after_id, context=store_ctx),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sdk_rows_upto(store, task_id: str, after_id: int, upto: int | None = None) -> list[dict[str, Any]]:
    """Persistent records with after_id < seq_id <= upto (no upper bound when upto is None)."""
    rows: list[dict[str, Any]] = []
    cursor = after_id
    while True:
        batch = store.list_sdk_records(task_id=task_id, after_id=cursor, limit=200)
        for rec in batch:
            seq_id = int(rec.get("seq_id") or 0)
            if upto is not None and seq_id > upto:
                return rows
            rows.append(rec)
            cursor = seq_id
        if len(batch) < 200:
            return rows


async def _stream_sdk_events(task_id: str, after_id: int, context: dict[str, str] | None = None) -> AsyncIterator[str]:
    """SSE for one task: live from memory while its segment runs here, MySQL polling otherwise.

    Design: docs/design/2026-11-13-content-delta-live-stream-design.md. In live
    mode, content.delta comes from the in-process channel (never stored) and a
    persistent record arrives as a seq_id marker, read back from MySQL in id
    order; timeouts also poll MySQL for rows written outside the event writer
    (cards, decisions).
    """
    cfg = get_settings()
    poll_interval = max(1, int(cfg.run_events_stream_poll_interval_seconds or 1))
    ping_seconds = max(5, int(cfg.run_events_stream_ping_seconds or 10))
    next_after_id = max(0, after_id)
    since_ping = 0
    delivered = 0
    last_task_status = ""
    store = _get_store()

    try:
        while True:
            sub = live_events.subscribe(task_id)
            if sub is not None:
                try:
                    # Stored records first, then the text of blocks still being
                    # generated, then live records in arrival order.
                    pending: list[dict[str, Any]] = _sdk_rows_upto(store, task_id, next_after_id)
                    pending.extend(sub.snapshot)
                    while True:
                        for rec in pending:
                            if rec.get("seq_id"):
                                next_after_id = max(next_after_id, int(rec["seq_id"]))
                            delivered += 1
                            yield _encode_sse(rec)
                        if pending:
                            since_ping = 0
                        pending = []
                        try:
                            item = await asyncio.wait_for(sub.queue.get(), timeout=poll_interval)
                        except asyncio.TimeoutError:
                            # The writer may have queued events after the timeout
                            # fired; deliver those first so a poll cannot overtake them.
                            if not sub.queue.empty():
                                continue
                            if sub.closed:
                                break
                            pending = _sdk_rows_upto(store, task_id, next_after_id)
                            if not pending:
                                since_ping += poll_interval
                                if since_ping >= ping_seconds:
                                    yield ": ping\n\n"
                                    since_ping = 0
                            continue
                        if item is None:
                            break
                        seq_id = item.get("seq_id")
                        if seq_id is None:
                            pending = [item]
                        elif int(seq_id) > next_after_id:
                            pending = _sdk_rows_upto(store, task_id, next_after_id, int(seq_id))
                finally:
                    live_events.unsubscribe(sub)
                # The segment ended or this subscriber fell behind: poll below,
                # or resubscribe (with a fresh snapshot) if a segment is live.
            records = store.list_sdk_records(task_id=task_id, after_id=next_after_id, limit=200)
            for rec in records:
                next_after_id = max(next_after_id, int(rec.get("seq_id") or 0))
                delivered += 1
                yield _encode_sse(rec)
            if records:
                since_ping = 0
            else:
                since_ping += poll_interval
                if since_ping >= ping_seconds:
                    yield ": ping\n\n"
                    since_ping = 0

            task = store.get_task(task_id, context=context)
            if not task:
                last_task_status = "missing"
                logger.info(
                    "sdk_events.stream.terminate task_id=%s reason=missing_task last_seq=%s delivered=%s",
                    task_id,
                    next_after_id,
                    delivered,
                )
                break
            last_task_status = str(task.get("task_status") or "")
            # Stop when: task is terminal and the latest DB poll had no new SDK records.
            if last_task_status in TERMINAL_TASK_STATUSES and not records:
                logger.info(
                    "sdk_events.stream.terminate task_id=%s reason=terminal task_status=%s last_seq=%s delivered=%s",
                    task_id,
                    last_task_status,
                    next_after_id,
                    delivered,
                )
                break
            await anyio.sleep(poll_interval)
    except asyncio.CancelledError:
        logger.info(
            "sdk_events.stream.disconnected task_id=%s requested_after_id=%s last_seq=%s task_status=%s delivered=%s",
            task_id,
            after_id,
            next_after_id,
            last_task_status,
            delivered,
        )
        raise
    finally:
        logger.info(
            "sdk_events.stream.close task_id=%s requested_after_id=%s last_seq=%s task_status=%s delivered=%s",
            task_id,
            after_id,
            next_after_id,
            last_task_status,
            delivered,
        )


@task_router.post("/{task_id}/cancel", response_model=CancelTaskResponse)
async def api_cancel_task(task_id: str, request: Request):
    store = _get_store()
    ctx = current_account_context(request)
    _require_writable_user(ctx)
    store_ctx = ctx.to_store_context()
    existing = store.get_task(task_id, context=store_ctx)
    if not existing:
        raise HTTPException(status_code=404, detail="Task not found")
    _require_writable_topic(str(existing.get("topic_id") or ""), ctx)
    task = store.request_task_cancel(task_id, context=store_ctx)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await get_task_coordinator().request_cancel(task_id)
    task = store.get_task(task_id, context=store_ctx) or task
    return CancelTaskResponse.model_validate(task)


@task_router.post("/{task_id}/permission-decision", response_model=PermissionDecisionResponse)
async def api_submit_permission_decision(task_id: str, payload: PermissionDecisionRequest, request: Request):
    response, appended = await asyncio.to_thread(_record_permission_decision, task_id, payload, request)
    if appended:
        await get_task_coordinator().submit_task(task_id)
    return response


def _record_permission_decision(task_id: str, payload: PermissionDecisionRequest, request: Request):
    appended = False
    store = _get_store()
    ctx = current_account_context(request)
    _require_writable_user(ctx)
    store_ctx = ctx.to_store_context()
    task = store.get_task(task_id, context=store_ctx)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    _require_writable_topic(str(task.get("topic_id") or ""), ctx)
    request_id = str(payload.request_id or "").strip()
    if not request_id:
        raise HTTPException(status_code=400, detail="request_id is required")
    decision = str(payload.decision or "").strip().lower()
    if decision not in {"allow", "deny"}:
        raise HTTPException(status_code=400, detail="decision must be allow or deny")
    current = store.get_current_permission_interaction(task_id=task_id, request_id=request_id)
    if current and str(current.get("status") or "") == "resolved":
        effective = str(current.get("decision") or decision)
    else:
        append_result = store.append_permission_decision_if_waiting(
            task_id=task_id,
            request_id=request_id,
            decision=decision,
            note=str(payload.note or ""),
        )
        if append_result not in {"appended", "already_resolved"}:
            detail = (
                "task is not awaiting this permission decision"
                if append_result in {"not_found", "status_mismatch"}
                else "failed to record permission decision"
            )
            raise HTTPException(status_code=409, detail=detail)
        appended = append_result == "appended"
        resolved = store.get_current_permission_interaction(task_id=task_id, request_id=request_id)
        effective = str((resolved or {}).get("decision") or decision)

    response = PermissionDecisionResponse.model_validate(
        {"task_id": task_id, "request_id": request_id, "decision": normalize_permission_decision(effective)}
    )
    return response, appended


@task_router.post("/{task_id}/question-answer", response_model=QuestionAnswerResponse)
async def api_submit_question_answer(task_id: str, payload: QuestionAnswerRequest, request: Request):
    response, appended = await asyncio.to_thread(_record_question_answer, task_id, payload, request)
    if appended:
        await get_task_coordinator().submit_task(task_id)
    return response


def _record_question_answer(task_id: str, payload: QuestionAnswerRequest, request: Request):
    appended = False
    store = _get_store()
    ctx = current_account_context(request)
    _require_writable_user(ctx)
    store_ctx = ctx.to_store_context()
    task = store.get_task(task_id, context=store_ctx)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    _require_writable_topic(str(task.get("topic_id") or ""), ctx)
    request_id = str(payload.request_id or "").strip()
    if not request_id:
        raise HTTPException(status_code=400, detail="request_id is required")
    answers = [a for a in (payload.answers or []) if isinstance(a, dict)]
    current = store.get_current_question_interaction(task_id=task_id, request_id=request_id)
    if not current or str(current.get("status") or "") != "resolved":
        append_result = store.append_question_answer_if_waiting(
            task_id=task_id,
            request_id=request_id,
            answers=answers,
        )
        if append_result not in {"appended", "already_resolved"}:
            detail = (
                "task is not awaiting this user answer"
                if append_result in {"not_found", "status_mismatch"}
                else "failed to record user answer"
            )
            raise HTTPException(status_code=409, detail=detail)
        appended = append_result == "appended"
    response = QuestionAnswerResponse.model_validate(
        {"task_id": task_id, "request_id": request_id, "accepted": True}
    )
    return response, appended


@queue_router.post("/queries", response_model=MessageQueuePageResponse)
def api_query_message_queues(payload: MessageQueueQueryRequest, request: Request):
    store_ctx = current_account_context(request).to_store_context()
    if payload.topic_id:
        _require_topic(str(payload.topic_id), store_ctx)
    page = _get_store().query_message_queues(topic_id=payload.topic_id, page=payload.page, page_size=payload.page_size, context=store_ctx)
    return MessageQueuePageResponse.model_validate(
        {
            "page": int(page.get("page") or payload.page),
            "page_size": int(page.get("page_size") or payload.page_size),
            "total": int(page.get("total") or 0),
            "list": [MessageQueueRecord.model_validate(item) for item in page.get("items") or []],
        }
    )


@queue_router.post("", response_model=MessageQueueRecord)
def api_create_message_queue(payload: MessageQueueUpsertRequest, request: Request):
    ctx = current_account_context(request)
    topic_id = str(payload.topic_id or "").strip()
    if not topic_id:
        raise HTTPException(status_code=400, detail="topic_id is required")
    _require_writable_topic(topic_id, ctx, check_agent_access=True)
    created = _get_store().create_message_queue(
        topic_id=topic_id,
        message_type=str(payload.message_type or "").strip() or "text",
        message_content=payload.message_content,
    )
    return MessageQueueRecord.model_validate(created)


@queue_router.put("/{queue_id}", response_model=MessageQueueRecord)
def api_update_message_queue(queue_id: str, payload: MessageQueueUpsertRequest, request: Request):
    ctx = current_account_context(request)
    store_ctx = ctx.to_store_context()
    store = _get_store()
    _require_writable_topic(str(payload.topic_id or "").strip(), ctx, check_agent_access=True)
    if not store.get_message_queue(queue_id, context=store_ctx):
        raise HTTPException(status_code=404, detail="Queue message not found")
    updated = store.update_message_queue(
        queue_id=queue_id,
        topic_id=str(payload.topic_id or "").strip(),
        message_type=str(payload.message_type or "").strip() or "text",
        message_content=payload.message_content,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Queue message not found")
    return MessageQueueRecord.model_validate(updated)


@queue_router.delete("/{queue_id}")
def api_delete_message_queue(queue_id: str, request: Request):
    store = _get_store()
    ctx = current_account_context(request)
    _require_writable_user(ctx)
    store_ctx = ctx.to_store_context()
    existing = store.get_message_queue(queue_id, context=store_ctx)
    if not existing:
        raise HTTPException(status_code=404, detail="Queue message not found")
    _require_writable_topic(str(existing.get("topic_id") or ""), ctx)
    deleted = store.delete_message_queue(queue_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Queue message not found")
    return JSONResponse({"status": "ok"})


@queue_router.post("/{queue_id}/consume", response_model=TaskSubmissionResponse)
async def api_consume_message_queue(queue_id: str, request: Request):
    store = _get_store()
    ctx = current_account_context(request)
    store_ctx = ctx.to_store_context()
    queue = store.get_message_queue(queue_id, context=store_ctx)
    if not queue:
        raise HTTPException(status_code=404, detail="Queue message not found")
    _require_writable_topic(str(queue.get("topic_id") or ""), ctx, check_agent_access=True)
    if str(queue.get("status") or "") == "running":
        raise HTTPException(status_code=409, detail="Queue message is already running")
    try:
        submitted = await submit_message_task(
            topic_id=str(queue.get("topic_id") or ""),
            message_type=str(queue.get("message_type") or "text"),
            message_content=queue.get("message_content"),
            source_queue_id=str(queue.get("queue_id") or ""),
            source_schedule_id=str(queue.get("source_schedule_id") or "") or None,
            source_schedule_log_id=str(queue.get("source_schedule_log_id") or "") or None,
        )
    except ValueError as exc:
        store.mark_message_queue_failed(queue_id=queue_id, error_message=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TaskSubmissionResponse.model_validate(submitted)


@schedule_router.post("/queries", response_model=MessageSchedulePageResponse)
def api_query_message_schedules(payload: MessageScheduleQueryRequest, request: Request):
    store_ctx = current_account_context(request).to_store_context()
    if payload.topic_id:
        _require_topic(str(payload.topic_id), store_ctx)
    page = _get_store().query_message_schedules(topic_id=payload.topic_id, page=payload.page, page_size=payload.page_size, context=store_ctx)
    return MessageSchedulePageResponse.model_validate(
        {
            "page": int(page.get("page") or payload.page),
            "page_size": int(page.get("page_size") or payload.page_size),
            "total": int(page.get("total") or 0),
            "list": [MessageScheduleRecord.model_validate(item) for item in page.get("items") or []],
        }
    )


@schedule_router.post("", response_model=MessageScheduleRecord)
def api_create_message_schedule(payload: MessageScheduleUpsertRequest, request: Request):
    ctx = current_account_context(request)
    topic_id = str(payload.topic_id or "").strip()
    if not topic_id:
        raise HTTPException(status_code=400, detail="topic_id is required")
    _require_writable_topic(topic_id, ctx, check_agent_access=True)
    try:
        next_run_at = compute_next_run_at(payload.cron_expr, payload.timezone) if payload.enabled else None
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid cron_expr or timezone: {exc}") from exc
    created = _get_store().create_message_schedule(
        topic_id=topic_id,
        name=str(payload.name or "").strip(),
        message_type=str(payload.message_type or "").strip() or "text",
        message_content=payload.message_content,
        cron_expr=str(payload.cron_expr or "").strip(),
        enabled=bool(payload.enabled),
        timezone=str(payload.timezone or "").strip() or "Asia/Shanghai",
        next_run_at=next_run_at,
    )
    return MessageScheduleRecord.model_validate(created)


@schedule_router.put("/{schedule_id}", response_model=MessageScheduleRecord)
def api_update_message_schedule(schedule_id: str, payload: MessageScheduleUpsertRequest, request: Request):
    ctx = current_account_context(request)
    store_ctx = ctx.to_store_context()
    store = _get_store()
    topic_id = str(payload.topic_id or "").strip()
    _require_writable_topic(topic_id, ctx, check_agent_access=True)
    if not store.get_message_schedule(schedule_id, context=store_ctx):
        raise HTTPException(status_code=404, detail="Message schedule not found")
    try:
        next_run_at = compute_next_run_at(payload.cron_expr, payload.timezone) if payload.enabled else None
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid cron_expr or timezone: {exc}") from exc
    updated = store.update_message_schedule(
        schedule_id=schedule_id,
        topic_id=topic_id,
        name=str(payload.name or "").strip(),
        message_type=str(payload.message_type or "").strip() or "text",
        message_content=payload.message_content,
        cron_expr=str(payload.cron_expr or "").strip(),
        enabled=bool(payload.enabled),
        timezone=str(payload.timezone or "").strip() or "Asia/Shanghai",
        next_run_at=next_run_at,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Message schedule not found")
    return MessageScheduleRecord.model_validate(updated)


@schedule_router.delete("/{schedule_id}")
def api_delete_message_schedule(schedule_id: str, request: Request):
    store = _get_store()
    ctx = current_account_context(request)
    _require_writable_user(ctx)
    store_ctx = ctx.to_store_context()
    existing = store.get_message_schedule(schedule_id, context=store_ctx)
    if not existing:
        raise HTTPException(status_code=404, detail="Message schedule not found")
    _require_writable_topic(str(existing.get("topic_id") or ""), ctx)
    deleted = store.delete_message_schedule(schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Message schedule not found")
    return JSONResponse({"status": "ok"})


@schedule_router.get("/{schedule_id}", response_model=MessageScheduleRecord)
def api_get_message_schedule(schedule_id: str, request: Request):
    schedule = _get_store().get_message_schedule(schedule_id, context=current_account_context(request).to_store_context())
    if not schedule:
        raise HTTPException(status_code=404, detail="Message schedule not found")
    return MessageScheduleRecord.model_validate(schedule)


@schedule_router.post("/{schedule_id}/logs", response_model=MessageScheduleLogPageResponse)
def api_list_message_schedule_logs(schedule_id: str, payload: MessageScheduleLogsQueryRequest, request: Request):
    store = _get_store()
    schedule = store.get_message_schedule(schedule_id, context=current_account_context(request).to_store_context())
    if not schedule:
        raise HTTPException(status_code=404, detail="Message schedule not found")
    page_payload = store.list_message_schedule_logs(
        schedule_id=schedule_id,
        page=payload.page,
        page_size=payload.page_size,
    )
    return MessageScheduleLogPageResponse.model_validate(
        {
            "schedule_id": schedule_id,
            "page": int(page_payload.get("page") or payload.page),
            "page_size": int(page_payload.get("page_size") or payload.page_size),
            "total": int(page_payload.get("total") or 0),
            "list": page_payload.get("items") or [],
        }
    )


router.include_router(topic_router)
router.include_router(task_router)
router.include_router(queue_router)
router.include_router(schedule_router)


def _get_store():
    store = get_topic_task_store()
    store.init_schema()
    return store


def _require_writable_user(ctx: AccountContext) -> User:
    if ctx.user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return ctx.user


def _require_topic(topic_id: str, context: dict[str, Any] | None = None) -> dict:
    store = _get_store()
    topic = store.get_topic(topic_id, context=context)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    return topic


def _require_writable_topic(
    topic_id: str,
    ctx: AccountContext,
    *,
    check_agent_access: bool = False,
) -> dict:
    user = _require_writable_user(ctx)
    topic = _require_topic(topic_id, ctx.to_store_context())
    if ctx.shared_session_key and topic.get("user_id") is None:
        raise HTTPException(status_code=403, detail="历史共享会话仅支持只读，请新建会话继续提问")
    if check_agent_access:
        _require_agent_profile(str(topic.get("agent_id") or ""), user)
    return topic


def _require_agent_profile(agent_id: str | None, user: User) -> dict:
    try:
        return require_agent_access(user.id, str(agent_id or "").strip() or DEFAULT_AGENT_ID)
    except AgentAccessDeniedError as exc:
        raise HTTPException(status_code=400, detail="agent not found") from exc
