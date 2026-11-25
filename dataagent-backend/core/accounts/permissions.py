"""Built-in roles.

Two levels only (user decision 2026-11-24): administrators get every menu and
management operation, ordinary users get chat, agents, Skills/MCP viewing,
their access tokens and profile. Custom roles exist only to grant assistants
in ``da_agent_grant``.
"""
from __future__ import annotations

ROLE_ADMIN = "admin"
ROLE_USER = "user"
BUILTIN_ROLE_KEYS: tuple[str, ...] = (ROLE_ADMIN, ROLE_USER)
