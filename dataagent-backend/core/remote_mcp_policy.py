"""Access policy for published assistants."""
from __future__ import annotations

import re

from core import remote_mcp_tokens
from core.auth import AuthIdentity, LOCAL_PROVIDER, ROLE_ADMIN, get_auth_settings, resolve_oauth_role

PREFIX = "/api/v1/remote-agents"
_AGENT_ID = r"[A-Za-z0-9_-]{1,64}"
_TOKEN = re.compile(rf"{remote_mcp_tokens.TOKEN_PREFIX}[A-Za-z0-9_-]{{43}}")


class RemoteMcpError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def enabled() -> bool:
    auth = get_auth_settings()
    return auth.enabled and (auth.local_login_enabled or auth.oauth_login_enabled)


def endpoint(origin: str, agent_id: str) -> str:
    if not re.fullmatch(_AGENT_ID, agent_id):
        raise RemoteMcpError("not_found", "找不到 MCP 助手")
    return f"{origin}{PREFIX}/{agent_id}/mcp"


def verify_identity(authorization: str) -> AuthIdentity:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not _TOKEN.fullmatch(token):
        raise RemoteMcpError("unauthorized", "MCP 访问令牌无效或已过期")
    record = remote_mcp_tokens.find_active_token(token)
    if record is None:
        raise RemoteMcpError("unauthorized", "MCP 访问令牌无效或已过期")
    subject = record["user_id"]
    provider, _, account = subject.partition(":")
    if not provider or not account:
        raise RemoteMcpError("unauthorized", "令牌无法映射到平台用户")
    if provider == LOCAL_PROVIDER:
        if not any(admin["username"] == account for admin in get_auth_settings().local_admins):
            raise RemoteMcpError("unauthorized", "令牌无法映射到平台用户")
        role = ROLE_ADMIN
    else:
        role = record["oauth_role"] or resolve_oauth_role(provider, account)
    remote_mcp_tokens.record_use(record["id"], record["last_used_at"])
    return AuthIdentity(user_id=subject, display_name=record["display_name"], role=role, provider=provider)
