"""experience library entries

One table: candidates produced by the daily experience digest and entries an
admin approved as assistant- or platform-level.

Revision ID: 20261119_000036
Revises: 20261119_000035
Create Date: 2026-11-19 13:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "20261119_000036"
down_revision = "20261119_000035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if inspect(op.get_bind()).has_table("da_agent_experience"):
        return
    op.create_table(
        "da_agent_experience",
        sa.Column("experience_id", sa.String(64), primary_key=True),
        sa.Column("scope", sa.String(16), nullable=False, server_default="assistant", comment="assistant | platform"),
        sa.Column("agent_id", sa.String(64), nullable=True, comment="助手级时的助手"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_task_ids_json", sa.Text(), nullable=True, comment="提炼来源任务"),
        sa.Column("digest_topic_id", sa.String(64), nullable=True, comment="产生该候选的总结会话"),
        sa.Column("status", sa.String(16), nullable=False, server_default="candidate", comment="candidate | active | retired"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        comment="经验库：每日总结的候选与管理员审核后的经验",
    )
    op.create_index("idx_da_agent_experience_status", "da_agent_experience", ["status", "scope", "agent_id"])


def downgrade() -> None:
    if inspect(op.get_bind()).has_table("da_agent_experience"):
        op.drop_table("da_agent_experience")
