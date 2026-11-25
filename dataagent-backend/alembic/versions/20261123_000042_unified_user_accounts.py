"""Unified user accounts, external identities, roles, and agent grants."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261123_000042"
down_revision = "20261122_000041"
branch_labels = None
depends_on = None


def _has_table(conn, name: str) -> bool:
    return inspect(conn).has_table(name)


def _has_column(conn, table: str, column: str) -> bool:
    if not _has_table(conn, table):
        return False
    return any(col["name"] == column for col in inspect(conn).get_columns(table))


def _has_fk(conn, table: str, fk_name: str) -> bool:
    if not _has_table(conn, table):
        return False
    return any(fk.get("name") == fk_name for fk in inspect(conn).get_foreign_keys(table))


def _has_index(conn, table: str, index_name: str) -> bool:
    if not _has_table(conn, table):
        return False
    return any(idx.get("name") == index_name for idx in inspect(conn).get_indexes(table))


def _add_nullable_user_fk(conn, table: str, fk_name: str, index_name: str) -> None:
    if not _has_table(conn, table):
        return
    if not _has_column(conn, table, "user_id"):
        op.add_column(table, sa.Column("user_id", sa.BigInteger(), nullable=True))
    if not _has_index(conn, table, index_name):
        op.create_index(index_name, table, ["user_id"])
    if not _has_fk(conn, table, fk_name):
        op.create_foreign_key(fk_name, table, "da_user", ["user_id"], ["id"])


def upgrade() -> None:
    conn = op.get_bind()

    if not _has_table(conn, "da_user"):
        op.create_table(
            "da_user",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("username", sa.String(128), nullable=False, unique=True),
            sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("email", sa.String(255), nullable=True),
            sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("password_hash", sa.String(255), nullable=False, server_default=""),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            ),
            sa.Column("last_login_at", sa.DateTime(), nullable=True),
            mysql_charset="utf8mb4",
            mysql_collate="utf8mb4_unicode_ci",
            mysql_engine="InnoDB",
        )

    if not _has_table(conn, "da_user_identity"):
        op.create_table(
            "da_user_identity",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("source", sa.String(128, collation="utf8mb4_bin"), nullable=False),
            sa.Column("subject", sa.String(255, collation="utf8mb4_bin"), nullable=False),
            sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("da_user.id", name="fk_da_user_identity_user"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("source", "subject", name="uq_da_user_identity_source_subject"),
            mysql_charset="utf8mb4",
            mysql_collate="utf8mb4_unicode_ci",
            mysql_engine="InnoDB",
        )
        op.create_index("ix_da_user_identity_user_id", "da_user_identity", ["user_id"])

    if not _has_table(conn, "da_role"):
        op.create_table(
            "da_role",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("role_key", sa.String(64), nullable=False, unique=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("description", sa.String(500), nullable=False, server_default=""),
            sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            ),
            mysql_charset="utf8mb4",
            mysql_collate="utf8mb4_unicode_ci",
            mysql_engine="InnoDB",
        )

    if not _has_table(conn, "da_user_role"):
        op.create_table(
            "da_user_role",
            sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("da_user.id", name="fk_da_user_role_user"), nullable=False),
            sa.Column("role_id", sa.BigInteger(), sa.ForeignKey("da_role.id", name="fk_da_user_role_role"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.PrimaryKeyConstraint("user_id", "role_id", name="pk_da_user_role"),
            mysql_charset="utf8mb4",
            mysql_collate="utf8mb4_unicode_ci",
            mysql_engine="InnoDB",
        )
        op.create_index("ix_da_user_role_role_id", "da_user_role", ["role_id"])

    if not _has_table(conn, "da_agent_grant"):
        op.create_table(
            "da_agent_grant",
            sa.Column("agent_id", sa.String(64), nullable=False),
            sa.Column("principal_type", sa.String(16), nullable=False),
            sa.Column("principal_id", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.PrimaryKeyConstraint("agent_id", "principal_type", "principal_id", name="pk_da_agent_grant"),
            mysql_charset="utf8mb4",
            mysql_collate="utf8mb4_unicode_ci",
            mysql_engine="InnoDB",
        )
        op.create_index("ix_da_agent_grant_principal", "da_agent_grant", ["principal_type", "principal_id"])

    # Recreate da_mcp_access_token on integer user_id FK, removing legacy string rows & snapshots.
    if _has_table(conn, "da_mcp_access_token"):
        op.drop_table("da_mcp_access_token")
    op.create_table(
        "da_mcp_access_token",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.CHAR(64), nullable=False, unique=True),
        sa.Column("token_prefix", sa.String(10), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("da_user.id", name="fk_da_mcp_access_token_user"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
        mysql_engine="InnoDB",
    )
    op.create_index("ix_da_mcp_access_token_user_id", "da_mcp_access_token", ["user_id"])

    # Add integer user_id FKs to inventory tables.
    _add_nullable_user_fk(conn, "da_agent_topic", "fk_da_agent_topic_user", "ix_da_agent_topic_user_id")
    _add_nullable_user_fk(conn, "da_agent_experience", "fk_da_agent_experience_user", "ix_da_agent_experience_user_id")
    _add_nullable_user_fk(conn, "da_agent_widget_event", "fk_da_agent_widget_event_user", "ix_da_agent_widget_event_user_id")
    _add_nullable_user_fk(conn, "eval_dataset", "fk_eval_dataset_user", "ix_eval_dataset_user_id")

    # Seed built-in roles, import cutover admins, backfill topics, migrate published agent grants.
    from core.accounts.migration import run_accounts_data_migration

    run_accounts_data_migration(conn)


def downgrade() -> None:
    # One-way cutover (user decision 2026-11-24): legacy allowlists and MCP
    # tokens are rewritten, so going back means restoring the backup taken
    # before the upgrade with scripts/restore-db.sh.
    raise RuntimeError("20261123_000042 不支持降级，请用 scripts/restore-db.sh 恢复升级前的备份")
