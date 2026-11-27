"""Keep only knowledge entries: drop kind/inject_mode, index (status, updated_at), widen content to MEDIUMTEXT, and persist digest target agent on topic.

Revision ID: 20261127_000047
Revises: 20261127_000046
Create Date: 2026-11-27 00:00:00

Design: docs/design/2026-11-19-experience-library-design.md.
Plan: docs/plans/2026-11-19-experience-library-plan.md (PR 2).

Existing memory rows stay in da_agent_experience as knowledge entries (preserving
status, digest_topic_id, and source_task_ids_json). Every DDL step checks current
schema state first so re-running after a partial MySQL implicit-commit failure is
idempotent.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261127_000047"
down_revision = "20261127_000046"
branch_labels = None
depends_on = None

TABLE = "da_agent_experience"
TOPIC_TABLE = "da_agent_topic"
PROFILE_TABLE = "da_agent_profile"
OLD_INDEX = "idx_da_agent_experience_kind"
NEW_INDEX = "idx_da_agent_experience_status_updated"
DIGEST_AGENT_ID = "agent_experience_digest"
OLD_DIGEST_DESCRIPTION = "内置：每天读取前一天的会话记录，提炼候选经验，供管理员在记忆页面审核。"
NEW_DIGEST_DESCRIPTION = "内置：每天读取前一天的会话记录，提炼候选知识，供管理员在知识页面审核。"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table(TABLE):
        existing_indexes = {idx["name"] for idx in inspector.get_indexes(TABLE)}
        if OLD_INDEX in existing_indexes:
            op.drop_index(OLD_INDEX, table_name=TABLE)

        existing_columns = {col["name"] for col in inspector.get_columns(TABLE)}
        for col_name in ("kind", "inject_mode"):
            if col_name in existing_columns:
                op.drop_column(TABLE, col_name)

        inspector = inspect(bind)
        existing_indexes = {idx["name"] for idx in inspector.get_indexes(TABLE)}
        if NEW_INDEX not in existing_indexes:
            op.create_index(NEW_INDEX, TABLE, ["status", "updated_at"])

        if "content" in existing_columns:
            row = bind.exec_driver_sql(
                """
                SELECT DATA_TYPE, CHARACTER_SET_NAME, COLLATION_NAME, IS_NULLABLE, COLUMN_COMMENT
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = %s
                  AND COLUMN_NAME = 'content'
                """,
                (TABLE,),
            ).fetchone()
            if row and str(row[0] or "").lower() != "mediumtext":
                charset = str(row[1] or "").strip()
                collation = str(row[2] or "").strip()
                is_nullable = str(row[3] or "").upper() == "YES"
                comment = str(row[4] or "")
                parts = [f"ALTER TABLE `{TABLE}` MODIFY COLUMN `content` MEDIUMTEXT"]
                if charset:
                    parts.append(f"CHARACTER SET {charset}")
                if collation:
                    parts.append(f"COLLATE {collation}")
                parts.append("NULL" if is_nullable else "NOT NULL")
                if comment:
                    escaped = comment.replace("\\", "\\\\").replace("'", "\\'")
                    parts.append(f"COMMENT '{escaped}'")
                bind.exec_driver_sql(" ".join(parts))

    inspector = inspect(bind)
    if inspector.has_table(TOPIC_TABLE):
        topic_columns = {col["name"] for col in inspector.get_columns(TOPIC_TABLE)}
        if "digest_target_agent_id" not in topic_columns:
            op.add_column(
                TOPIC_TABLE,
                sa.Column(
                    "digest_target_agent_id",
                    sa.String(64),
                    nullable=True,
                    comment="知识总结会话对应的来源助手",
                ),
            )

    inspector = inspect(bind)
    if inspector.has_table(PROFILE_TABLE):
        profile_columns = {col["name"] for col in inspector.get_columns(PROFILE_TABLE)}
        if {"agent_id", "description"} <= profile_columns:
            bind.execute(
                sa.text(
                    f"""
                    UPDATE `{PROFILE_TABLE}`
                    SET `description` = :new_desc
                    WHERE `agent_id` = :agent_id AND `description` = :old_desc
                    """
                ),
                {
                    "new_desc": NEW_DIGEST_DESCRIPTION,
                    "agent_id": DIGEST_AGENT_ID,
                    "old_desc": OLD_DIGEST_DESCRIPTION,
                },
            )


def downgrade() -> None:
    raise RuntimeError(
        "Restore the pre-upgrade backup (scripts/restore-db.sh) with the matching application version"
    )
