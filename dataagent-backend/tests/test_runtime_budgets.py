"""Default inheritance and the single budget tier, including the sandbox dispatch boundary."""
from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings, resolve_run_limits
from core import task_executor, task_submission_service
from core.agent_profile_service import default_agent_payload
from core.task_executor import TaskExecutionInput, TaskExecutionResult


def test_no_overrides_selects_bounded_defaults():
    cfg = Settings(_env_file=None)
    assert resolve_run_limits(cfg) == dict(max_turns=40, max_tool_calls=200,
                                           total_timeout_seconds=1800, bash_timeout_seconds=600,
                                           idle_timeout_seconds=300)
    assert default_agent_payload()["max_turns"] == 0


def test_global_environment_then_explicit_settings_then_agent_override(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "250")
    monkeypatch.setenv("AGENT_MAX_TURNS", "48")
    cfg = Settings(_env_file=None)
    assert resolve_run_limits(cfg)["max_tool_calls"] == 250
    assert resolve_run_limits(cfg, agent_max_turns=0)["max_turns"] == 48
    assert resolve_run_limits(cfg, agent_max_turns=12)["max_turns"] == 12
    cfg = Settings(_env_file=None, agent_max_tool_calls=220)
    assert resolve_run_limits(cfg)["max_tool_calls"] == 220


@pytest.mark.parametrize("field", [
    "agent_max_turns", "agent_max_tool_calls", "agent_timeout_seconds",
    "agent_bash_timeout_seconds", "agent_sql_read_timeout_seconds",
    "dataagent_run_idle_timeout_seconds", "agent_sql_write_timeout_seconds",
])
@pytest.mark.parametrize("invalid", [0, -1])
def test_global_budgets_reject_unbounded_or_negative_values(field, invalid):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: invalid})


@pytest.mark.parametrize("patch", [
    {"agent_bash_timeout_seconds": 1800},
    {"agent_timeout_seconds": 300, "agent_bash_timeout_seconds": 300},
])
def test_invalid_timeout_chain_is_rejected(patch):
    with pytest.raises(ValidationError, match="agent_bash_timeout_seconds must be less"):
        Settings(_env_file=None, **patch)


def test_retired_tier_settings_are_unknown_fields():
    """Old per-mode names must not silently configure anything."""
    assert "agent_background_timeout_seconds" not in Settings.model_fields
    assert "agent_interactive_max_turns" not in Settings.model_fields


def test_submission_and_execution_share_total_budget_and_preserve_submitted_value(monkeypatch):
    cfg = Settings(_env_file=None, agent_timeout_seconds=2400)
    monkeypatch.setattr(task_submission_service, "get_settings", lambda: cfg)
    assert task_submission_service.resolve_task_timeouts()["timeout_seconds"] == 2400
    assert resolve_run_limits(cfg, total_timeout_seconds=1800)["total_timeout_seconds"] == 1800
