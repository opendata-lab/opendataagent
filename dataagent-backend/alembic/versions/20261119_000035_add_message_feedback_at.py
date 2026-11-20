"""record when an answer was last rated

The admin 用户反馈 menu lists ratings by time, and the daily experience digest
picks up ratings given on a day even when the answer is older.

Revision ID: 20261119_000035
Revises: 20261113_000034
Create Date: 2026-11-19 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "20261119_000035"
down_revision = "20261113_000034"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = inspect(op.get_bind())
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_column("da_agent_message", "feedback_at"):
        op.add_column(
            "da_agent_message",
            sa.Column("feedback_at", sa.DateTime(), nullable=True, comment="最近一次评价变更时间"),
        )
        # Existing ratings keep their last update time as the best available guess.
        op.execute("UPDATE da_agent_message SET feedback_at = updated_at WHERE feedback <> ''")
        op.create_index("idx_da_agent_message_feedback_at", "da_agent_message", ["feedback_at"])


def downgrade() -> None:
    if _has_column("da_agent_message", "feedback_at"):
        op.drop_index("idx_da_agent_message_feedback_at", table_name="da_agent_message")
        op.drop_column("da_agent_message", "feedback_at")
