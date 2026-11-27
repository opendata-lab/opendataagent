from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import AwareDatetime

from api.topic_file_response import topic_file_response
from config import get_settings
from core import experience, remote_mcp_policy
from core.accounts import (
    AccountContext,
    AgentAccessDeniedError,
    User,
    current_account_context,
    current_user,
    current_user_id,
    filter_accessible_agents,
    get_account_store,
    require_agent_access,
    require_admin,
)
from core.agent_draft_service import DraftConflict, agent_drafts, check_revision
from core.agent_profile_service import (
    agent_capabilities,
    build_agent_snapshot,
    get_agent_profile,
    list_agent_profiles,
    skill_folders_from_documents,
)
from core.mcp_admin_service import (
    annotate_mcp_servers,
    create_mcp_server,
    delete_mcp_server,
    detect_mcp_server,
    import_mcp_servers,
    list_mcp_servers,
    redact_mcp_servers,
    update_mcp_server,
)
from core.skill_admin_service import (
    compare_document_versions,
    current_settings_payload,
    delete_provider_config,
    detect_model_availability,
    export_skill_as_zip,
    get_document_detail,
    import_skill_from_zip,
    list_documents,
    list_provider_configs,
    persist_admin_settings,
    rollback_document,
    save_document_content,
    save_provider_config,
    uninstall_skill,
    update_skill_runtime,
)
from core.skill_discovery import resolve_skills_root_dir
from core.task_submission_service import submit_message_task
from core.topic_files import list_files
from core.topic_task_store import get_topic_task_store
from models.schemas import (
    AdminFeedbackPage,
    AdminSettingsResponse,
    AdminSettingsUpdateRequest,
    AdminWidgetTopicPage,
    AdminWidgetTopicSummary,
    AgentCapabilitiesResponse,
    AgentCatalogProfile,
    AgentDraftRevisionRequest,
    AgentPreviewMessageRequest,
    AgentProfile,
    AgentProfileCreateRequest,
    AgentProfileUpdateRequest,
    AgentReadableProfile,
    AgentRemoteMcpRequest,
    AgentSlashCommandsResponse,
    Experience,
    ExperienceCreateRequest,
    ExperiencePage,
    ExperienceUpdateRequest,
    McpServerCreateRequest,
    McpServerDetectionResponse,
    McpServerListResponse,
    McpServerUpdateRequest,
    ModelDetectionRequest,
    ModelDetectionResponse,
    ProviderConfig,
    ProviderCreateRequest,
    ProviderListResponse,
    ProviderUpdateRequest,
    SkillDocumentCompareRequest,
    SkillDocumentCompareResponse,
    SkillDocumentDetail,
    SkillDocumentSummary,
    SkillDocumentUpdateRequest,
    SkillImportResponse,
    SkillRuntimeConfig,
    SkillRuntimeUpdateRequest,
    SkillUninstallResponse,
    TopicMessagePageResponse,
    WorkspaceFileListResponse,
)

router = APIRouter()
settings_router = APIRouter(prefix="/api/v1/nl2sql-admin")
dataagent_router = APIRouter(prefix="/api/v1/dataagent")


def _readable_agent_payload(profile: dict) -> dict:
    visibility = profile.get("visibility") or {}
    path = ""
    if profile.get("remote_mcp_enabled"):
        path = remote_mcp_policy.endpoint("", profile["agent_id"])
    return {**profile, "visibility_mode": str(visibility.get("mode") or "all"), "remote_mcp_path": path}


def _provider_catalog() -> list[ProviderConfig]:
    return [ProviderConfig.model_validate(item) for item in list_provider_configs(enabled_only=False)]


def _build_admin_settings_response(updated_at: str = "") -> AdminSettingsResponse:
    payload = current_settings_payload()
    return AdminSettingsResponse(
        provider_id=str(payload.get("provider_id") or ""),
        model=str(payload.get("model") or ""),
        providers=_provider_catalog(),
        widget_allowed_sites=[
            site for site in (payload.get("widget_allowed_sites") or []) if isinstance(site, dict)
        ],
        skills_output_dir=str(payload.get("skills_output_dir") or ""),
        skills_root_dir=str(resolve_skills_root_dir()),
        updated_at=updated_at,
    )


