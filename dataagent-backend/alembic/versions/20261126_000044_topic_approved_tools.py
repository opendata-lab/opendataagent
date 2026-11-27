"""Add approved_tools_json to da_agent_topic for session-scoped write-tool approvals."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261126_000044"
down_revision = "20261123_000042"
branch_labels = None
depends_on = None


def _has_column(conn, table: str, column: str) -> bool:
    if not inspect(conn).has_table(table):
        return False
    return any(col["name"] == column for col in inspect(conn).get_columns(table))


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn, "da_agent_topic", "approved_tools_json"):
        op.add_column(
            "da_agent_topic",
            sa.Column(
                "approved_tools_json",
                sa.JSON(),
                nullable=True,
                comment="本会话内免确认的非破坏性 MCP 工具键列表(JSON)",
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _has_column(conn, "da_agent_topic", "approved_tools_json"):
        op.drop_column("da_agent_topic", "approved_tools_json")
