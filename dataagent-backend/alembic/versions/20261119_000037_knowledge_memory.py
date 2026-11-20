"""Separate knowledge and memory, preserving existing experience rows.

Revision ID: 20261119_000037
Revises: 20261119_000036
"""
from alembic import op
import sqlalchemy as sa

revision = "20261119_000037"
down_revision = "20261119_000036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("da_agent_experience", sa.Column("kind", sa.String(16), nullable=False, server_default="memory"))
    op.add_column("da_agent_experience", sa.Column("title", sa.String(40), nullable=False, server_default=""))
    op.add_column("da_agent_experience", sa.Column("when_text", sa.String(120), nullable=False, server_default=""))
    op.add_column("da_agent_experience", sa.Column("inject_mode", sa.String(16), nullable=False, server_default="on_demand"))
    op.execute("""UPDATE da_agent_experience SET title = LEFT(content, 40),
        scope = CASE scope WHEN 'platform' THEN 'global' ELSE 'agent' END,
        agent_id = CASE WHEN scope IN ('platform', 'global') THEN NULL ELSE agent_id END,
        status = CASE status WHEN 'candidate' THEN 'pending' WHEN 'retired' THEN 'disabled' ELSE status END""")
    op.alter_column("da_agent_experience", "scope", existing_type=sa.String(16), server_default="agent")
    op.alter_column("da_agent_experience", "status", existing_type=sa.String(16), server_default="pending")
    op.create_index("idx_da_agent_experience_kind", "da_agent_experience", ["kind", "status", "updated_at"])


def downgrade() -> None:
    op.execute("""UPDATE da_agent_experience SET
        scope = CASE scope WHEN 'global' THEN 'platform' ELSE 'assistant' END,
        status = CASE status WHEN 'pending' THEN 'candidate' WHEN 'disabled' THEN 'retired' ELSE status END""")
    op.alter_column("da_agent_experience", "scope", existing_type=sa.String(16), server_default="assistant")
    op.alter_column("da_agent_experience", "status", existing_type=sa.String(16), server_default="candidate")
    op.drop_index("idx_da_agent_experience_kind", table_name="da_agent_experience")
    for name in ("inject_mode", "when_text", "title", "kind"):
        op.drop_column("da_agent_experience", name)
