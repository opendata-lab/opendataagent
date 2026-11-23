"""Replace MCP refresh tokens with personal access tokens."""
import sqlalchemy as sa
from alembic import op

revision = "20261122_000040"
down_revision = "20261120_000039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "da_mcp_access_token",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("token_hash", sa.CHAR(64), nullable=False, unique=True),
        sa.Column("token_prefix", sa.String(10), nullable=False),
        sa.Column("user_id", sa.String(255, collation="utf8mb4_bin"), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index("ix_da_mcp_access_token_user_id", "da_mcp_access_token", ["user_id"])
    op.drop_table("da_mcp_refresh_token")


def downgrade() -> None:
    op.create_table(
        "da_mcp_refresh_token",
        sa.Column("token_hash", sa.CHAR(64), primary_key=True),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.drop_table("da_mcp_access_token")
