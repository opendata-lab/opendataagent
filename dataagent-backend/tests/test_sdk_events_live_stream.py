"""SSE live mode: deltas from memory, persistent rows from MySQL, in event order.

Design: docs/design/2026-11-13-content-delta-live-stream-design.md.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import routes  # noqa: E402
from core import live_events  # noqa: E402
from core.pi_event_writer import PiEventWriter  # noqa: E402

TASK = "task-live"


class _Store:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.status = "running"

    def append_sdk_record(self, *, task_id, topic_id, turn_index, record_type, event_type, data, envelope=None) -> int:
        seq_id = len(self.rows) + 1
        self.rows.append({"seq_id": seq_id, "turn_index": turn_index, "record_type": record_type,
                          "event_type": event_type, "data": data, "created_at": None})
        return seq_id

    def list_sdk_records(self, *, task_id, after_id=0, limit=200):
        return [r for r in self.rows if r["seq_id"] > after_id][:limit]

    def get_task(self, task_id, context=None):
        return {"task_id": task_id, "task_status": self.status}


def _event(seq: int, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"event_id": f"ev-{seq}", "sequence": seq, "type": event_type, "payload": payload or {}}


def _delta(seq: int, text: str) -> dict[str, Any]:
    return _event(seq, "content.delta", {"turn_id": "t", "content_id": "c-0", "kind": "answer", "delta": text})


async def _collect(after_id: int, out: list[dict[str, Any]]) -> None:
    async for chunk in routes._stream_sdk_events(task_id=TASK, after_id=after_id):
        if chunk.startswith("data: "):
            out.append(json.loads(chunk[len("data: "):]))


def _summary(records: list[dict[str, Any]]) -> list[tuple]:
    out = []
    for r in records:
        data = r.get("data") or {}
        if r.get("event_type") == "content.delta":
            out.append(("text", data["text"]) if "text" in data else ("delta", data["delta"]))
        else:
            out.append((r.get("event_type") or r.get("record_type"), r.get("seq_id")))
    return out


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.fixture
def live(monkeypatch):
    store = _Store()
    monkeypatch.setattr(routes, "_get_store", lambda: store)
    writer = PiEventWriter(store, TASK, "topic-1")
    live_events.open_channel(TASK, writer.open_blocks_snapshot)
    yield store, writer
    live_events.close_channel(TASK)


@pytest.mark.asyncio
async def test_live_stream_orders_snapshot_deltas_rows_and_out_of_writer_rows(live):
    store, writer = live
    writer.ingest(_event(1, "turn.started", {"turn_id": "t"}))
    writer.ingest(_event(2, "content.started", {"turn_id": "t", "content_id": "c-0", "kind": "answer"}))
    writer.ingest(_delta(3, "he"))

    got: list[dict[str, Any]] = []
    consumer = asyncio.create_task(_collect(0, got))
    await _settle()
    assert _summary(got) == [("turn.started", 1), ("content.started", 2), ("text", "he")]

    writer.ingest(_delta(4, "llo"))
    writer.ingest(_event(5, "tool.started", {"turn_id": "t", "tool_call_id": "x"}))
    await _settle()
    assert _summary(got)[3:] == [("delta", "llo"), ("tool.started", 3)]

    # A row written outside the writer (e.g. a card) arrives on the next poll.
    store.append_sdk_record(task_id=TASK, topic_id="topic-1", turn_index=1,
                            record_type="question_request", event_type=None, data={})
    await asyncio.sleep(1.3)
    assert _summary(got)[5:] == [("question_request", 4)]

    writer.ingest(_event(6, "content.completed", {"turn_id": "t", "content_id": "c-0", "kind": "answer", "text": "hello"}))
    writer.ingest(_event(7, "run.completed", {"terminal_status": "success"}))
    live_events.close_channel(TASK)
    store.status = "finished"
    await asyncio.wait_for(consumer, timeout=5)

    assert _summary(got)[6:] == [("content.completed", 5), ("run.completed", 6)]
    assert not any(r["event_type"] == "content.delta" for r in store.rows)


@pytest.mark.asyncio
async def test_reconnect_mid_block_replaces_text_without_duplicates(live):
    store, writer = live
    writer.ingest(_event(1, "turn.started", {"turn_id": "t"}))
    writer.ingest(_event(2, "content.started", {"turn_id": "t", "content_id": "c-0", "kind": "answer"}))
    writer.ingest(_delta(3, "par"))
    writer.ingest(_delta(4, "tial"))

    # The client already saw seq 1-2 and some deltas before disconnecting.
    got: list[dict[str, Any]] = []
    consumer = asyncio.create_task(_collect(2, got))
    await _settle()
    writer.ingest(_delta(5, "!"))
    await _settle()
    assert _summary(got) == [("text", "partial"), ("delta", "!")]

    writer.ingest(_event(6, "run.failed", {"error_code": "X", "message": "boom"}))
    live_events.close_channel(TASK)
    store.status = "error"
    await asyncio.wait_for(consumer, timeout=5)

    # The interrupted block was stored once, before the terminal event, as
    # full text: the client replaces its block with it instead of appending.
    stored = [(r["event_type"] or r["record_type"], r["data"].get("text")) for r in store.rows[2:]]
    assert stored == [("content.delta", "partial!"), ("run.failed", None), ("error", None)]
    assert _summary(got)[2:] == [("text", "partial!"), ("run.failed", 4), ("error", 5)]
