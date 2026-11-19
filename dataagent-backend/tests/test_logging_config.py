"""One-line JSON logs, request and task ids, runtime.log (docs/design/2026-11-19-logging-standard-design.md)."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.logging_config import (  # noqa: E402
    RUNTIME_LOGGER,
    JsonFormatter,
    configure_logging,
    log_runtime_line,
    redact,
    request_id_var,
    task_id_var,
)


def _record(message: str, **extra) -> logging.LogRecord:
    record = logging.LogRecord("core.test", logging.WARNING, __file__, 1, message, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_a_record_is_one_line_of_json_with_context_ids():
    tokens = (request_id_var.set("req-1"), task_id_var.set("task-9"))
    try:
        line = JsonFormatter().format(_record("a\nb", run_id="run-1", other="x"))
    finally:
        request_id_var.reset(tokens[0])
        task_id_var.reset(tokens[1])
    assert "\n" not in line
    entry = json.loads(line)
    assert entry["service"] == "dataagent-backend"
    assert entry["level"] == "WARN"
    assert entry["timestamp"].endswith("Z")
    assert (entry["request_id"], entry["task_id"], entry["run_id"]) == ("req-1", "task-9", "run-1")
    assert entry["message"] == "a\nb"
    assert "other" not in entry


def test_credentials_are_redacted():
    text = redact(
        "Authorization: Bearer abc.def key sk-abcdefgh12345678 password=hunter2 mysql://u:pw@db/x "
        "access_token=s1 client_secret=s2 {'password': 's3'} Authorization: Basic s4 "
        "https://mcp.invalid/sse?access_token=s5&x=1"
    )
    for secret in ("abc.def", "12345678", "hunter2", ":pw@", "s1", "s2", "s3", "s4", "s5"):
        assert secret not in text
    assert redact("max_tokens=4096") == "max_tokens=4096"


def _configured(tmp_path):
    root = logging.getLogger()
    runtime = logging.getLogger(RUNTIME_LOGGER)
    saved = (list(root.handlers), root.level, list(runtime.handlers), runtime.propagate)
    configure_logging("INFO", tmp_path)

    def restore():
        for logger, handlers in ((root, saved[0]), (runtime, saved[2])):
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in handlers:
                logger.addHandler(handler)
        root.setLevel(saved[1])
        runtime.propagate = saved[3]
        logging.captureWarnings(False)

    return restore


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_runtime_lines_go_to_runtime_log_and_service_lines_to_app_log(tmp_path):
    restore = _configured(tmp_path)
    try:
        logging.getLogger("core.service").info("service line")
        log_runtime_line(json.dumps({"level": "WARN", "source": "sandbox", "message": "sandbox start"}), task_id="t-1")
        log_runtime_line("FATAL ERROR: Reached heap limit", task_id="t-1")
        log_runtime_line(json.dumps({"level": "INFO", "message": "model retry", "source": "bogus"}), task_id="t-2")
    finally:
        restore()

    app = _lines(tmp_path / "app.log")
    runtime = _lines(tmp_path / "runtime.log")
    assert [e["message"] for e in app] == ["service line"]
    assert [(e["level"], e["source"], e["task_id"], e["message"]) for e in runtime] == [
        ("WARN", "sandbox", "t-1", "sandbox start"),
        ("WARN", "runtime", "t-1", "FATAL ERROR: Reached heap limit"),
        ("INFO", "runtime", "t-2", "model retry"),
    ]


def test_uvicorn_goes_through_the_root_handlers(tmp_path):
    restore = _configured(tmp_path)
    try:
        assert logging.getLogger("uvicorn.error").propagate
        assert logging.getLogger("uvicorn.error").handlers == []
        assert logging.getLogger("uvicorn.access").disabled
    finally:
        restore()


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_requests_get_an_id_and_one_completion_line():
    from fastapi.testclient import TestClient

    from main import app  # importing main configures logging, replacing root handlers

    collect = _Collect()
    request_logger = logging.getLogger("dataagent.request")
    request_logger.addHandler(collect)
    try:
        client = TestClient(app)  # no context manager: startup (MySQL, Redis) is not run
        passed = client.get("/", headers={"X-Request-ID": "of-req-7"})
        replaced = client.get("/", headers={"X-Request-ID": "not valid!"})
    finally:
        request_logger.removeHandler(collect)

    assert passed.headers["X-Request-ID"] == "of-req-7"
    assert replaced.headers["X-Request-ID"] != "not valid!"
    assert [(r.path, r.status_code) for r in collect.records] == [("/", 200), ("/", 200)]


def _middleware_app(lines: list[dict]):
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse

    from core.logging_config import RequestContextMiddleware

    app = FastAPI()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("password=hunter2 exploded")

    @app.get("/stream")
    async def stream():
        async def body():
            lines.append({"message": "body finished"})
            yield b"a"

        return StreamingResponse(body())

    app.add_middleware(RequestContextMiddleware, logger=logging.getLogger("dataagent.request"), skip_path_suffix="/health")
    return app


class _Formatted(logging.Handler):
    """Formats while the request context is still active, as a real handler does."""

    def __init__(self, lines: list[dict]) -> None:
        super().__init__()
        self.lines = lines

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(JsonFormatter().format(record)))


def _with_request_logger(lines, run):
    handler = _Formatted(lines)
    request_logger = logging.getLogger("dataagent.request")
    request_logger.addHandler(handler)
    previous = request_logger.level
    request_logger.setLevel(logging.INFO)
    try:
        return run()
    finally:
        request_logger.removeHandler(handler)
        request_logger.setLevel(previous)


def test_an_unhandled_error_keeps_its_request_id():
    from fastapi.testclient import TestClient

    lines: list[dict] = []
    app = _middleware_app(lines)
    response = _with_request_logger(lines, lambda: TestClient(app, raise_server_exceptions=False).get("/boom", headers={"X-Request-ID": "req-500"}))

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "req-500"
    error, done = lines
    assert (error["message"], error["request_id"]) == ("unhandled error", "req-500")
    assert "RuntimeError" in error["stack_trace"] and "hunter2" not in error["stack_trace"]
    assert (done["message"], done["status_code"]) == ("request completed", 500)


def test_a_streaming_response_is_logged_when_it_ends():
    from fastapi.testclient import TestClient

    lines: list[dict] = []
    app = _middleware_app(lines)
    response = _with_request_logger(lines, lambda: TestClient(app).get("/stream"))
    assert response.text == "a"
    assert [line["message"] for line in lines] == ["body finished", "request completed"]
