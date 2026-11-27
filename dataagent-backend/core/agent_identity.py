"""Trusted execution identity; never sourced from model tool arguments.

Internal code passes integer ``user_id``; HTTP header encoding to text happens
strictly at the boundary here.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_identity: ContextVar[dict[str, str]] = ContextVar("agent_identity", default={})


def current_headers() -> dict[str, str]:
    return dict(_identity.get())


def set_execution_identity(params: Any):
    agent_id = str((getattr(params, "agent_snapshot", None) or {}).get("agent_id") or "")
    raw_uid = getattr(params, "user_id", None)
    header_uid = str(raw_uid) if isinstance(raw_uid, int) and not isinstance(raw_uid, bool) and raw_uid > 0 else ""
    return _identity.set({"X-Agent-User-Id": header_uid, "X-Agent-Id": agent_id})
