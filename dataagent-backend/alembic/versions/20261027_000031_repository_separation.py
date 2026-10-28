"""Make legacy platform records administrator-owned and rename the web source."""
from alembic import op
import sqlalchemy as sa

revision = "20261027_000031"
down_revision = "20261027_000030"
branch_labels = None
depends_on = None


def _records(source: str, builtin: int) -> None:
    op.execute(sa.text("UPDATE da_mcp_server SET source = :source, updated_at = updated_at WHERE server_id = 'portal'").bindparams(source=source))
    op.execute(sa.text("UPDATE da_agent_profile SET is_builtin = :builtin "
                       "WHERE agent_id IN ('agent_opendataworks', 'agent_ontology_modeling')").bindparams(builtin=builtin))
    for column in ("draft_json", "published_json"):
        op.execute(sa.text(f"UPDATE da_agent_draft SET {column} = JSON_SET({column}, '$.is_builtin', "
                           f"CAST(:builtin AS JSON)), updated_at = updated_at WHERE agent_id IN ('agent_opendataworks', 'agent_ontology_modeling') "
                           f"AND {column} IS NOT NULL AND JSON_VALID({column})").bindparams(builtin="true" if builtin else "false"))


# updated_at = updated_at keeps MySQL from stamping every row with the migration time;
# da_agent_widget_event has no auto-updated column.
_KEEP_UPDATED_AT = {"da_agent_topic": ", updated_at = updated_at", "da_agent_widget_event": ""}


def upgrade() -> None:
    _records("configured", 0)
    for table, keep in _KEEP_UPDATED_AT.items():
        op.execute(f"UPDATE {table} SET source = 'web'{keep} WHERE source = 'portal'")
        op.alter_column(table, "source", existing_type=sa.String(32), existing_nullable=False, server_default="web")


def downgrade() -> None:
    for table, keep in _KEEP_UPDATED_AT.items():
        op.execute(f"UPDATE {table} SET source = 'portal'{keep} WHERE source = 'web'")
        op.alter_column(table, "source", existing_type=sa.String(32), existing_nullable=False, server_default="portal")
    _records("plugin", 1)
