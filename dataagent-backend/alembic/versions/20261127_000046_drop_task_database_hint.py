"""Drop da_agent_task.database_hint: no client sends the `database` hint.

Revision ID: 20261127_000046
Revises: 20261127_000045
"""
import sqlalchemy as sa
from alembic import op

revision = "20261127_000046"
down_revision = "20261127_000045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("da_agent_task", "database_hint")


def downgrade() -> None:
    op.add_column(
        "da_agent_task",
        sa.Column("database_hint", sa.String(255), nullable=True, comment="数据库选择提示"),
    )
