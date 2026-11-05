"""Protocol-v2 supervisor for one Pi SDK execution segment over stdio.

The backend owns provider configuration, events, checkpoints and scheduling.
A card commits session.entries atomically with its waiting state, then releases
this process; a reply starts another segment from that checkpoint. Approved
MCP operation results are acknowledged only after durable storage. Each segment
owns one lazily started tools-only sandbox, cleaned here if the Cell is killed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from config import get_settings

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 2

# Grace period between asking the Cell to stop and killing it. One turn of the
# agent loop is far shorter than this; anything still running is wedged.
CANCEL_GRACE_SECONDS = 2.0
# The Cell answers cell.init as soon as its module graph is loaded.
HANDSHAKE_TIMEOUT_SECONDS = 10.0
# How often the cancel flag is polled while events stream.
CANCEL_POLL_INTERVAL_SECONDS = 1.0

TERMINAL_EVENT_TYPES = frozenset({"run.completed", "run.failed", "run.cancelled", "run.suspended"})


class PiRuntimeUnavailable(RuntimeError):
    """The Pi Cell entrypoint or its Node runtime is missing."""


@dataclass
class PiRunContext:
    """Everything the Cell needs for one run, assembled by the control plane."""

    task_id: str
    topic_id: str
    provider_id: str
    api_format: str
    model: str
    system_prompt: str
    messages: list[dict[str, str]]
    project_cwd: Path
    # Effective budgets are required; config.py owns defaults and inheritance.
    total_timeout_seconds: int
    idle_timeout_seconds: int
    max_turns: int
    max_tool_calls: int
    bash_timeout_seconds: int
    history: list[dict[str, str]] = field(default_factory=list)
    prompt: str = ""
    runtime_env: dict[str, str] = field(default_factory=dict)
    provider_env: dict[str, str] = field(default_factory=dict)
    skills: list[dict[str, str]] = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    # The model's configured output ceiling per reply; None lets the Cell use its default.
    max_output_tokens: int | None = None
    # The model's context window; None lets governance_settings.max_context_tokens decide.
    context_window: int | None = None
    # "plan" exposes ExitPlanMode and gates writes until approval.
    permission_mode: str = "default"
    # Context governance, assembled by core.context_governance so the wire
    # contract has one producer. Field names mirror ``governance_settings`` in
    # ``src/protocol/frames.ts``; a rename on either side has to move in
    # lockstep or the Cell silently falls back to its own defaults.
    governance_settings: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    sandbox: dict[str, Any] = field(default_factory=dict)
    results_dir: str = ""
    session_checkpoint: dict[str, Any] | None = None
    plan_approved: bool = False
    turn_offset: int = 0
    allow_questions: bool = True
    resume_operation: dict[str, Any] | None = None

    def to_init_payload(self) -> dict[str, Any]:
        """Serialize for the ``cell.init`` frame.

        Provider credentials are deliberately absent: they travel through the
        child's environment instead, so they never appear on stdio, in a log
        line, or in any persisted record.
        """
        prompt_val = self.prompt
        history_val = list(self.history)
        if not prompt_val and self.messages:
            prompt_val = str(self.messages[-1].get("content") or "").strip()
            if not history_val:
                history_val = self.messages[:-1]

        return {
            "run_id": self.run_id or self.task_id,
            "task_id": self.task_id,
            "topic_id": self.topic_id,
            "system_prompt": self.system_prompt,
            "messages": self.messages,
            "history": history_val,
            "prompt": prompt_val,
            "model": {
                "provider_id": self.provider_id,
                "api_format": self.api_format,
                "model_id": self.model,
                **({"max_output_tokens": self.max_output_tokens} if self.max_output_tokens else {}),
                **({"context_window": self.context_window} if self.context_window else {}),
            },
            "workspace": {"project_cwd": str(self.project_cwd)},
            "sandbox": self.sandbox,
            "results_dir": self.results_dir,
            "session_checkpoint": self.session_checkpoint,
            "plan_approved": self.plan_approved,
            "turn_offset": self.turn_offset,
            "allow_questions": self.allow_questions,
            "resume_operation": self.resume_operation,
            "skills": self.skills,
            "mcp_servers": self.mcp_servers,
            "runtime_env": self.runtime_env,
            "limits": {
                "total_timeout_seconds": self.total_timeout_seconds,
                "idle_timeout_seconds": self.idle_timeout_seconds,
                "max_turns": self.max_turns,
                "max_tool_calls": self.max_tool_calls,
                "bash_timeout_seconds": self.bash_timeout_seconds,
            },
            "governance_settings": dict(self.governance_settings),
            "permission_mode": self.permission_mode,
        }


def resolve_cell_command(cfg: Any = None) -> list[str]:
    """Locate the Node binary and the built Cell entrypoint.

    Raises :class:`PiRuntimeUnavailable` with an actionable message rather than
    letting a spawn failure surface as an opaque OSError, mirroring how the SDK
    path reports ``sdk_not_installed``.
    """
    cfg = cfg if cfg is not None else get_settings()
    node_bin = str(getattr(cfg, "dataagent_node_bin", "") or "").strip()
    node_bin = node_bin or os.environ.get("DATAAGENT_NODE_BIN", "").strip() or shutil.which("node") or ""
    if not node_bin:
        raise PiRuntimeUnavailable("找不到 Node 运行时：请设置 DATAAGENT_NODE_BIN 或将 node 加入 PATH")

    pi_dir = str(getattr(cfg, "dataagent_runtime_pi_dir", "") or "").strip()
    pi_dir = pi_dir or os.environ.get("DATAAGENT_RUNTIME_PI_DIR", "").strip()
    if pi_dir:
        root = Path(pi_dir)
    else:
        # dataagent-backend/core/pi_runtime.py -> dataagent-runtime-pi
        root = Path(__file__).resolve().parents[2] / "dataagent-runtime-pi"

    entrypoint = root / "dist" / "src" / "main.js"
    if not entrypoint.exists():
        raise PiRuntimeUnavailable(
            f"Pi Cell 入口不存在：{entrypoint}（请先在 {root} 执行 npm ci && npm run build）"
        )
    return [node_bin, str(entrypoint)]


def resolve_mcp_probe_command(cfg: Any = None) -> list[str]:
    """Locate the MCP probe built alongside the Pi Cell."""
    cell_command = resolve_cell_command(cfg)
    entrypoint = Path(cell_command[1]).parent / "mcp" / "probe-cli.js"
    if not entrypoint.exists():
        raise PiRuntimeUnavailable(
            f"MCP 检测入口不存在：{entrypoint}（请重新构建 dataagent-runtime-pi）"
        )
    return [cell_command[0], str(entrypoint)]


def _frame(frame_type: str, payload: dict[str, Any] | None = None) -> str:
    return json.dumps(
        {"protocol_version": PROTOCOL_VERSION, "type": frame_type, "payload": payload or {}},
        separators=(",", ":"),
        ensure_ascii=False,
    )


class _CellChannel:
    """stdio framing for one Cell child process."""

    def __init__(self, process: asyncio.subprocess.Process, task_id: str) -> None:
        self._process = process
        self._task_id = task_id

    async def send(self, frame_type: str, payload: dict[str, Any] | None = None) -> None:
        stdin = self._process.stdin
        if stdin is None or stdin.is_closing():
            return
        try:
            stdin.write((_frame(frame_type, payload) + "\n").encode("utf-8"))
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            # The child is gone; the stdout reader will surface the loss.
            logger.warning("pi_cell.stdin_closed task_id=%s frame=%s", self._task_id, frame_type)

    async def read_frame(self) -> Optional[dict[str, Any]]:
        """Next protocol frame, or ``None`` at end of stream.

        A blank line is skipped rather than treated as EOF, and a line that is
        not valid JSON is logged and skipped rather than tearing the channel
        down: the child is still alive and its next line may well be fine.
        Losing the channel on one stray line would strand a healthy run.
        """
        stdout = self._process.stdout
        if stdout is None:
            return None
        while True:
            raw = await stdout.readline()
            if not raw:
                return None
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("pi_cell.non_json_line task_id=%s line=%.200s", self._task_id, line)
                continue
            if not isinstance(parsed, dict):
                logger.warning("pi_cell.non_object_frame task_id=%s line=%.200s", self._task_id, line)
                continue
            return parsed


async def _drain_stderr(process: asyncio.subprocess.Process, task_id: str) -> None:
    """Mirror the child's stderr into the task log; never touches the protocol."""
    stderr = process.stderr
    if stderr is None:
        return
    try:
        while True:
            raw = await stderr.readline()
            if not raw:
                return
            logger.info("pi_cell.stderr task_id=%s %s", task_id, raw.decode("utf-8", errors="replace").rstrip())
    except Exception:
        logger.debug("pi_cell.stderr_reader_stopped task_id=%s", task_id, exc_info=True)


