"""Trusted execution identity; never sourced from model tool arguments.

Internal code passes integer ``user_id``; HTTP header encoding to text happens
strictly at the boundary here.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_identity: ContextVar[dict[str, str]] = ContextVar("agent_identity", default={})


def identity_headers(user_id: int | None, agent_id: str) -> dict[str, str]:
    if not agent_id:
        raise ValueError("缺少智能体身份")
    if user_id is None or isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("数据库访问需要登录用户")
    return {"X-Agent-User-Id": str(user_id), "X-Agent-Id": agent_id}


def current_headers() -> dict[str, str]:
    return dict(_identity.get())


def set_execution_identity(params: Any):
    agent_id = str((getattr(params, "agent_snapshot", None) or {}).get("agent_id") or "")
    raw_uid = getattr(params, "user_id", None)
    header_uid = str(raw_uid) if isinstance(raw_uid, int) and not isinstance(raw_uid, bool) and raw_uid > 0 else ""
    return _identity.set({"X-Agent-User-Id": header_uid, "X-Agent-Id": agent_id})
