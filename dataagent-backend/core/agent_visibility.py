"""Agent visibility mode normalization.

``da_agent_profile.visibility_json`` stores only ``{"mode": "all|selected"}``.
Every caller is signed in, so "all" means every signed-in user.
When ``mode == "selected"``, access grants live solely in ``da_agent_grant``
(managed through ``core.accounts``). Legacy ``allowed_users`` and
``allowed_groups`` fields are stripped and no longer persisted.
"""
from __future__ import annotations

import json
from typing import Any

VISIBILITY_MODE_ALL = "all"
VISIBILITY_MODE_SELECTED = "selected"
VISIBILITY_MODES = (
    VISIBILITY_MODE_ALL,
    VISIBILITY_MODE_SELECTED,
)


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


def _safe_json_load(value: Any) -> Any:
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except Exception:
            return {}
    return value
