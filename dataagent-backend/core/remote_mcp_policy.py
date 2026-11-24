"""Published MCP assistant endpoint helpers.

Token verification, user loading, and role/permission checks live solely in
``core.accounts``; this module only defines the MCP route prefix and errors.
"""
from __future__ import annotations

import re

PREFIX = "/api/v1/remote-agents"
_AGENT_ID = r"[A-Za-z0-9_-]{1,64}"


class RemoteMcpError(ValueError):
    def __init__(self, code: str, message: str, *, next_step: str = ""):
        super().__init__(message)
        self.code = code
        self.next_step = next_step


def enabled() -> bool:
    return True


def endpoint(origin: str, agent_id: str) -> str:
    if not re.fullmatch(_AGENT_ID, agent_id):
        raise RemoteMcpError("not_found", "找不到 MCP 助手")
    return f"{origin}{PREFIX}/{agent_id}/mcp"
