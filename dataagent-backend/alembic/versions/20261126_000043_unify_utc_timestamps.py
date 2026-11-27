"""Unify historical Path-A DATETIME columns to UTC.

Revision ID: 20261126_000043
Revises: 20261123_000042
Create Date: 2026-11-26 12:00:00

Design: docs/design/2026-11-26-timezone-unification-design.md.

Detects the database's global timezone offset relative to UTC. If the database
was already running in UTC (offset == 0), no rows are modified. Otherwise,
subtracts the offset from every Path-A DATETIME column (columns previously
written by MySQL CURRENT_TIMESTAMP / NOW()). Path-B (already UTC), Path-C
(TIMESTAMP), and Path-D (container/mixed) columns are never modified.
"""
from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import inspect

revision = "20261126_000043"
down_revision = "20261123_000042"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_SDK_RECORD_BATCH_SIZE = 5_000

# Class-A tables and their Path-A DATETIME columns (design §5.2).
# Every table below that defines `updated_at ... ON UPDATE CURRENT_TIMESTAMP`
# explicitly assigns `updated_at` in the same UPDATE so MySQL does not stamp
# rows with the migration wall-clock time.
_PATH_A_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("da_agent_chunk", ("created_at",)),
    ("da_agent_draft", ("created_at", "updated_at")),
    ("da_agent_experience", ("created_at", "updated_at")),
    ("da_agent_grant", ("created_at",)),
    ("da_agent_message", ("created_at", "updated_at", "feedback_at")),
    ("da_agent_message_queue", ("consumed_at", "created_at", "updated_at")),
    ("da_agent_message_schedule", ("created_at", "updated_at")),
    ("da_agent_message_schedule_log", ("finished_at", "created_at")),
    ("da_agent_profile", ("created_at", "updated_at")),
    ("da_agent_settings", ("created_at", "updated_at")),
    (
        "da_agent_task",
        ("cancel_requested_at", "started_at", "heartbeat_at", "finished_at", "created_at", "updated_at"),
    ),
    ("da_agent_topic", ("created_at", "updated_at")),
    ("da_agent_widget_event", ("created_at",)),
    ("da_mcp_server", ("created_at", "updated_at")),
    ("da_model_provider", ("created_at", "updated_at")),
    ("da_role", ("created_at", "updated_at")),
    ("da_skill_document", ("created_at", "updated_at")),
    ("da_skill_document_version", ("created_at",)),
    ("da_system_prompt", ("created_at",)),
    ("da_user", ("created_at", "updated_at")),
    ("da_user_identity", ("created_at",)),
    ("da_user_role", ("created_at",)),
)


def _detect_legacy_offset_seconds(bind) -> int:
    """Measure @@global.time_zone minus UTC in seconds, then restore session UTC."""
    bind.exec_driver_sql("SET SESSION time_zone = @@global.time_zone")
    try:
        raw = bind.exec_driver_sql(
            "SELECT TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())"
        ).scalar()
    finally:
        bind.exec_driver_sql("SET SESSION time_zone = '+00:00'")
    return int(raw or 0)


def upgrade() -> None:
    bind = op.get_bind()
    offset_seconds = _detect_legacy_offset_seconds(bind)
    if offset_seconds == 0:
        logger.info("timezone migration 20261126_000043: offset_seconds=0; no rows modified")
        return

    logger.info("timezone migration 20261126_000043: shifting Path-A DATETIME columns by -%s seconds", offset_seconds)
    inspector = inspect(bind)

    for table, columns in _PATH_A_TABLES:
        if not inspector.has_table(table):
            continue
        existing = {col["name"] for col in inspector.get_columns(table)}
        target_cols = [col for col in columns if col in existing]
        if not target_cols:
            continue
        assignments = [
            f"`{col}` = IF(`{col}` IS NULL, NULL, DATE_SUB(`{col}`, INTERVAL %s SECOND))"
            for col in target_cols
        ]
        # Defensive guard: if a table has updated_at with ON UPDATE CURRENT_TIMESTAMP
        # that isn't in target_cols, preserve it unchanged.
        if "updated_at" in existing and "updated_at" not in target_cols:
            assignments.append("`updated_at` = `updated_at`")
        sql = f"UPDATE `{table}` SET {', '.join(assignments)}"
        bind.exec_driver_sql(sql, tuple([offset_seconds] * len(target_cols)))
    bind.commit()

    if inspector.has_table("da_agent_sdk_record"):
        bounds = bind.exec_driver_sql(
            "SELECT MIN(id), MAX(id) FROM `da_agent_sdk_record`"
        ).fetchone()
        min_id = bounds[0] if bounds else None
        max_id = bounds[1] if bounds else None
        if min_id is not None and max_id is not None:
            min_id = int(min_id)
            max_id = int(max_id)
            batch_start = min_id
            while batch_start <= max_id:
                batch_end = batch_start + _SDK_RECORD_BATCH_SIZE - 1
                bind.exec_driver_sql(
                    "UPDATE `da_agent_sdk_record` "
                    "SET `created_at` = IF(`created_at` IS NULL, NULL, DATE_SUB(`created_at`, INTERVAL %s SECOND)) "
                    "WHERE `id` BETWEEN %s AND %s",
                    (offset_seconds, batch_start, batch_end),
                )
                bind.commit()
                batch_start = batch_end + 1


def downgrade() -> None:
    raise RuntimeError("Restore the pre-upgrade backup (scripts/restore-db.sh) with the matching application version")
