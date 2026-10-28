"""Remove agent data scopes; preserve existing source-prefixed login identity."""
from alembic import op

revision = "20261027_000030"
down_revision = "20261026_000029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("da_agent_profile", "data_scope_json")
    for table, columns in {
        "da_agent_draft": ["draft_json", "published_json"],
        "da_agent_topic": ["agent_snapshot_json"],
        "da_agent_task": ["agent_snapshot_json"],
    }.items():
        for column in columns:
            # updated_at = updated_at keeps MySQL from stamping every row with the migration time.
            op.execute(f"UPDATE {table} SET {column} = JSON_REMOVE({column}, '$.data_scope'), updated_at = updated_at "
                       f"WHERE {column} IS NOT NULL AND JSON_VALID({column})")


def downgrade() -> None:
    raise RuntimeError("Restore the pre-upgrade backup with the matching application version")
