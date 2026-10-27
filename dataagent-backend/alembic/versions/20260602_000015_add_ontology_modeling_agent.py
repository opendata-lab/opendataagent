"""add ontology modeling builtin agent

Revision ID: 20260602_000015
Revises: 20260601_000014
Create Date: 2026-06-02 10:00:00
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "20260602_000015"
down_revision = "20260601_000014"
branch_labels = None
depends_on = None


ONTOLOGY_MODELING_AGENT_ID = "agent_ontology_modeling"

ONTOLOGY_MODELING_AGENT_SNAPSHOT = {
    "agent_id": ONTOLOGY_MODELING_AGENT_ID,
    "name": "本体建模助手",
    "description": "根据业务需求、上传文档和数据库表字段创建特定业务域本体语义 Skill。",
    "system_prompt": "你是 OpenDataWorks 本体建模助手，专注把用户需求、上传文档和数据库表字段整理成可复用的领域本体语义 Skill。",
    "permission_mode": "inherit",
    "allowed_tools": ["Skill", "Bash", "Read", "LS", "Glob", "Grep"],
    "mcp_server_ids": ["portal"],
    "skill_folders": ["ontology-modeling-assistant"],
    "max_turns": 0,
    "env_vars": {},
    "data_scope": {"allowed_scopes": []},
    "preset_questions": [
        "帮我根据上传文档和候选表创建一个业务域本体 Skill",
        "把这些业务术语、表字段和指标口径整理成本体 JSON",
        "检查这个领域本体的对象、关系和 semantic_edges 是否完整",
    ],
    "is_default": False,
    "is_builtin": True,
}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _has_table(table_name: str) -> bool:
    return inspect(op.get_bind()).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = inspect(op.get_bind())
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))




def downgrade() -> None:
    if not _has_table("da_agent_profile"):
        return
    op.get_bind().execute(
        sa.text("DELETE FROM da_agent_profile WHERE agent_id = :agent_id"),
        {"agent_id": ONTOLOGY_MODELING_AGENT_ID},
    )


def upgrade() -> None:
    # Platform agents are administrator-owned after repository separation.
    pass
