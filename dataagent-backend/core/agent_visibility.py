"""Agent visibility mode normalization and access checks.

``da_agent_profile.visibility_json`` stores only ``{"mode": "all|authenticated|selected"}``.
When ``mode == "selected"``, access grants live solely in ``da_agent_grant``
(managed through ``core.accounts``). Legacy ``allowed_users`` and
``allowed_groups`` fields are stripped and no longer persisted.
"""
from __future__ import annotations

import json
from typing import Any

from core.accounts.models import User
from core.accounts.service import can_user_access_agent, filter_accessible_agents

VISIBILITY_MODE_ALL = "all"
VISIBILITY_MODE_AUTHENTICATED = "authenticated"
VISIBILITY_MODE_SELECTED = "selected"
VISIBILITY_MODES = (
    VISIBILITY_MODE_ALL,
    VISIBILITY_MODE_AUTHENTICATED,
    VISIBILITY_MODE_SELECTED,
)


def default_agent_visibility() -> dict[str, str]:
    return {"mode": VISIBILITY_MODE_ALL}


def normalize_agent_visibility(value: Any, *, strict: bool = False) -> dict[str, str]:
    """Normalize visibility config to ``{"mode": ...}`` only."""
    payload = _safe_json_load(value)
    if not isinstance(payload, dict):
        if strict and payload not in (None, ""):
            raise ValueError("visibility must be an object")
        payload = {}

    raw_mode = str(payload.get("mode") or "").strip().lower()
    if not raw_mode:
        mode = VISIBILITY_MODE_ALL
    elif raw_mode in VISIBILITY_MODES:
        mode = raw_mode
    elif strict:
        raise ValueError(f"invalid visibility mode: {raw_mode}")
    else:
        mode = VISIBILITY_MODE_ALL

    return {"mode": mode}


def agent_visible_to(profile: dict[str, Any] | None, user: User | None) -> bool:
    """Delegate assistant visibility check to core.accounts."""
    return can_user_access_agent(user, profile)


def filter_visible_agent_profiles(
    profiles: list[dict[str, Any]], user: User | None
) -> list[dict[str, Any]]:
    return filter_accessible_agents(user, profiles)


def _safe_json_load(value: Any) -> Any:
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except Exception:
            return {}
    return value
