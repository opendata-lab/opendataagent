"""Preserve the role granted by the web session when issuing MCP tokens."""
import sqlalchemy as sa
from alembic import op

revision = "20261122_000041"
down_revision = "20261122_000040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Older tokens lack the issuing session's role. Preserve ordinary access.
    op.add_column("da_mcp_access_token", sa.Column("oauth_role", sa.String(8), server_default="user"))


def downgrade() -> None:
    op.drop_column("da_mcp_access_token", "oauth_role")
