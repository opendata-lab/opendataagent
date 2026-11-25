"""Domain models and exceptions for unified user accounts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class AccountError(RuntimeError):
    """Base error for account operations."""


class AccountDisabledError(AccountError):
    """Raised when a matched user account is disabled."""


class IdentityConflictError(AccountError):
    """Raised when an unbound external identity conflicts with an existing username or admin."""


class AutoRegisterUnavailableError(AccountError):
    """Raised when auto-registration cannot proceed (e.g. DEFAULT_USER_PASSWORD missing)."""


class AccountNotFoundError(AccountError):
    """Raised when a user, role, or agent cannot be found."""


class AgentAccessDeniedError(AccountError):
    """Raised when an account is disabled or lacks access to an agent."""


@dataclass(frozen=True)
class RoleRef:
    id: int
    role_key: str
    name: str
    is_builtin: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role_key": self.role_key,
            "name": self.name,
            "is_builtin": self.is_builtin,
        }


@dataclass(frozen=True)
class IdentityRef:
    id: int
    source: str
    subject: str
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "subject": self.subject,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    is_active: bool = True
    roles: tuple[RoleRef, ...] = field(default_factory=tuple)
    identities: tuple[IdentityRef, ...] = field(default_factory=tuple)
    created_at: str = ""
    updated_at: str = ""
    last_login_at: str | None = None

    @property
    def role_keys(self) -> frozenset[str]:
        return frozenset(r.role_key for r in self.roles)

    @property
    def role_ids(self) -> tuple[int, ...]:
        return tuple(r.id for r in self.roles)

    @property
    def is_admin(self) -> bool:
        return "admin" in self.role_keys

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.id,
            "username": self.username,
            "display_name": self.display_name or self.username,
            "is_active": self.is_active,
            "is_admin": self.is_admin,
            "roles": [r.to_dict() for r in self.roles],
            "identities": [i.to_dict() for i in self.identities],
            "created_at": self.created_at,
            "last_login_at": self.last_login_at,
        }


@dataclass(frozen=True)
class AccountContext:
    """Verified per-request account and Widget site context."""

    user: User | None
    source: str = "web"  # "web" | "widget" | "remote"
    website_id: str = ""
    auth_method: str = "session"  # "session" | "mcp_token" | "widget_trusted"
    login_method: str = ""  # "password" | "oauth" when present in the session

    @property
    def user_id(self) -> int | None:
        return self.user.id if self.user is not None else None

    @property
    def is_admin(self) -> bool:
        return bool(self.user and self.user.is_admin)

    def to_store_context(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "website_id": self.website_id,
            "user_id": self.user_id,
            "is_admin": self.is_admin,
        }
