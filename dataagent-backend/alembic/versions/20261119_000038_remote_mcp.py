"""Publish assistants over MCP: the switch, non-interactive tasks and request keys.

An assistant can be published over MCP (an attribute like its visibility). A
task submitted there is an ordinary task that cannot ask the user anything, and
a client may retry the same request.

Revision ID: 20261119_000038
Revises: 20261119_000037
"""
import sqlalchemy as sa
from alembic import op

revision = "20261119_000038"
down_revision = "20261119_000037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("da_agent_profile", sa.Column("remote_mcp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("da_agent_task", sa.Column("interactive", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("da_agent_task", sa.Column("request_key", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_da_agent_task_request_key", "da_agent_task", ["request_key"])


def downgrade() -> None:
    op.drop_constraint("uq_da_agent_task_request_key", "da_agent_task", type_="unique")
    op.drop_column("da_agent_task", "request_key")
    op.drop_column("da_agent_task", "interactive")
    op.drop_column("da_agent_profile", "remote_mcp_enabled")
