"""Keep only settings that have no other source in da_agent_settings.

Database connections come from deployment config, and provider definitions
moved to da_model_provider in 20260911_000025. The stored copies were never
updated after that and, at startup, an old copy of the connection overrode the
deployment's MySQL settings.
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "20261106_000033"
down_revision = "20261103_000032"
branch_labels = None
depends_on = None

DROPPED_COLUMNS = (
    "anthropic_api_key",
    "anthropic_auth_token",
    "anthropic_base_url",
    "mysql_host",
    "mysql_port",
    "mysql_user",
    "mysql_password",
    "mysql_database",
    "doris_host",
    "doris_port",
    "doris_user",
    "doris_password",
    "doris_database",
)
KEPT_RAW_KEYS = ("skill_runtime", "widget_allowed_sites")


def _existing_columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table("da_agent_settings"):
        return set()
    return {column["name"] for column in inspector.get_columns("da_agent_settings")}


def upgrade():
    bind = op.get_bind()
    columns = _existing_columns(bind)
    if not columns:
        return
    rows = bind.execute(sa.text("SELECT settings_key, raw_json FROM da_agent_settings")).mappings().all()
    for row in rows:
        try:
            payload = json.loads(row["raw_json"] or "{}")
        except ValueError:
            payload = {}
        kept = {key: payload[key] for key in KEPT_RAW_KEYS if isinstance(payload, dict) and key in payload}
        bind.execute(
            sa.text("UPDATE da_agent_settings SET raw_json = :raw_json WHERE settings_key = :settings_key"),
            {"raw_json": json.dumps(kept, ensure_ascii=False), "settings_key": row["settings_key"]},
        )
    for column in DROPPED_COLUMNS:
        if column in columns:
            op.drop_column("da_agent_settings", column)


def downgrade():
    columns = _existing_columns(op.get_bind())
    if not columns:
        return
    for column in DROPPED_COLUMNS:
        if column in columns:
            continue
        column_type = sa.Integer() if column.endswith("_port") else sa.String(512 if column.startswith("anthropic_") else 255)
        op.add_column("da_agent_settings", sa.Column(column, column_type, nullable=True))
