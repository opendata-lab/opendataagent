"""OAuth policy for published assistants."""
from __future__ import annotations

from base64 import urlsafe_b64encode
import hashlib
import hmac
import re
import secrets
from time import time
from urllib.parse import urlsplit

import jwt

from core.auth import AuthIdentity, LOCAL_PROVIDER, ROLE_ADMIN, get_auth_settings, resolve_oauth_role

PREFIX = "/api/v1/remote-agents"
OAUTH_PREFIX = f"{PREFIX}/oauth"
SCOPE = "dataagent:ask"
PUBLIC_CLIENT_ID = "dataagent"
ACCESS_TTL = 3600
CODE_TTL = 300
_AGENT_ID = r"[A-Za-z0-9_-]{1,64}"
_RESOURCE_PATH = re.compile(rf"{re.escape(PREFIX)}/{_AGENT_ID}/mcp")
_PKCE_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
_PKCE_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
_NON_APP_SCHEMES = {"about", "blob", "data", "file", "ftp", "intent", "javascript", "vbscript", "ws", "wss"}


class RemoteMcpError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def enabled() -> bool:
    auth = get_auth_settings()
    return auth.enabled and (auth.local_login_enabled or auth.oauth_login_enabled)


def _key(purpose: str) -> bytes:
    return hmac.digest(get_auth_settings().secret_key.encode(), f"dataagent:mcp:{purpose}:v1".encode(), "sha256")


def endpoint(origin: str, agent_id: str) -> str:
    if not re.fullmatch(_AGENT_ID, agent_id):
        raise RemoteMcpError("not_found", "找不到 MCP 助手")
    return f"{origin}{PREFIX}/{agent_id}/mcp"


def shared_audience(origin: str) -> str:
    return f"{origin}{PREFIX}"


def valid_resource(origin: str, resource: str) -> bool:
    return resource == shared_audience(origin) or (
        resource.startswith(origin) and bool(_RESOURCE_PATH.fullmatch(resource[len(origin):]))
    )


def valid_redirect_uri(uri: object) -> bool:
    if not isinstance(uri, str) or not uri or len(uri) > 2048 or re.search(r"[\s\x00-\x1f\x7f\\#]", uri):
        return False
    try:
        parsed = urlsplit(uri)
        if (parsed.username is not None or parsed.password is not None
                or (parsed.port is not None and parsed.port <= 0)):
            return False
    except ValueError:
        return False
    if parsed.scheme == "http":
        return bool(re.fullmatch(r"(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]+)?", parsed.netloc, re.IGNORECASE | re.ASCII))
    if parsed.scheme == "https":
        return bool(parsed.hostname)
    return (bool(parsed.scheme and uri.partition(":")[2].startswith("/") and (parsed.hostname or parsed.path))
            and parsed.scheme not in _NON_APP_SCHEMES)


def register_client(client_name: str, redirect_uris: list[str]) -> str:
    return jwt.encode({"client_name": client_name, "redirect_uris": redirect_uris}, _key("client"), algorithm="HS256")


def client_registration(client_id: str) -> dict | None:
    if client_id == PUBLIC_CLIENT_ID:
        return None
    try:
        return jwt.decode(client_id, _key("client"), algorithms=["HS256"])
    except (jwt.PyJWTError, ValueError) as exc:
        raise RemoteMcpError("invalid_client", "Client is not registered.") from exc


def validate_client(client_id: str, redirect_uri: str) -> None:
    registration = client_registration(client_id)
    if registration is not None and redirect_uri not in registration["redirect_uris"]:
        raise RemoteMcpError("invalid_request", "Redirect URI does not match the client registration.")
    if not valid_redirect_uri(redirect_uri):
        raise RemoteMcpError("invalid_request", "Invalid redirect URI.")


def valid_challenge(challenge: str, method: str) -> bool:
    return method == "S256" and bool(_PKCE_CHALLENGE.fullmatch(challenge))


def verify_pkce(verifier: str, challenge: str) -> bool:
    if not _PKCE_VERIFIER.fullmatch(verifier):
        return False
    digest = urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    return hmac.compare_digest(digest, challenge.encode())


def issue_refresh_token(origin: str) -> str:
    nonce = secrets.token_urlsafe(32)
    tag = hmac.digest(_key("refresh"), f"{nonce}:{origin}".encode(), "sha256")
    return f"{nonce}.{urlsafe_b64encode(tag).rstrip(b'=').decode()}"


def valid_refresh_token(token: str, origin: str) -> bool:
    nonce, _, tag = token.partition(".")
    if not _PKCE_CHALLENGE.fullmatch(nonce) or not _PKCE_CHALLENGE.fullmatch(tag):
        return False
    expected = hmac.digest(_key("refresh"), f"{nonce}:{origin}".encode(), "sha256")
    return hmac.compare_digest(tag.encode(), urlsafe_b64encode(expected).rstrip(b"="))


def issue_access_token(origin: str, user_id: str, display_name: str, client_id: str, scope: str) -> str:
    return jwt.encode({"iss": origin, "aud": shared_audience(origin), "sub": user_id, "name": display_name,
                       "client_id": client_id, "scope": scope, "exp": int(time()) + ACCESS_TTL,
                       "jti": secrets.token_urlsafe(16)},
                      _key("access"), algorithm="HS256")


def verify_identity(authorization: str, agent_id: str, origin: str) -> AuthIdentity:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 8192:
        raise RemoteMcpError("unauthorized", "请登录后连接 MCP")
    audiences = [endpoint(origin, agent_id), shared_audience(origin)]
    try:
        claims = jwt.decode(token, _key("access"), algorithms=["HS256"], issuer=origin,
                            audience=audiences,
                            options={"require": ["iss", "aud", "sub", "exp", "client_id", "name"]})
    except (jwt.PyJWTError, ValueError) as exc:
        raise RemoteMcpError("unauthorized", "MCP 访问令牌无效或已过期") from exc
    scope = claims.get("scope")
    if not isinstance(scope, str) or SCOPE not in scope.split():
        raise RemoteMcpError("forbidden", "令牌未授权使用问数助手")
    subject = claims["sub"]
    provider, _, account = subject.partition(":")
    if not provider or not account or len(subject) > 255:
        raise RemoteMcpError("unauthorized", "令牌无法映射到平台用户")
    if provider == LOCAL_PROVIDER:
        if not any(admin["username"] == account for admin in get_auth_settings().local_admins):
            raise RemoteMcpError("unauthorized", "令牌无法映射到平台用户")
        role = ROLE_ADMIN
    else:
        role = resolve_oauth_role(provider, account)
    return AuthIdentity(user_id=subject, display_name=claims["name"], role=role, provider=provider)
