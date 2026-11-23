"""Store rotating MCP refresh token hashes."""
import sqlalchemy as sa
from alembic import op

revision = "20261120_000039"
down_revision = "20261119_000038"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_table("da_mcp_refresh_token")