async def _terminate(process: asyncio.subprocess.Process, task_id: str) -> None:
    """Ensure the child is gone.

    Always called, including on the success path: a Cell that has settled its
    run but not exited would otherwise linger as an orphan holding the
    workspace open.
    """
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=CANCEL_GRACE_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("pi_cell.kill_after_grace task_id=%s", task_id)
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=CANCEL_GRACE_SECONDS)
        except asyncio.TimeoutError:
            logger.error("pi_cell.kill_timeout task_id=%s", task_id)


@dataclass
class PiRunOutcome:
    """Engine-level result, translated to TaskExecutionResult by the caller."""

    terminal_status: str  # "success" | "failed" | "cancelled"
    answer: str = ""
    error_code: str = ""
    error_message: str = ""
    usage: dict[str, Any] | None = None
    last_sequence: int = 0


def _stdio_frame_limit() -> int:
    """Bytes a single NDJSON frame may occupy on the way back from the Cell.

    One frame must fit in the reader's buffer or readline() raises "Separator is
    not found, and chunk exceed the limit" and takes the whole run with it —
    every completed turn lost, and nothing in the failed message to say why. The
    Cell may send a UI copy of a tool result up to STRUCTURED_OUTPUT_MAX_BYTES
    (512 KiB) in one frame, so asyncio's 64 KiB default is far too small.

    Uses agent_max_buffer_size_bytes (10 MiB by default), which also covers
    the bounded 8 MiB session snapshot and protocol envelope.
    """
    configured = int(getattr(get_settings(), "agent_max_buffer_size_bytes", 0) or 0)
    return max(1024 * 1024, configured)


