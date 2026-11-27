"""Normalize persisted permission decisions."""

from __future__ import annotations

from typing import Any


def normalize_permission_decision(decision: Any) -> str:
    """Coerce a permission decision to its canonical persisted form.

    ``allow``/``deny``/``timeout`` stay as-is; anything unknown collapses to
    ``deny`` (fail-closed)."""
    value = str(decision or "").strip().lower()
    return value if value in {"allow", "deny", "timeout"} else "deny"
