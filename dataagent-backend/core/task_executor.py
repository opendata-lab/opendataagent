from __future__ import annotations

import asyncio
import inspect
import json
import math
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from config import get_settings, resolve_run_limits
from core.agent_profile_service import normalize_agent_snapshot, normalize_permission_mode
from core.agent_runtime import (_build_system_prompt, _build_mcp_servers, _build_provider_env,
    resolve_agent_skill_runtime, resolve_enabled_skill_runtime, resolve_runtime_provider_selection)
from core.context_governance import build_governance_settings
from core.interaction_policy import plan_deadline, question_deadline
from core.pi_event_writer import PiEventWriter
from core.pi_runtime import PiRunContext, accumulate_usage, execute_pi_run
from core.sandbox_config import build_sandbox_config
from core.topic_task_store import get_topic_task_store
from core.topic_workspace import prepare_topic_workspace
from core.task_status import from_engine_outcome

@dataclass
class TaskExecutionInput:
    """One NL2SQL run's execution contract, assembled by the coordinator/submission layer.

    Timeout fields are resolved per ``execution_mode`` at submission time (see
    ``resolve_task_timeouts``); when a recovered/legacy task carries ``0``/``None``
    the runtime falls back to the mode-specific value (see ``agent_runtime`` and
    ``config.resolve_sql_read_timeout_seconds``).

    Fields:
        task_id / topic_id: identity of this run and its conversation topic.
        question: the user prompt for this run.
        history: prior turns passed to the model as context.
        resume_session_id: SDK session id to resume, or ``None`` for a fresh run.
        provider_id / model: selected runtime provider and model id.
        database_hint: optional default schema/database for SQL tools.
        debug: enable verbose runtime diagnostics.
        timeout_seconds: legacy submission-time budget retained for recovery
            heuristics and compatibility; the SDK message loop is not wrapped in
            this value while confirmation/input waits are parked.
        sql_read_timeout_seconds: per read-query budget exposed to skill SQL tools.
        sql_write_timeout_seconds: per write-statement budget.
        execution_mode: ``interactive`` or ``background``/``auto`` timeout tier.
        agent_snapshot: frozen agent profile (skills, data scope, max_turns, ...).
        permission_mode: write/high-risk confirmation policy for this run.
        runtime_limits: effective control-plane budgets carried through the
            internal sandbox protocol; public requests cannot set this field.
    """

    task_id: str
    topic_id: str
    question: str
    history: list[dict[str, str]]
    resume_session_id: str | None
    provider_id: str
    model: str
    database_hint: str | None
    debug: bool = False
    timeout_seconds: int | None = None
    sql_read_timeout_seconds: int | None = None
    sql_write_timeout_seconds: int | None = None
    execution_mode: str = "background"
    agent_snapshot: dict[str, Any] | None = None
    auth_user_id: str = ""
    permission_mode: str | None = None
    # Internal control-plane -> child contract, not a public request override.
    runtime_limits: dict[str, int] | None = None
    workspace_before: dict[str, tuple[int, str]] | None = None


@dataclass
class TaskExecutionResult:
    task_status: str
    content: str
    usage: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    provider_id: str = ""
    model: str = ""
    session_id: str = ""