@settings_router.get(
    "/settings",
    response_model=AdminSettingsResponse,
    dependencies=[Depends(require_admin)],
)
def get_admin_settings():
    return _build_admin_settings_response()


@settings_router.put(
    "/settings",
    response_model=AdminSettingsResponse,
    dependencies=[Depends(require_admin)],
)
def update_admin_settings(request: AdminSettingsUpdateRequest):
    try:
        saved = persist_admin_settings(request.model_dump(exclude_none=True, exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _build_admin_settings_response(updated_at=str(saved.get("updated_at") or ""))


@settings_router.post(
    "/model-detections",
    response_model=ModelDetectionResponse,
    dependencies=[Depends(require_admin)],
)
async def create_model_detection(request: ModelDetectionRequest):
    try:
        result = await detect_model_availability(request.model_dump(exclude_none=True, exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ModelDetectionResponse.model_validate(result)


@settings_router.get(
    "/providers",
    response_model=ProviderListResponse,
    dependencies=[Depends(require_admin)],
)
def get_providers():
    return ProviderListResponse(providers=_provider_catalog())


@settings_router.post(
    "/providers",
    dependencies=[Depends(require_admin)],
)
def create_provider(request: ProviderCreateRequest):
    try:
        saved = save_provider_config(request.model_dump(exclude_none=True, exclude_unset=True), create=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"provider_id": str(saved.get("provider_id") or "")}


@settings_router.put(
    "/providers/{provider_id}",
    dependencies=[Depends(require_admin)],
)
def update_provider(provider_id: str, request: ProviderUpdateRequest):
    try:
        save_provider_config(
            {"provider_id": provider_id, **request.model_dump(exclude_none=True, exclude_unset=True)},
            create=False,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@settings_router.delete(
    "/providers/{provider_id}",
    dependencies=[Depends(require_admin)],
)
def delete_provider(provider_id: str):
    try:
        delete_provider_config(provider_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


# ---- MCP Servers -----------------------------------------------------------

@dataagent_router.get("/mcp/servers", response_model=McpServerListResponse)
def get_mcp_servers(user: User = Depends(current_user)):
    listing = list_mcp_servers()
    if not user.is_admin:
        return McpServerListResponse.model_validate(redact_mcp_servers(listing))
    return McpServerListResponse.model_validate(annotate_mcp_servers(listing))


@dataagent_router.post(
    "/mcp/servers",
    dependencies=[Depends(require_admin)],
)
def create_mcp_server_endpoint(request: McpServerCreateRequest):
    try:
        server_id = create_mcp_server(request.model_dump(exclude_none=True, exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"server_id": server_id}


@dataagent_router.patch(
    "/mcp/servers/{server_id}",
    dependencies=[Depends(require_admin)],
)
def update_mcp_server_endpoint(server_id: str, request: McpServerUpdateRequest):
    try:
        update_mcp_server(server_id, request.model_dump(exclude_none=True, exclude_unset=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@dataagent_router.delete(
    "/mcp/servers/{server_id}",
    dependencies=[Depends(require_admin)],
)
def delete_mcp_server_endpoint(server_id: str):
    try:
        delete_mcp_server(server_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@dataagent_router.post(
    "/mcp/servers/{server_id}/detections",
    response_model=McpServerDetectionResponse,
    dependencies=[Depends(require_admin)],
)
async def detect_mcp_server_endpoint(server_id: str):
    try:
        result = await detect_mcp_server(server_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return McpServerDetectionResponse.model_validate(result)


@dataagent_router.post(
    "/mcp/servers/import",
    dependencies=[Depends(require_admin)],
)
def import_mcp_servers_endpoint(payload: dict[str, Any] = Body(...)):
    try:
        imported = import_mcp_servers(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"imported": imported}


# ---- Feedback & Experience -------------------------------------------------

@settings_router.get(
    "/feedback",
    response_model=AdminFeedbackPage,
    dependencies=[Depends(require_admin)],
)
def admin_list_feedback(
    agent_id: str | None = Query(default=None),
    feedback: str = Query(default="", pattern="^(|like|dislike)$"),
    start: AwareDatetime | None = Query(default=None),
    end: AwareDatetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    payload = get_topic_task_store().list_message_feedback(
        agent_id=agent_id or None,
        feedback=feedback or None,
        start=start,
        end=end,
        page=page,
        page_size=page_size,
    )
    return AdminFeedbackPage.model_validate(payload)


@settings_router.get(
    "/experiences",
    response_model=ExperiencePage,
    dependencies=[Depends(require_admin)],
)
def admin_list_experiences(
    keyword: str = Query(default="", max_length=160),
    status: str = Query(default="", pattern="^(|pending|active|disabled)$"),
    scope: str = Query(default="", pattern="^(|agent|global)$"),
    agent_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    settings = get_settings()
    payload = experience.list_experiences(
        keyword=keyword,
        status=status,
        scope=scope,
        agent_id=agent_id or "",
        page=page,
        page_size=page_size,
    )
    return ExperiencePage(
        **payload,
        digest_enabled=bool(settings.dataagent_experience_digest_enabled),
        injection_enabled=bool(settings.dataagent_experience_injection_enabled),
        digest_agent_id=experience.DIGEST_AGENT_ID,
    )


@settings_router.post(
    "/experiences",
    response_model=Experience,
    dependencies=[Depends(require_admin)],
)
def admin_create_experience(payload: ExperienceCreateRequest, user_id: int = Depends(current_user_id)):
    try:
        return experience.create_experience(**payload.model_dump(), user_id=user_id, status="active")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@settings_router.get(
    "/experiences/injection-preview",
    dependencies=[Depends(require_admin)],
)
def admin_injection_preview(agent_id: str = Query(min_length=1)):
    return experience.injection_preview(
        agent_id,
        injection_enabled=bool(get_settings().dataagent_experience_injection_enabled),
    )


@settings_router.delete(
    "/experiences/{experience_id}",
    dependencies=[Depends(require_admin)],
)
def admin_delete_experience(experience_id: str):
    if not experience.delete_experience(experience_id):
        raise HTTPException(status_code=404, detail="experience not found")
    return {"ok": True}


@settings_router.patch(
    "/experiences/{experience_id}",
    response_model=Experience,
    dependencies=[Depends(require_admin)],
)
def admin_update_experience(
    experience_id: str,
    payload: ExperienceUpdateRequest,
    user_id: int = Depends(current_user_id),
):
    try:
        updated = experience.update_experience(
            experience_id,
            user_id=user_id,
            **payload.model_dump(exclude_unset=True, exclude_none=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="experience not found")
    return updated


# ---- Admin Topics Audit ----------------------------------------------------

def _admin_topic_summary(topic: dict, user: User) -> AdminWidgetTopicSummary:
    source = topic.get("source") or "web"
    can_continue = (
        source in {"web", "widget", "remote"}
        and topic.get("user_id") == user.id
    )
    return AdminWidgetTopicSummary.model_validate({**topic, "can_continue": can_continue})


@settings_router.get("/topics", response_model=AdminWidgetTopicPage)
def admin_list_all_topics(
    source: str = Query(default="", pattern="^(|web|widget|remote)$"),
    user_id: int | None = Query(default=None),
    agent_id: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    start: AwareDatetime | None = Query(default=None),
    end: AwareDatetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    user: User = Depends(require_admin),
):
    payload = get_topic_task_store().admin_list_topics(
        source=source,
        user_id=user_id,
        agent_id=agent_id,
        keyword=keyword,
        start=start,
        end=end,
        page=page,
        page_size=page_size,
    )
    return AdminWidgetTopicPage(
        items=[_admin_topic_summary(item, user) for item in payload.get("items") or []],
        total=int(payload.get("total") or 0),
        page=int(payload.get("page") or page),
        page_size=int(payload.get("page_size") or page_size),
    )


def _require_audit_topic(store, topic_id: str) -> dict:
    topic = store.get_topic(topic_id, context=None)
    if not topic or topic.get("is_agent_preview"):
        raise HTTPException(status_code=404, detail="Topic not found")
    return topic


@settings_router.get("/topics/{topic_id}", response_model=AdminWidgetTopicSummary)
def admin_get_topic(topic_id: str, user: User = Depends(require_admin)):
    return _admin_topic_summary(_require_audit_topic(get_topic_task_store(), topic_id), user)


@settings_router.get(
    "/topics/{topic_id}/messages",
    response_model=TopicMessagePageResponse,
    dependencies=[Depends(require_admin)],
)
def admin_list_topic_messages(
    topic_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=200, ge=1, le=500),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
):
    store = get_topic_task_store()
    _require_audit_topic(store, topic_id)
    payload = store.list_topic_messages_page(
        topic_id=topic_id, page=page, page_size=page_size, order=order, context=None
    )
    return TopicMessagePageResponse.model_validate(payload)


@settings_router.get(
    "/topics/{topic_id}/files",
    response_model=WorkspaceFileListResponse,
    dependencies=[Depends(require_admin)],
)
def admin_list_topic_files(topic_id: str):
    _require_audit_topic(get_topic_task_store(), topic_id)
    return WorkspaceFileListResponse(files=list_files(topic_id))


@settings_router.get(
    "/topics/{topic_id}/files/{rel_path:path}",
    dependencies=[Depends(require_admin)],
)
def admin_download_topic_file(topic_id: str, rel_path: str, download: int = Query(default=1)):
    _require_audit_topic(get_topic_task_store(), topic_id)
    return topic_file_response(topic_id, rel_path, download=bool(download))


# ---- Agents & Workbench (static routes registered before dynamic {agent_id}) ----

def _draft_error(exc: ValueError) -> HTTPException:
    status = 409 if isinstance(exc, DraftConflict) else 404 if "not found" in str(exc) else 400
    return HTTPException(status_code=status, detail=str(exc))


def _preview_owner_context(user: User) -> dict[str, Any]:
    return {
        "source": "web",
        "website_id": "",
        "user_id": user.id,
        "is_admin": user.is_admin,
    }


@dataagent_router.get(
    "/agents/capabilities",
    response_model=AgentCapabilitiesResponse,
    dependencies=[Depends(require_admin)],
)
def get_agent_capabilities():
    return AgentCapabilitiesResponse.model_validate(agent_capabilities(list_documents()))


@dataagent_router.get(
    "/agents/workbench",
    dependencies=[Depends(require_admin)],
)
def list_agent_workbench():
    return agent_drafts.list()


@dataagent_router.get("/agents/builtin-prompt")
def get_agent_builtin_prompt(_: User = Depends(current_user)):
    from core.agent_runtime import _load_system_prompt_template

    return {"content": _load_system_prompt_template()}


@dataagent_router.get("/agents/profiles", response_model=list[AgentReadableProfile])
def get_readable_agent_profiles(user: User = Depends(current_user)):
    profiles = filter_accessible_agents(user, list_agent_profiles())
    return [AgentReadableProfile.model_validate(_readable_agent_payload(item)) for item in profiles]


@dataagent_router.get("/agents", response_model=list[AgentCatalogProfile])
def get_agents(ctx: AccountContext = Depends(current_account_context)):
    if ctx.user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    profiles = filter_accessible_agents(ctx.user, list_agent_profiles())
    return [AgentCatalogProfile.model_validate(item) for item in profiles]


@dataagent_router.post(
    "/agents",
    dependencies=[Depends(require_admin)],
)
def create_agent(request: AgentProfileCreateRequest):
    try:
        return agent_drafts.create(
            request.model_dump(exclude_none=True, exclude_unset=True),
            skill_folders_from_documents(list_documents()),
        )
    except ValueError as exc:
        raise _draft_error(exc) from exc


@dataagent_router.get("/agents/{agent_id}/draft")
def get_agent_draft(agent_id: str, user: User = Depends(require_admin)):
    try:
        draft = agent_drafts.get(agent_id)
        preview_id = draft.get("preview_topic_id")
        if preview_id and not get_topic_task_store().get_topic(preview_id, context=_preview_owner_context(user)):
            return {**draft, "preview_topic_id": None, "preview_task_id": None, "preview_status": None}
        return draft
    except ValueError as exc:
        raise _draft_error(exc) from exc


@dataagent_router.put(
    "/agents/{agent_id}/remote-mcp",
    dependencies=[Depends(require_admin)],
)
def set_agent_remote_mcp(agent_id: str, request: AgentRemoteMcpRequest):
    try:
        return agent_drafts.set_remote_mcp(agent_id, request.enabled)
    except ValueError as exc:
        raise _draft_error(exc) from exc


@dataagent_router.post(
    "/agents/{agent_id}/publish",
    dependencies=[Depends(require_admin)],
)
def publish_agent(agent_id: str, request: AgentDraftRevisionRequest):
    try:
        return agent_drafts.publish(agent_id, request.expected_revision)
    except ValueError as exc:
        raise _draft_error(exc) from exc


@dataagent_router.post("/agents/{agent_id}/preview-topics")
def create_agent_preview_topic(
    agent_id: str,
    request: AgentDraftRevisionRequest,
    user: User = Depends(require_admin),
):
    try:
        draft = agent_drafts.get(agent_id)
        check_revision(draft, request.expected_revision)
        return get_topic_task_store().create_topic(
            title=f"调试 · {draft['name']}",
            agent_snapshot=build_agent_snapshot(draft),
            context=_preview_owner_context(user),
            is_agent_preview=True,
        )
    except ValueError as exc:
        raise _draft_error(exc) from exc


@dataagent_router.post("/agents/{agent_id}/preview-tasks")
async def submit_agent_preview(
    agent_id: str,
    request: AgentPreviewMessageRequest,
    user: User = Depends(require_admin),
):
    try:
        draft = agent_drafts.get(agent_id)
        check_revision(draft, request.expected_revision)
        topic = get_topic_task_store().get_topic(request.topic_id, context=_preview_owner_context(user))
        if not topic or not topic.get("is_agent_preview") or topic.get("agent_id") != agent_id:
            raise ValueError("preview topic not found")
        token = await anyio.to_thread.run_sync(agent_drafts.reserve_preview, agent_id, request.expected_revision, topic)
        result = await submit_message_task(
            topic_id=request.topic_id,
            message_type="text",
            message_content=request.message_content,
            agent_id=agent_id,
            provider_id=request.provider_id,
            model=request.model,
            debug=True,
        )
        await anyio.to_thread.run_sync(
            agent_drafts.register_preview,
            agent_id,
            request.expected_revision,
            result["task_id"],
            token,
        )
        return result
    except ValueError as exc:
        raise _draft_error(exc) from exc


@dataagent_router.get("/agents/{agent_id}/profile", response_model=AgentReadableProfile)
def get_readable_agent_profile(agent_id: str, user: User = Depends(current_user)):
    try:
        profile = require_agent_access(user.id, agent_id)
    except AgentAccessDeniedError as exc:
        raise HTTPException(status_code=404, detail="agent not found") from exc
    return AgentReadableProfile.model_validate(_readable_agent_payload(profile))


@dataagent_router.get(
    "/agents/{agent_id}/configuration",
    response_model=AgentProfile,
    dependencies=[Depends(require_admin)],
)
def get_agent_configuration(agent_id: str):
    profile = get_agent_profile(agent_id)
    if not profile:
        raise HTTPException(status_code=404, detail="agent not found")
    return AgentProfile.model_validate(profile)


@dataagent_router.get("/agents/{agent_id}/slash-commands", response_model=AgentSlashCommandsResponse)
def get_agent_slash_commands_endpoint(agent_id: str, ctx: AccountContext = Depends(current_account_context)):
    if ctx.user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        profile = require_agent_access(ctx.user.id, agent_id)
    except AgentAccessDeniedError as exc:
        raise HTTPException(status_code=404, detail="agent not found") from exc
    raw = profile.get("skill_folders")
    folders = [str(item).strip() for item in raw if str(item or "").strip()] if isinstance(raw, list) else []
    return AgentSlashCommandsResponse(slash_commands=folders, source="skills")


@dataagent_router.get("/agents/{agent_id}", response_model=AgentCatalogProfile)
def get_agent(agent_id: str, ctx: AccountContext = Depends(current_account_context)):
    if ctx.user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        profile = require_agent_access(ctx.user.id, agent_id)
    except AgentAccessDeniedError as exc:
        raise HTTPException(status_code=404, detail="agent not found") from exc
    return AgentCatalogProfile.model_validate(profile)


@dataagent_router.put(
    "/agents/{agent_id}",
    dependencies=[Depends(require_admin)],
)
def update_agent(agent_id: str, request: AgentProfileUpdateRequest):
    try:
        return agent_drafts.save(
            agent_id,
            request.model_dump(exclude_none=True, exclude_unset=True, exclude={"expected_revision"}),
            request.expected_revision,
            skill_folders_from_documents(list_documents()),
        )
    except ValueError as exc:
        raise _draft_error(exc) from exc


@dataagent_router.delete(
    "/agents/{agent_id}",
    dependencies=[Depends(require_admin)],
)
def delete_agent(agent_id: str):
    try:
        agent_drafts.delete(agent_id)
        return {"status": "ok"}
    except ValueError as exc:
        raise _draft_error(exc) from exc


@dataagent_router.get(
    "/tasks/{task_id}/system-prompt",
    dependencies=[Depends(require_admin)],
)
def get_task_system_prompt(task_id: str):
    record = get_topic_task_store().get_task_system_prompt(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="system prompt not recorded for task")
    return record


# ---- Skills ----------------------------------------------------------------

@dataagent_router.get(
    "/skills/documents",
    response_model=list[SkillDocumentSummary],
    dependencies=[Depends(current_user)],
)
def get_skill_documents():
    return [SkillDocumentSummary.model_validate(item) for item in list_documents()]


@dataagent_router.get(
    "/skills/documents/{document_id}",
    response_model=SkillDocumentDetail,
    dependencies=[Depends(current_user)],
)
def get_skill_document(document_id: int):
    document = get_document_detail(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="document not found")
    return SkillDocumentDetail.model_validate(document)


@dataagent_router.put(
    "/skills/documents/{document_id}",
    response_model=SkillDocumentDetail,
    dependencies=[Depends(require_admin)],
)
def update_skill_document(document_id: int, request: SkillDocumentUpdateRequest):
    try:
        document = save_document_content(document_id, request.content, request.change_summary)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return SkillDocumentDetail.model_validate(document)


@dataagent_router.put(
    "/skills/runtime/{folder}",
    response_model=SkillRuntimeConfig,
    dependencies=[Depends(require_admin)],
)
def update_skill_runtime_config(folder: str, request: SkillRuntimeUpdateRequest):
    try:
        result = update_skill_runtime(folder, request.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SkillRuntimeConfig.model_validate(result)


@dataagent_router.post(
    "/skills/imports",
    response_model=SkillImportResponse,
    dependencies=[Depends(require_admin)],
)
async def import_skill(file: UploadFile = File(...)):
    try:
        content = await file.read()
        result = import_skill_from_zip(file.filename or "", content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SkillImportResponse.model_validate(result)


@dataagent_router.get(
    "/skills/{folder}/export",
    dependencies=[Depends(require_admin)],
)
def export_skill(folder: str):
    try:
        file_name, content = export_skill_as_zip(folder)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@dataagent_router.delete(
    "/skills/{folder}",
    response_model=SkillUninstallResponse,
    dependencies=[Depends(require_admin)],
)
def delete_skill(folder: str):
    try:
        result = uninstall_skill(folder)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return SkillUninstallResponse.model_validate(result)


@dataagent_router.post(
    "/skills/documents/{document_id}/compare",
    response_model=SkillDocumentCompareResponse,
    dependencies=[Depends(current_user)],
)
def compare_skill_document(document_id: int, request: SkillDocumentCompareRequest):
    try:
        result = compare_document_versions(
            document_id,
            left_version_id=request.left_version_id,
            right_version_id=request.right_version_id,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return SkillDocumentCompareResponse.model_validate(result)


@dataagent_router.post(
    "/skills/documents/{document_id}/versions/{version_id}/rollback",
    response_model=SkillDocumentDetail,
    dependencies=[Depends(require_admin)],
)
def rollback_skill_document(document_id: int, version_id: int):
    try:
        document = rollback_document(document_id, version_id)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return SkillDocumentDetail.model_validate(document)


router.include_router(settings_router)
router.include_router(dataagent_router)
