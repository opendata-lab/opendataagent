"""Drop dead table da_agent_chunk and dead columns across task/topic/message/provider/mcp tables.

Revision ID: 20261127_000048
Revises: 20261127_000047
Create Date: 2026-11-27 00:00:00

Every DDL step checks current schema state first so re-running after a partial
MySQL implicit-commit failure is idempotent. Keeps da_agent_settings.skills_output_dir
(legacy skill path/runtime fallback), da_agent_message.show_in_ui (preserves any
historical hidden lifecycle rows), and da_agent_widget_event (behaviour telemetry).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "20261127_000048"
down_revision = "20261127_000047"
branch_labels = None
depends_on = None

# Indexes that exist solely to index columns being dropped below.
_DROP_INDEXES: tuple[tuple[str, str], ...] = (
    ("da_agent_task", "idx_da_agent_task_parent"),
    ("da_agent_topic", "uk_da_agent_topic_chat_topic"),
)

_DROP_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("da_agent_task", ("debug_enabled", "last_event_seq", "from_task_id")),
    ("da_model_provider", ("supports_partial_messages", "provider_type")),
    ("da_mcp_server", ("oauth_required",)),
    ("da_agent_profile", ("data_scope_json",)),
    (
        "da_agent_message",
        (
            "event",
            "steps_json",
            "tool_json",
            "correlation_id",
            "parent_correlation_id",
            "content_type",
        ),
    ),
    ("da_agent_topic", ("chat_topic_id", "chat_conversation_id")),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("da_agent_chunk"):
        op.drop_table("da_agent_chunk")

    inspector = inspect(bind)
    for table, index_name in _DROP_INDEXES:
        if not inspector.has_table(table):
            continue
        existing_indexes = {idx["name"] for idx in inspector.get_indexes(table)}
        existing_uniques = {uc["name"] for uc in inspector.get_unique_constraints(table)}
        if index_name in existing_indexes or index_name in existing_uniques:
            op.drop_index(index_name, table_name=table)

    inspector = inspect(bind)
    for table, columns in _DROP_COLUMNS:
        if not inspector.has_table(table):
            continue
        existing_columns = {col["name"] for col in inspector.get_columns(table)}
        for col_name in columns:
            if col_name in existing_columns:
                op.drop_column(table, col_name)


def downgrade() -> None:
    raise RuntimeError(
        "Restore the pre-upgrade backup (scripts/restore-db.sh) with the matching application version"
    )
