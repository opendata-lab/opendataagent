"""Who is calling a published assistant's MCP endpoint.

The endpoint authenticates with an OAuth access token issued for this
assistant's URL and maps it to the same platform identity a web login gets.
Everything after that is the ordinary signed-in path.
"""
from __future__ import annotations

from functools import lru_cache
import re
from urllib.parse import urlsplit

import jwt

from config import get_settings
from core.auth import AuthIdentity, get_auth_settings, resolve_oauth_role

PREFIX = "/api/v1/remote-agents"
SCOPE = "dataagent:ask"
_AGENT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class RemoteMcpError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _https_url(url: str, *, origin: bool = False) -> bool:
    try:
        parsed = urlsplit(url)
        valid_port = parsed.port is None or parsed.port > 0
    except ValueError:
        return False
    return bool(parsed.scheme == "https" and parsed.hostname and valid_port and not parsed.username
                and not parsed.query and not parsed.fragment and (not origin or not parsed.path))


def enabled() -> bool:
    cfg = get_settings()
    auth = get_auth_settings()
    return (auth.enabled and auth.oauth_login_enabled
            and _https_url(cfg.remote_mcp_public_origin, origin=True)
            and _https_url(cfg.remote_mcp_oauth_issuer)
            and _https_url(cfg.remote_mcp_oauth_jwks_url))


def endpoint(agent_id: str) -> str:
    if not _AGENT_ID.fullmatch(agent_id):
        raise RemoteMcpError("not_found", "找不到 MCP 助手")
    return f"{get_settings().remote_mcp_public_origin}{PREFIX}/{agent_id}/mcp"


def shared_audience() -> str:
    """One audience for every published assistant, for IdPs without RFC 8707.

    Keycloak, for one, ignores the MCP client's 'resource' parameter; an audience
    mapper on the dataagent:ask scope can still put this value into 'aud'. Which
    assistant a token may use is decided by assistant visibility, as for a web login.
    """
    return f"{get_settings().remote_mcp_public_origin}{PREFIX}"


@lru_cache(maxsize=4)
def _jwks(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, timeout=5)


def verify_identity(authorization: str, agent_id: str) -> AuthIdentity:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 8192:
        raise RemoteMcpError("unauthorized", "请登录后连接 MCP")
    cfg = get_settings()
    try:
        key = _jwks(cfg.remote_mcp_oauth_jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"],
                            issuer=cfg.remote_mcp_oauth_issuer, audience=[endpoint(agent_id), shared_audience()],
                            options={"require": ["iss", "aud", "sub", "exp"]})
    except (jwt.PyJWTError, ValueError) as exc:
        raise RemoteMcpError("unauthorized", "MCP 访问令牌无效或已过期") from exc
    scope = claims.get("scope")
    if not isinstance(scope, str) or SCOPE not in scope.split():
        raise RemoteMcpError("forbidden", "令牌未授权使用问数助手")
    # Same stable subject the web OAuth login maps to, so both see one account.
    subject = claims.get(cfg.remote_mcp_oauth_subject_claim)
    provider = get_auth_settings().oauth.provider_name
    if not isinstance(subject, str) or not subject.strip() or len(f"{provider}:{subject}") > 255:
        raise RemoteMcpError("unauthorized", "令牌无法映射到平台用户")
    name = claims.get("name") or claims.get("preferred_username") or subject
    return AuthIdentity(user_id=f"{provider}:{subject}", display_name=str(name)[:255],
                        role=resolve_oauth_role(provider, subject), provider=provider)
