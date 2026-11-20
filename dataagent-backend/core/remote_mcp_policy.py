"""Who is calling a published assistant's MCP endpoint.

The endpoint authenticates with an OAuth access token issued for this
assistant's URL and maps it to the same platform identity a web login gets.
Everything after that is the ordinary signed-in path.

The issuer and signing keys come from the web login's own OAuth provider
(its OIDC discovery document), and the public address is the one the caller
used, so publishing an assistant needs no settings of its own.
"""
from __future__ import annotations

from functools import lru_cache
import re
from urllib.parse import urlsplit

import httpx
import jwt

from core.auth import AuthIdentity, get_auth_settings, resolve_oauth_role

PREFIX = "/api/v1/remote-agents"
SCOPE = "dataagent:ask"
_AGENT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class RemoteMcpError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _https_url(url: object) -> bool:
    try:
        parsed = urlsplit(url) if isinstance(url, str) else None
        valid_port = parsed is not None and (parsed.port is None or parsed.port > 0)
    except ValueError:
        return False
    return bool(valid_port and parsed.scheme == "https" and parsed.hostname and not parsed.username
                and not parsed.query and not parsed.fragment)


def enabled() -> bool:
    auth = get_auth_settings()
    return auth.enabled and auth.oauth_login_enabled and bool(auth.oauth.server_metadata_url)


@lru_cache(maxsize=1)
def _provider_metadata(url: str) -> tuple[str, str]:
    """The provider's issuer and JWKS URL. A failed fetch is not cached."""
    try:
        document = httpx.get(url, timeout=5).raise_for_status().json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RemoteMcpError("unavailable", "无法读取身份提供方的发现文档") from exc
    issuer, jwks_uri = document.get("issuer"), document.get("jwks_uri")
    if not (_https_url(issuer) and _https_url(jwks_uri)):
        raise RemoteMcpError("unavailable", "身份提供方的发现文档缺少 HTTPS 的 issuer 或 jwks_uri")
    return issuer, jwks_uri


def issuer() -> str:
    return _provider_metadata(get_auth_settings().oauth.server_metadata_url)[0]


def endpoint(origin: str, agent_id: str) -> str:
    if not _AGENT_ID.fullmatch(agent_id):
        raise RemoteMcpError("not_found", "找不到 MCP 助手")
    return f"{origin}{PREFIX}/{agent_id}/mcp"


def shared_audience(origin: str) -> str:
    """One audience for every published assistant, for IdPs without RFC 8707.

    Keycloak, for one, ignores the MCP client's 'resource' parameter; an audience
    mapper on the dataagent:ask scope can still put this value into 'aud'. Which
    assistant a token may use is decided by assistant visibility, as for a web login.
    """
    return f"{origin}{PREFIX}"


@lru_cache(maxsize=4)
def _jwks(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, timeout=5)


def verify_identity(authorization: str, agent_id: str, origin: str) -> AuthIdentity:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 8192:
        raise RemoteMcpError("unauthorized", "请登录后连接 MCP")
    token_issuer, jwks_uri = _provider_metadata(get_auth_settings().oauth.server_metadata_url)
    try:
        key = _jwks(jwks_uri).get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], issuer=token_issuer,
                            audience=[endpoint(origin, agent_id), shared_audience(origin)],
                            options={"require": ["iss", "aud", "sub", "exp"]})
    except (jwt.PyJWTError, ValueError) as exc:
        raise RemoteMcpError("unauthorized", "MCP 访问令牌无效或已过期") from exc
    scope = claims.get("scope")
    if not isinstance(scope, str) or SCOPE not in scope.split():
        raise RemoteMcpError("forbidden", "令牌未授权使用问数助手")
    # The same 'sub' the web login's user-info hook returns, so both see one account.
    subject = claims["sub"]
    provider = get_auth_settings().oauth.provider_name
    if not isinstance(subject, str) or not subject.strip() or len(f"{provider}:{subject}") > 255:
        raise RemoteMcpError("unauthorized", "令牌无法映射到平台用户")
    name = claims.get("name") or claims.get("preferred_username") or subject
    return AuthIdentity(user_id=f"{provider}:{subject}", display_name=str(name)[:255],
                        role=resolve_oauth_role(provider, subject), provider=provider)
