"""Unit tests for the Pi data plane's event writer."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import live_events  # noqa: E402
from core.pi_event_writer import PiEventWriter  # noqa: E402


class _FakeStore:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def append_sdk_record(self, **kwargs: Any) -> int:
        self.records.append(kwargs)
        return len(self.records)


def _drain(sub: live_events.Subscription) -> list[Any]:
    items = []
    while not sub.queue.empty():
        items.append(sub.queue.get_nowait())
    return items


def _event(sequence: int, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "event_id": f"ev-{sequence}",
        "run_id": "task-1",
        "task_id": "task-1",
        "task_attempt_id": "attempt-1",
        "sequence": sequence,
        "type": event_type,
        "payload": payload or {},
    }


def test_writes_neutral_records_as_agent_event():
    """The record type carries the envelope now; "pi_event" is the legacy name.

    Both are accepted on read, so existing rows keep replaying.
    """
    store = _FakeStore()
    writer = PiEventWriter(store, "task-1", "topic-1")

    writer.ingest(_event(1, "run.started"))
    writer.ingest(_event(2, "turn.started", {"turn_id": "turn-1"}))
    writer.ingest(_event(3, "content.completed", {"turn_id": "turn-1", "content_id": "c-0", "kind": "answer", "text": "hi"}))

    assert [r["record_type"] for r in store.records] == ["agent_event"] * 3
    assert [r["event_type"] for r in store.records] == ["run.started", "turn.started", "content.completed"]


def test_content_delta_is_streamed_live_and_never_stored():
    store = _FakeStore()
    writer = PiEventWriter(store, "task-1", "topic-1")
    live_events.open_channel("task-1", writer.open_blocks_snapshot)
    try:
        sub = live_events.subscribe("task-1")
        writer.ingest(_event(1, "turn.started", {"turn_id": "turn-1"}))
        writer.ingest(_event(2, "content.delta", {"turn_id": "turn-1", "content_id": "c-0", "kind": "answer", "delta": "he"}))
        writer.ingest(_event(3, "content.delta", {"turn_id": "turn-1", "content_id": "c-0", "kind": "answer", "delta": "llo"}))

        assert [r["event_type"] for r in store.records] == ["turn.started"]
        assert _drain(sub) == [
            {"seq_id": 1},
            {"turn_index": 1, "record_type": "agent_event", "event_type": "content.delta",
             "data": {"turn_id": "turn-1", "content_id": "c-0", "kind": "answer", "delta": "he"}},
            {"turn_index": 1, "record_type": "agent_event", "event_type": "content.delta",
             "data": {"turn_id": "turn-1", "content_id": "c-0", "kind": "answer", "delta": "llo"}},
        ]
        # A late subscriber gets the text so far as one replacing snapshot.
        assert live_events.subscribe("task-1").snapshot == [
            {"turn_index": 1, "record_type": "agent_event", "event_type": "content.delta",
             "data": {"turn_id": "turn-1", "content_id": "c-0", "kind": "answer", "text": "hello"}},
        ]
    finally:
        live_events.close_channel("task-1")


def test_completed_block_leaves_nothing_to_flush():
    store = _FakeStore()
    writer = PiEventWriter(store, "task-1", "topic-1")

    writer.ingest(_event(1, "content.delta", {"turn_id": "t", "content_id": "c-0", "kind": "answer", "delta": "hi"}))
    writer.ingest(_event(2, "content.completed", {"turn_id": "t", "content_id": "c-0", "kind": "answer", "text": "hi"}))
    writer.ingest(_event(3, "run.completed", {"terminal_status": "success"}))
    writer.flush_open_blocks()

    assert [r["event_type"] for r in store.records] == ["content.completed", "run.completed"]
    assert writer.open_blocks_snapshot() == []


def test_interrupted_run_stores_each_unfinished_block_once_before_the_terminal_event():
    store = _FakeStore()
    writer = PiEventWriter(store, "task-1", "topic-1")

    writer.ingest(_event(1, "turn.started", {"turn_id": "t"}))
    writer.ingest(_event(2, "content.delta", {"turn_id": "t", "content_id": "r-0", "kind": "reasoning", "delta": "think"}))
    writer.ingest(_event(3, "content.delta", {"turn_id": "t", "content_id": "c-0", "kind": "answer", "delta": "par"}))
    writer.ingest(_event(4, "content.delta", {"turn_id": "t", "content_id": "c-0", "kind": "answer", "delta": "tial"}))
    writer.ingest(_event(5, "run.cancelled", {"reason": "user"}))
    writer.flush_open_blocks()  # the runtime's finally: nothing left to store

    assert [(r["event_type"], r["data"].get("text")) for r in store.records] == [
        ("turn.started", None),
        ("content.delta", "think"),
        ("content.delta", "partial"),
        ("run.cancelled", None),
    ]
    flushed = store.records[2]
    assert flushed["data"] == {"turn_id": "t", "content_id": "c-0", "kind": "answer", "text": "partial"}
    assert flushed["turn_index"] == 1
    assert flushed["envelope"]["event_id"] == "ev-4"


def test_flush_on_exit_without_terminal_event_keeps_the_text():
    store = _FakeStore()
    writer = PiEventWriter(store, "task-1", "topic-1")

    writer.ingest(_event(1, "content.delta", {"turn_id": "t", "content_id": "c-0", "kind": "answer", "delta": "abc"}))
    writer.flush_open_blocks()
    writer.flush_open_blocks()

    assert [(r["event_type"], r["data"]["text"]) for r in store.records] == [("content.delta", "abc")]


def test_turn_index_advances_on_turn_started():
    store = _FakeStore()
    writer = PiEventWriter(store, "task-1", "topic-1")

    writer.ingest(_event(1, "run.started"))
    assert store.records[-1]["turn_index"] == 0

    writer.ingest(_event(2, "turn.started", {"turn_id": "turn-1"}))
    assert store.records[-1]["turn_index"] == 1

    writer.ingest(_event(3, "turn.started", {"turn_id": "turn-2"}))
    assert store.records[-1]["turn_index"] == 2
    assert writer.turn_index == 2


def test_out_of_order_and_replayed_events_are_dropped():
    store = _FakeStore()
    writer = PiEventWriter(store, "task-1", "topic-1")

    writer.ingest(_event(1, "run.started"))
    writer.ingest(_event(3, "content.delta", {"content_id": "c-0", "delta": "kept"}))
    writer.ingest(_event(2, "content.delta", {"content_id": "c-0", "delta": "stale"}))
    writer.ingest(_event(3, "content.delta", {"content_id": "c-0", "delta": "replayed"}))
    writer.flush_open_blocks()

    assert [r["data"].get("text") for r in store.records] == [None, "kept"]


def test_run_failed_also_emits_the_shared_error_record():
    """Terminal failures must surface through the same record type as the SDK path."""
    store = _FakeStore()
    writer = PiEventWriter(store, "task-1", "topic-1")

    writer.ingest(
        _event(1, "run.failed", {"error_code": "CELL_LOSS", "message": "child exited", "detail": "signal 9"})
    )

    assert [r["record_type"] for r in store.records] == ["agent_event", "error"]
    error_payload = store.records[-1]["data"]
    assert error_payload["code"] == "CELL_LOSS"
    assert error_payload["message"] == "child exited"
    assert error_payload["detail"] == "signal 9"


def test_malformed_event_never_raises():
    store = _FakeStore()
    writer = PiEventWriter(store, "task-1", "topic-1")

    writer.ingest({})                      # no type
    writer.ingest({"type": ""})            # blank type
    writer.ingest("not a dict")            # type: ignore[arg-type]
    writer.ingest({"type": "content.completed", "payload": "not a dict"})

    # Only the last one is well-formed enough to record; payload coerces to {}.
    assert [r["event_type"] for r in store.records] == ["content.completed"]
    assert store.records[0]["data"] == {}


def test_store_failure_is_swallowed_not_propagated():
    """A persistence hiccup must not abort an in-flight run."""

    class _BrokenStore:
        def append_sdk_record(self, **kwargs: Any) -> None:
            raise RuntimeError("db down")

    writer = PiEventWriter(_BrokenStore(), "task-1", "topic-1")
    writer.ingest(_event(1, "content.delta", {"delta": "x"}))
    writer.ingest(_event(2, "run.failed", {"error_code": "X"}))
    writer.flush_open_blocks()


def test_block_text_is_kept_until_its_row_is_stored():
    """A failed insert must not lose text that exists nowhere else."""

    class _FlakyStore(_FakeStore):
        def __init__(self) -> None:
            super().__init__()
            self.failures = 2

        def append_sdk_record(self, **kwargs: Any) -> int:
            if self.failures:
                self.failures -= 1
                raise RuntimeError("db hiccup")
            return super().append_sdk_record(**kwargs)

    store = _FlakyStore()
    writer = PiEventWriter(store, "task-1", "topic-1")
    writer.ingest(_event(1, "content.delta", {"turn_id": "t", "content_id": "c-0", "kind": "answer", "delta": "hello"}))
    writer.ingest(_event(2, "content.completed", {"turn_id": "t", "content_id": "c-0", "kind": "answer", "text": "hello"}))  # fails
    writer.flush_open_blocks()  # fails, block kept
    writer.flush_open_blocks()  # succeeds

    assert [(r["event_type"], r["data"].get("text")) for r in store.records] == [("content.delta", "hello")]
    assert writer.open_blocks_snapshot() == []
