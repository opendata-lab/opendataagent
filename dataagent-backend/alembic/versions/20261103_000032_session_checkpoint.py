"""Persist Pi sessions at completed segment boundaries."""
from alembic import op

revision = "20261103_000032"
down_revision = "20261027_000031"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS da_agent_session_checkpoint (
            topic_id VARCHAR(64) NOT NULL PRIMARY KEY,
            task_id VARCHAR(64) NOT NULL,
            header_json LONGTEXT NOT NULL,
            entries_json LONGTEXT NOT NULL,
            leaf_id VARCHAR(64) NULL,
            version BIGINT NOT NULL DEFAULT 1,
            state_json LONGTEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT fk_session_checkpoint_topic FOREIGN KEY (topic_id)
                REFERENCES da_agent_topic(topic_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)

    # A rollback stamps revision 31 while retaining the table. On re-upgrade,
    # text history may include turns from the old engine; do not reuse stale SDK state.
    op.execute("DELETE FROM da_agent_session_checkpoint")


def downgrade():
    op.drop_table("da_agent_session_checkpoint")
