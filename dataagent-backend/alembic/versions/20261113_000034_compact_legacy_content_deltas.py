"""compact legacy per-token content.delta rows of terminal tasks

Revision ID: 20261113_000034
Revises: 20261106_000033
Create Date: 2026-11-13 12:00:00

Design: docs/design/2026-11-13-legacy-content-delta-compaction-design.md.

Before the live stream (#39), a task that ended in error or suspended kept
every streamed content.delta row, and opening its conversation downloads and
replays all of them. Per content block:

- a block with content.completed: its deltas are redundant (completed.text
  replaces the block in every reader), so they are deleted;
- a block cut short: its first delta row is rewritten to the full text as
  ``text`` (the form #39 stores) and the rest are deleted.

Only terminal tasks are touched. Rows without a content_id are left alone,
because readers do not merge them into one block.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from alembic import op
from sqlalchemy import inspect

revision = "20261113_000034"
down_revision = "20261106_000033"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_TERMINAL_STATUSES = ("finished", "error", "suspended")
_DELETE_BATCH_SIZE = 1_000


def _block_key(turn_index: Any, data: dict[str, Any]) -> tuple:
    return (int(turn_index or 0), str(data.get("turn_id") or ""), str(data.get("content_id") or ""), str(data.get("kind") or ""))


def _load(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def compact_task_rows(rows: Iterable[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], list[int]]:
    """Plan the compaction of one task.

    ``rows`` are the task's content.delta and content.completed rows in id
    order, each with ``id``, ``turn_index``, ``event_type`` and ``data``.
    Returns (rows to rewrite: id -> new data, ids to delete).
    """
    completed: set[tuple] = set()
    blocks: dict[tuple, list[tuple[int, dict[str, Any]]]] = {}
    for row in rows:
        data = _load(row.get("data"))
        key = _block_key(row.get("turn_index"), data)
        if row.get("event_type") == "content.completed":
            completed.add(key)
        elif row.get("event_type") == "content.delta" and key[2]:
            blocks.setdefault(key, []).append((int(row["id"]), data))

    updates: dict[int, dict[str, Any]] = {}
    delete_ids: list[int] = []
    for key, deltas in blocks.items():
        if key in completed:
            delete_ids.extend(row_id for row_id, _ in deltas)
            continue
        first_id, first_data = deltas[0]
        if len(deltas) == 1 and "text" in first_data and "delta" not in first_data:
            continue  # already in the compact form
        text = ""
        for _, data in deltas:
            text = str(data.get("text") or "") if "text" in data else text + str(data.get("delta") or "")
        base = {k: v for k, v in first_data.items() if k not in ("delta", "text")}
        updates[first_id] = {**base, "text": text}
        delete_ids.extend(row_id for row_id, _ in deltas[1:])
    return updates, delete_ids


def upgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table("da_agent_sdk_record"):
        return
    placeholders = ", ".join(["%s"] * len(_TERMINAL_STATUSES))
    task_ids = [
        row[0]
        for row in bind.exec_driver_sql(
            "SELECT DISTINCT r.task_id FROM da_agent_sdk_record r "
            "JOIN da_agent_task t ON t.task_id = r.task_id "
            f"WHERE r.event_type = 'content.delta' AND t.task_status IN ({placeholders})",
            _TERMINAL_STATUSES,
        ).fetchall()
    ]
    rewritten = deleted = 0
    # One short transaction per task instead of one migration-wide transaction.
    with op.get_context().autocommit_block():
        for task_id in task_ids:
            rows = bind.exec_driver_sql(
                "SELECT id, turn_index, event_type, data FROM da_agent_sdk_record "
                "WHERE task_id = %s AND event_type IN ('content.delta', 'content.completed') ORDER BY id",
                (task_id,),
            ).mappings().all()
            updates, delete_ids = compact_task_rows(rows)
            for row_id, data in updates.items():
                bind.exec_driver_sql(
                    "UPDATE da_agent_sdk_record SET data = %s WHERE id = %s",
                    (json.dumps(data, ensure_ascii=False), row_id),
                )
            for start in range(0, len(delete_ids), _DELETE_BATCH_SIZE):
                batch = delete_ids[start:start + _DELETE_BATCH_SIZE]
                bind.exec_driver_sql(
                    f"DELETE FROM da_agent_sdk_record WHERE id IN ({', '.join(['%s'] * len(batch))})",
                    tuple(batch),
                )
            rewritten += len(updates)
            deleted += len(delete_ids)
    logger.info(
        "content.delta compaction tasks=%s rewritten=%s deleted=%s",
        len(task_ids),
        rewritten,
        deleted,
    )


def downgrade() -> None:
    # Deleted rows are not restorable and not needed: completed.text or the
    # rewritten full-text row keeps every block's content.
    pass
