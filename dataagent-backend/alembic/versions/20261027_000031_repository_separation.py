"""Make legacy platform records administrator-owned and rename the web source."""
from alembic import op
import sqlalchemy as sa

revision = "20261027_000031"
down_revision = "20261027_000030"
branch_labels = None
depends_on = None


def _records(source: str, builtin: int) -> None:
    op.execute(sa.text("UPDATE da_mcp_server SET source = :source WHERE server_id = 'portal'").bindparams(source=source))
    op.execute(sa.text("UPDATE da_agent_profile SET is_builtin = :builtin "
                       "WHERE agent_id IN ('agent_opendataworks', 'agent_ontology_modeling')").bindparams(builtin=builtin))
    for column in ("draft_json", "published_json"):
        op.execute(sa.text(f"UPDATE da_agent_draft SET {column} = JSON_SET({column}, '$.is_builtin', "
                           f"CAST(:builtin AS JSON)) WHERE agent_id IN ('agent_opendataworks', 'agent_ontology_modeling') "
                           f"AND {column} IS NOT NULL AND JSON_VALID({column})").bindparams(builtin="true" if builtin else "false"))


def upgrade() -> None:
    _records("configured", 0)
    for table in ("da_agent_topic", "da_agent_widget_event"):
        op.execute(f"UPDATE {table} SET source = 'web' WHERE source = 'portal'")
        op.alter_column(table, "source", existing_type=sa.String(32), existing_nullable=False, server_default="web")


def downgrade() -> None:
    for table in ("da_agent_topic", "da_agent_widget_event"):
        op.execute(f"UPDATE {table} SET source = 'portal' WHERE source = 'web'")
        op.alter_column(table, "source", existing_type=sa.String(32), existing_nullable=False, server_default="portal")
    _records("plugin", 1)
