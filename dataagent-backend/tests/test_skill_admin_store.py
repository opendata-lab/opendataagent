from __future__ import annotations

import sys
import types
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.skill_admin_store import SkillAdminStore


LEGACY_ROW_COLUMNS = {
    "anthropic_api_key": "sk-old",
    "mysql_host": "old-mysql",
    "mysql_port": 3316,
    "mysql_database": "dataagent",
}


def test_normalize_settings_payload_stores_only_owned_settings():
    store = SkillAdminStore()
    normalized = store._normalize_settings_payload(
        {
            "provider_id": "openrouter",
            "model": "anthropic/claude-sonnet-4.5",
            "skills_output_dir": "../.claude/skills/example",
            "skill_runtime": {"example": {"enabled": True}},
            "widget_allowed_sites": [{"website_id": "site-a"}],
            "provider_settings": {"openrouter": {"api_key": "sk-copy"}},
            "anthropic_api_key": "sk-copy",
            "mysql_host": "old-mysql",
            "session_mysql_database": "dataagent",
            "validated_model": "anthropic/claude-sonnet-4.5",
        }
    )

    assert set(normalized) == {
        "provider_id",
        "model",
        "skills_output_dir",
        "skill_runtime",
        "widget_allowed_sites",
    }
    assert normalized["widget_allowed_sites"] == [{"website_id": "site-a"}]


def test_normalize_settings_payload_keeps_blank_provider_and_model():
    store = SkillAdminStore()
    normalized = store._normalize_settings_payload(
        {
            "provider_id": "",
            "model": "",
        }
    )

    assert normalized["provider_id"] == ""
    assert normalized["model"] == ""


def test_normalize_settings_row_ignores_legacy_connection_and_provider_copies():
    store = SkillAdminStore()
    normalized = store._normalize_settings_row(
        {
            "provider_id": "openrouter",
            "model_name": "anthropic/claude-sonnet-4.5",
            **LEGACY_ROW_COLUMNS,
            "skills_output_dir": "../.claude/skills/example-business-knowledge",
            "updated_at": None,
            "raw_json": (
                '{"provider_settings":{"openrouter":{"provider_id":"openrouter"}},'
                '"mysql_host":"old-mysql","widget_allowed_sites":[{"website_id":"site-a"}]}'
            ),
        }
    )

    assert "provider_settings" not in normalized
    assert "mysql_host" not in normalized
    assert "anthropic_api_key" not in normalized
    assert normalized["widget_allowed_sites"] == [{"website_id": "site-a"}]


def test_normalize_settings_row_keeps_blank_provider_and_model():
    store = SkillAdminStore()
    normalized = store._normalize_settings_row(
        {
            "provider_id": "",
            "model_name": "",
            "skills_output_dir": "../.claude/skills/example-business-knowledge",
            "updated_at": None,
            "raw_json": None,
        }
    )

    assert normalized["provider_id"] == ""
    assert normalized["model"] == ""