async def _execute_task_stream(params: TaskExecutionInput, *,
    is_cancel_requested: Callable[[], Awaitable[Any] | Any] | None = None,
) -> TaskExecutionResult:
    cfg = get_settings()
    store = get_topic_task_store()
    target = resolve_runtime_provider_selection(params.provider_id, params.model)
    provider_id = str(target.get("provider_id") or "")
    model = str(target.get("model") or "")
    api_format = str(target.get("api_format") or "/v1/messages")
    snapshot = normalize_agent_snapshot(params.agent_snapshot) if params.agent_snapshot else None
    skills = resolve_agent_skill_runtime(snapshot, resolve_enabled_skill_runtime())
    roots = dict(skills.get("enabled_roots") or {})
    workspace = prepare_topic_workspace(params.topic_id, list(roots), allow_empty=True)
    saved = store.get_session_checkpoint(params.topic_id)
    state = dict(saved["state"]) if saved and saved["task_id"] == params.task_id else {}
    previous_usage = dict(state.get("usage") or {})
    interaction = state.get("interaction") or {}
    prompt = params.question
    operation = None
    if interaction:
        if not interaction.get("result"):
            raise ValueError("Session is still waiting for an answer")
        prompt = json.dumps({"request_id":interaction["request_id"],"kind":interaction["kind"],
            "user_response":interaction["result"]},ensure_ascii=False)
        if interaction["kind"] == "permission" and interaction["result"].get("decision") == "allow":
            operation = store.claim_confirmed_operation(topic_id=params.topic_id,task_id=params.task_id,request_id=interaction["request_id"])
    limits = params.runtime_limits or resolve_run_limits(cfg, params.execution_mode,
        agent_max_turns=int((snapshot or {}).get("max_turns") or 0),total_timeout_seconds=params.timeout_seconds)
    limits = dict(limits)
    limits["total_timeout_seconds"] -= math.ceil(previous_usage.get("elapsed_seconds",0))
    limits["max_turns"] -= int(previous_usage.get("turns",0))
    limits["max_tool_calls"] -= int(previous_usage.get("tool_calls",0))
    if min(limits["total_timeout_seconds"],limits["max_turns"]) <= 0 or limits["max_tool_calls"] < 0:
        raise ValueError("PI_LIMIT_EXCEEDED: cumulative task budget exhausted")
    limits["bash_timeout_seconds"] = min(limits["bash_timeout_seconds"],limits["total_timeout_seconds"])
    mode = normalize_permission_mode(params.permission_mode)
    plan_approved = bool(state.get("plan_approved"))
    runtime_env = {"DATAAGENT_PYTHON_BIN":"/usr/local/bin/python", "SKILLS_ROOT_DIR":"/opt/skills",
        "DATAAGENT_SKILL_ROOT": "/opt/skills/" + str(skills.get("primary_folder") or ""),
        "DATAAGENT_ENABLED_SKILLS": ",".join(roots),
        "DATAAGENT_ENABLED_SKILL_ROOTS":json.dumps({name:f"/opt/skills/{name}" for name in roots}),
        "DATAAGENT_PLAN_STATE":"approved" if plan_approved or mode != "plan" else "pending",
        "DATAAGENT_QUERY_LIMIT":str(cfg.query_result_limit),
        "DATAAGENT_RESULT_PREVIEW_ROWS":str(min(20,cfg.query_result_limit)),
        "DATAAGENT_ORIGINAL_QUESTION":params.question,
        "DATAAGENT_SQL_READ_TIMEOUT_SECONDS":str(min(params.sql_read_timeout_seconds or limits["total_timeout_seconds"],limits["total_timeout_seconds"])),
        "DATAAGENT_SQL_WRITE_TIMEOUT_SECONDS":str(min(params.sql_write_timeout_seconds or 60,limits["total_timeout_seconds"]))}
    runtime_env.update((snapshot or {}).get("env_vars") or {})
    run_id = uuid.uuid4().hex
    sandbox,results = build_sandbox_config(cfg,run_id=run_id,workspace=workspace,
        enabled_roots=roots,lifetime=limits["total_timeout_seconds"],env=runtime_env)
    provider_env = _build_provider_env(api_format,api_key=str(target.get("api_key") or ""),
        auth_token=str(target.get("auth_token") or ""),base_url=str(target.get("base_url") or ""))
    system_prompt = _build_system_prompt(params.database_hint,skills,snapshot)
    system_prompt += "\nWorking directory: /mnt/workspace. Saved results: /mnt/results (read-only). Skills: /opt/skills (read-only)."
    if not saved and params.history:
        system_prompt += "\nHistorical result caches at workspace/.dataagent/results were migrated to /mnt/results with the same filenames."
    if mode == "plan" and not plan_approved:
        system_prompt += "\n" + (Path(__file__).parents[1]/"prompts/plan_mode.md").read_text()
    await asyncio.to_thread(store.record_task_system_prompt,params.task_id,system_prompt)
    servers = _build_mcp_servers(cfg,(snapshot or {}).get("mcp_server_ids"),agent_snapshot=snapshot)
    ctx = PiRunContext(task_id=params.task_id,topic_id=params.topic_id,run_id=run_id,
        provider_id=provider_id,api_format=api_format,model=model,system_prompt=system_prompt,
        messages=[],history=params.history if not saved else [],prompt=prompt,project_cwd=workspace,
        provider_env=provider_env,runtime_env=runtime_env,sandbox=sandbox,results_dir=str(results),
        session_checkpoint={key:saved[key] for key in ("header","entries","leaf_id")} if saved else None,
        plan_approved=plan_approved,resume_operation=operation,permission_mode=mode,
        turn_offset=int(previous_usage.get("turns",0)),
        allow_questions=cfg.dataagent_ask_user_question_enabled,
        skills=[{"name":name,"root_path":f"/opt/skills/{name}"} for name in roots],
        mcp_servers=[{"name":name,**conf} for name,conf in servers.items()],
        max_output_tokens=target.get("max_output_tokens"),context_window=target.get("context_window"),
        governance_settings=build_governance_settings(cfg,provider_env),**limits)
    writer = PiEventWriter(store,params.task_id,params.topic_id,initial_turn_index=int(previous_usage.get("turns",0)))

    async def commit(checkpoint,request,elapsed,token_usage):
        next_state = {"plan_approved":plan_approved,"usage":{
            "elapsed_seconds":previous_usage.get("elapsed_seconds",0)+elapsed,
            "turns":previous_usage.get("turns",0)+int(checkpoint.get("turns") or 0),
            "tool_calls":previous_usage.get("tool_calls",0)+int(checkpoint.get("tool_calls") or 0),
            "tokens":accumulate_usage(previous_usage.get("tokens"),token_usage)},
            "workspace_before":params.workspace_before}
        card = None
        if request:
            kind = request["kind"]; data=request["payload"]; request_id=request["request_id"]
            if kind not in {"question","plan","permission"}: raise ValueError("Invalid interaction kind")
            next_state["interaction"] = request
            if kind == "question":
                deadline = question_deadline(cfg).iso
                card={"record_type":"question_request","data":{"request_id":request_id,"questions":data["questions"],"deadline_at":deadline}}
            else:
                deadline = plan_deadline(cfg,snapshot).iso if kind == "plan" else ""
                card={"record_type":"permission_request","data":{"request_id":request_id,
                    "tool_name":"ExitPlanMode" if kind == "plan" else data["tool_name"],
                    "risk_level":"plan" if kind == "plan" else "high","title":"请确认执行计划" if kind == "plan" else "请确认写操作",
                    "summary":data.get("plan") or data["tool_name"],"payload_preview":data,"deadline_at":deadline}}
                if kind == "plan": next_state["plan_approved"]=False
            next_state["interaction"]["deadline_at"] = deadline
        await asyncio.to_thread(store.commit_session_segment,task_id=params.task_id,checkpoint=checkpoint,
            expected_version=int((saved or {}).get("version",0)),state=next_state,card=card,turn_index=writer.turn_index)

    async def operation_result(payload):
        await asyncio.to_thread(store.save_confirmed_operation_result,topic_id=params.topic_id,task_id=params.task_id,
            request_id=payload["request_id"],result=payload["result"])

    async def cancelled():
        if is_cancel_requested is None: return None
        value=is_cancel_requested()
        return await value if inspect.isawaitable(value) else value

    outcome = await execute_pi_run(ctx,writer=writer,cancel_reason=cancelled,segment_handler=commit,operation_handler=operation_result)
    return TaskExecutionResult(task_status="parked" if outcome.terminal_status=="parked" else from_engine_outcome(outcome.terminal_status),
        content=outcome.answer or outcome.error_message,usage=accumulate_usage(previous_usage.get("tokens"),outcome.usage) or None,
        error={"code":outcome.error_code,"message":outcome.error_message} if outcome.error_code else None,
        provider_id=provider_id,model=model,session_id=f"pi-{params.topic_id}")


async def execute_task_stream(params: TaskExecutionInput, *, is_cancel_requested=None) -> TaskExecutionResult:
    from core.agent_identity import _identity, set_execution_identity
    token = set_execution_identity(params)
    try:
        return await _execute_task_stream(params,is_cancel_requested=is_cancel_requested)
    finally:
        _identity.reset(token)