def accumulate_usage(previous: dict[str, Any] | None, current: dict[str, Any] | None) -> dict[str, Any]:
    total = dict(previous or {})
    for key, value in (current or {}).items():
        if isinstance(value, dict):
            total[key] = accumulate_usage(total.get(key), value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            total[key] = (total.get(key) or 0) + value
        else:
            total[key] = value
    return total


async def execute_pi_run(
    ctx: PiRunContext, *, writer: Any,
    cancel_reason: Callable[[], Awaitable[Any]] | None = None,
    cell_command: list[str] | None = None,
    child_env: dict[str, str] | None = None,
    segment_handler: Callable[..., Awaitable[None]] | None = None,
    operation_handler: Callable[..., Awaitable[None]] | None = None,
) -> PiRunOutcome:
    command = cell_command or resolve_cell_command()
    env = dict(child_env) if child_env is not None else dict(os.environ)
    env.update(ctx.provider_env)
    env.setdefault("NODE_ENV", "production")
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=str(ctx.project_cwd), env=env, limit=_stdio_frame_limit())
    channel = _CellChannel(process, ctx.task_id)
    stderr_task = asyncio.create_task(_drain_stderr(process, ctx.task_id))
    started = time.monotonic()
    last_activity = started
    cancel_deadline = None
    snapshot = None
    highest_sequence = 0
    answer_parts: list[str] = []
    usage = None
    sandbox_pending = False
    try:
        await channel.send("cell.init", ctx.to_init_payload())
        while True:
            now = time.monotonic()
            reason = await cancel_reason() if cancel_reason and cancel_deadline is None else None
            if reason:
                cancel_deadline = now + CANCEL_GRACE_SECONDS
                await channel.send("run.cancel", {"reason": str(reason)})
            if cancel_deadline is not None and now >= cancel_deadline:
                return PiRunOutcome(terminal_status="cancelled", answer="".join(answer_parts), usage=usage, last_sequence=highest_sequence)
            if now - started >= ctx.total_timeout_seconds:
                raise TimeoutError("PI_RUN_TIMEOUT")
            if ctx.idle_timeout_seconds > 0 and now - last_activity >= ctx.idle_timeout_seconds:
                raise TimeoutError("PI_RUN_IDLE_TIMEOUT")
            try:
                frame = await asyncio.wait_for(channel.read_frame(), timeout=CANCEL_POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                continue
            if frame is None:
                raise RuntimeError("CELL_LOSS")
            if frame.get("protocol_version") != PROTOCOL_VERSION:
                raise RuntimeError("PI_PROTOCOL_VERSION_MISMATCH")
            last_activity = time.monotonic()
            ftype = frame.get("type")
            payload = frame.get("payload") or {}
            if ftype == "run.heartbeat":
                if payload.get("sandbox_state") == "starting": sandbox_pending = True
                elif payload.get("sandbox_state") == "closed": sandbox_pending = False
            elif ftype == "session.entries":
                if snapshot is not None:
                    raise ValueError("Duplicate session snapshot")
                snapshot = payload
            elif ftype == "operation.result":
                if operation_handler is None:
                    raise ValueError("Operation persistence unavailable")
                await operation_handler(payload)
                await channel.send("operation.saved", {"request_id": payload["request_id"]})
            elif ftype == "run.parked":
                if snapshot is None or segment_handler is None:
                    raise ValueError("Parked segment has no persistent snapshot")
                await segment_handler(snapshot, payload.get("interaction"), time.monotonic() - started)
                return PiRunOutcome(terminal_status="parked", answer="".join(answer_parts), usage=usage, last_sequence=highest_sequence)
            elif ftype == "run.event":
                event_type = payload.get("type")
                event_data = payload.get("payload") or {}
                highest_sequence = max(highest_sequence, int(payload.get("sequence") or 0))
                if event_type == "run.completed":
                    if snapshot is None or segment_handler is None:
                        raise ValueError("Completed segment has no persistent snapshot")
                    await segment_handler(snapshot, None, time.monotonic() - started)
                writer.ingest(payload)
                if event_type == "content.delta" and event_data.get("kind") != "reasoning":
                    answer_parts.append(str(event_data.get("delta") or ""))
                elif event_type == "usage.updated":
                    usage = accumulate_usage(usage, event_data.get("usage"))
                elif event_type in TERMINAL_EVENT_TYPES:
                    return _outcome_from_terminal_event(event_type,event_data,"".join(answer_parts),usage,highest_sequence)
            elif ftype == "protocol.error":
                raise RuntimeError(str(payload.get("error") or "PI_PROTOCOL_ERROR"))
            elif ftype == "run.settled":
                raise RuntimeError("PI_SETTLED_WITHOUT_TERMINAL_EVENT")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        code = "task_cancelled" if cancel_deadline is not None else str(exc)
        writer.ingest({"event_id": f"ev-{ctx.run_id}-terminal", "run_id":ctx.run_id,
            "task_id":ctx.task_id,"task_attempt_id":ctx.run_id,"sequence":highest_sequence+1,
            "type":"run.cancelled" if cancel_deadline is not None else "run.failed",
            "payload":{"error_code":code,"message":str(exc)}})
        return PiRunOutcome(terminal_status="cancelled" if cancel_deadline is not None else "failed",
            answer="".join(answer_parts), error_code=code, error_message=str(exc), usage=usage,last_sequence=highest_sequence)
    finally:
        await _terminate(process,ctx.task_id)
        stderr_task.cancel()
        try:
            await stderr_task
        except (asyncio.CancelledError,Exception):
            pass
        if ctx.sandbox and sandbox_pending:
            from core.sandbox_config import remove_segment_sandbox
            await remove_segment_sandbox(ctx.sandbox)


def _outcome_from_terminal_event(
    event_type: str,
    payload: dict[str, Any],
    answer: str,
    usage: dict[str, Any] | None,
    last_sequence: int,
) -> PiRunOutcome:
    if event_type == "run.completed":
        return PiRunOutcome(
            terminal_status="success",
            answer=str(payload.get("answer") or answer),
            usage=usage,
            last_sequence=last_sequence,
        )
    if event_type == "run.cancelled":
        return PiRunOutcome(terminal_status="cancelled", answer=answer, usage=usage, last_sequence=last_sequence)
    return PiRunOutcome(
        terminal_status="failed",
        answer=answer,
        error_code=str(payload.get("error_code") or "PI_EXECUTION_ERROR"),
        error_message=str(payload.get("message") or "Pi 运行时执行失败"),
        usage=usage,
        last_sequence=last_sequence,
    )
