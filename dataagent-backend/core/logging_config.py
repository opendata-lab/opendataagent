"""One-line JSON logs to the console and, when a log directory is set, to daily files.

Format, rotation and field names follow
docs/design/2026-11-19-logging-standard-design.md, shared word for word with
OntoFoundry and OpenDataWorks. Service logs go to app.log; the Pi runtime's and
the sandboxes' diagnostics go to runtime.log through the ``dataagent.runtime``
logger.
"""

from __future__ import annotations

import contextvars
import gzip
import json
import logging
import logging.handlers
import os
import re
import shutil
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

SERVICE_NAME = "dataagent-backend"
RUNTIME_LOGGER = "dataagent.runtime"
RETENTION_DAYS = 30
MAX_FIELD_CHARS = 8192

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
# Set for the life of one task run; asyncio tasks it starts inherit it.
task_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("task_id", default=None)
topic_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("topic_id", default=None)

_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_LEVELS = {"WARNING": "WARN", "CRITICAL": "ERROR"}
# Fields a caller may attach with logger.info(..., extra={...}); anything else is ignored.
_EXTRA_FIELDS = (
    "task_id",
    "run_id",
    "topic_id",
    "source",
    "error_code",
    "method",
    "path",
    "status_code",
    "duration_ms",
)
_SECRETS = (
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9_.\-+/=]+"), r"\1 ***"),
    (re.compile(r"\b(sk-[A-Za-z0-9_\-]{8})[A-Za-z0-9_\-]+"), r"\1***"),
    (re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?key)(\"?\s*[:=]\s*\"?)[^\s\"',;&]+"), r"\1\2***"),
    (re.compile(r"(://[^/\s:@]+):[^/\s@]+@"), r"\1:***@"),
)


def valid_request_id(value: str | None) -> str | None:
    return value if value and _REQUEST_ID.match(value) else None


def redact(text: str) -> str:
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text if len(text) <= MAX_FIELD_CHARS else text[:MAX_FIELD_CHARS] + "…(truncated)"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": _LEVELS.get(record.levelname, record.levelname),
            "service": SERVICE_NAME,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        for name, var in (("request_id", request_id_var), ("task_id", task_id_var), ("topic_id", topic_id_var)):
            value = var.get()
            if value:
                entry[name] = value
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                entry[field] = value
        if record.exc_info:
            entry["stack_trace"] = redact("".join(traceback.format_exception(*record.exc_info)))
        return json.dumps(entry, ensure_ascii=False, default=str)


def _gzip_rotator(source: str, dest: str) -> None:
    with open(source, "rb") as src, gzip.open(dest, "wb") as out:
        shutil.copyfileobj(src, out)
    os.remove(source)


def daily_file_handler(path: Path) -> logging.Handler:
    """Rotate at UTC midnight into <name>.YYYY-MM-DD.gz and keep RETENTION_DAYS of them.

    Only one process may write a given file: TimedRotatingFileHandler does not
    coordinate rollover across processes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.TimedRotatingFileHandler(
        path, when="midnight", utc=True, backupCount=RETENTION_DAYS, encoding="utf-8"
    )
    handler.namer = lambda name: name + ".gz"
    handler.rotator = _gzip_rotator
    return handler


def _attach(logger: logging.Logger, handlers: list[logging.Handler]) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    for handler in handlers:
        logger.addHandler(handler)


def configure_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    formatter = JsonFormatter()

    def console() -> logging.Handler:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        return handler

    def file(name: str) -> logging.Handler:
        handler = daily_file_handler(log_dir / name)  # type: ignore[operator]
        handler.setFormatter(formatter)
        return handler

    root = logging.getLogger()
    _attach(root, [console(), *([file("app.log")] if log_dir else [])])
    root.setLevel(level.upper())
    logging.captureWarnings(True)

    # Runtime and sandbox diagnostics: their own file, still on the console.
    runtime = logging.getLogger(RUNTIME_LOGGER)
    _attach(runtime, [console(), *([file("runtime.log")] if log_dir else [])])
    runtime.propagate = False

    # Uvicorn installs its own text handlers before importing the app; route its
    # messages through the root handlers. Requests are logged by the app's
    # middleware, so the access logger stays silent.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True
    for name in ("httpx", "httpcore", "sqlalchemy", "alembic", "multipart", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)


def log_runtime_line(line: str, *, task_id: str) -> None:
    """Forward one stderr line of the Pi runtime to runtime.log.

    The runtime writes {"level", "message", "source"} JSON; anything else (a
    Node crash trace, a library writing to stderr) is kept as plain text.
    """
    logger = logging.getLogger(RUNTIME_LOGGER)
    level, message, source = "INFO", line, "runtime"
    try:
        parsed = json.loads(line)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict) and "message" in parsed:
        level = str(parsed.get("level") or "INFO").upper()
        message = str(parsed["message"])
        source = "sandbox" if parsed.get("source") == "sandbox" else "runtime"
    elif line.strip():
        level = "WARN"
    numeric = {"DEBUG": logging.DEBUG, "WARN": logging.WARNING, "ERROR": logging.ERROR}.get(level, logging.INFO)
    logger.log(numeric, message, extra={"source": source, "task_id": task_id})
