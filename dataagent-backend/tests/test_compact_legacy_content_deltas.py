"""Compaction of legacy per-token content.delta rows (migration 20261113_000034)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from core.topic_task_store import _project_sdk_records

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "20261113_000034_compact_legacy_content_deltas.py"
)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("compact_legacy_content_deltas", _MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(row_id, event_type, data, turn_index=1, record_type="agent_event"):
    return {"id": row_id, "turn_index": turn_index, "record_type": record_type, "event_type": event_type,
            "data": json.dumps(data, ensure_ascii=False)}


def _delta(row_id, content_id, delta, kind="answer", turn_id="t1", turn_index=1):
    return _row(row_id, "content.delta", {"turn_id": turn_id, "content_id": content_id, "kind": kind, "delta": delta}, turn_index)


def _apply(rows, updates, delete_ids):
    out = []
    for row in rows:
        if row["id"] in delete_ids:
            continue
        if row["id"] in updates:
            row = {**row, "data": json.dumps(updates[row["id"]], ensure_ascii=False)}
        out.append(row)
    return out


def _projected_text(rows):
    records = [{"seq_id": r["id"], "turn_index": r["turn_index"], "record_type": r["record_type"],
                "event_type": r["event_type"], "data": json.loads(r["data"])} for r in rows]
    projection = _project_sdk_records(records)
    return [(b.get("type"), b.get("text")) for b in projection.get("blocks") or [] if b.get("type") in ("thinking", "main_text")]


def _interrupted_task():
    return [
        _row(1, "turn.started", {"turn_id": "t1"}),
        _row(2, "content.started", {"turn_id": "t1", "content_id": "c-0", "kind": "reasoning"}),
        _delta(3, "c-0", "thin", kind="reasoning"),
        _delta(4, "c-0", "king", kind="reasoning"),
        _row(5, "content.completed", {"turn_id": "t1", "content_id": "c-0", "kind": "reasoning", "text": "thinking"}),
        _row(6, "content.started", {"turn_id": "t1", "content_id": "c-1", "kind": "answer"}),
        _delta(7, "c-1", "par"),
        _delta(8, "c-1", "tial "),
        _delta(9, "c-1", "answer"),
        _row(10, "run.failed", {"error_code": "CELL_LOSS"}),
    ]


def test_completed_block_deltas_are_deleted_and_cut_block_becomes_one_full_text_row():
    rows = _interrupted_task()
    stored = [r for r in rows if r["event_type"] in ("content.delta", "content.completed")]

    updates, delete_ids = _migration().compact_task_rows(stored)

    assert updates == {7: {"turn_id": "t1", "content_id": "c-1", "kind": "answer", "text": "partial answer"}}
    assert sorted(delete_ids) == [3, 4, 8, 9]


def test_projection_is_unchanged_by_compaction():
    rows = _interrupted_task()
    stored = [r for r in rows if r["event_type"] in ("content.delta", "content.completed")]
    updates, delete_ids = _migration().compact_task_rows(stored)

    before = _projected_text(rows)
    after = _projected_text(_apply(rows, updates, set(delete_ids)))

    assert before == after == [("thinking", "thinking"), ("main_text", "partial answer")]


def test_rerun_is_a_no_op():
    migration = _migration()
    rows = _interrupted_task()
    stored = [r for r in rows if r["event_type"] in ("content.delta", "content.completed")]
    updates, delete_ids = migration.compact_task_rows(stored)
    compacted = [r for r in _apply(rows, updates, set(delete_ids)) if r["event_type"] in ("content.delta", "content.completed")]

    assert migration.compact_task_rows(compacted) == ({}, [])


def test_existing_full_text_row_replaces_rather_than_appends():
    rows = [
        _delta(1, "c-0", "ab"),
        _row(2, "content.delta", {"turn_id": "t1", "content_id": "c-0", "kind": "answer", "text": "xyz"}),
        _delta(3, "c-0", "!"),
    ]

    updates, delete_ids = _migration().compact_task_rows(rows)

    assert updates[1]["text"] == "xyz!"
    assert sorted(delete_ids) == [2, 3]


def test_blocks_are_keyed_by_turn_and_kind_and_rows_without_content_id_are_left_alone():
    rows = [
        _delta(1, "c-0", "a", turn_id="t1", turn_index=1),
        _delta(2, "c-0", "b", turn_id="t2", turn_index=2),
        _row(3, "content.completed", {"turn_id": "t2", "content_id": "c-0", "kind": "answer", "text": "b"}, turn_index=2),
        _delta(4, "c-0", "r", kind="reasoning", turn_id="t1", turn_index=1),
        _row(5, "content.delta", {"kind": "answer", "delta": "orphan"}),
        _row(6, "content.delta", {"kind": "answer", "delta": "orphan2"}),
    ]

    updates, delete_ids = _migration().compact_task_rows(rows)

    # t1 answer and t1 reasoning were cut short; t2's block completed.
    assert updates == {
        1: {"turn_id": "t1", "content_id": "c-0", "kind": "answer", "text": "a"},
        4: {"turn_id": "t1", "content_id": "c-0", "kind": "reasoning", "text": "r"},
    }
    assert delete_ids == [2]
