"""Account, role, profile, and assistant grant management APIs."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.accounts import (
    AccountNotFoundError,
    AutoRegisterUnavailableError,
    IdentityConflictError,
    User,
    current_user,
    filter_accessible_agents,
    get_account_store,
    hash_default_password,
    hash_password,
    list_permission_catalog,
    require_permission,
    verify_password,
)
from core.accounts.permissions import ROLE_ADMIN
from core.agent_profile_service import list_agent_profiles

router = APIRouter(prefix="/api/v1/dataagent", tags=["accounts"])


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class ProfileUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    email: Optional[str] = None


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=6, max_length=128)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    display_name: str = ""
    email: Optional[str] = None
    role_ids: Optional[List[int]] = None


class UserUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    email: Optional[str] = None
    is_active: Optional[bool] = None
    role_ids: Optional[List[int]] = None


class RoleCreateRequest(BaseModel):
    role_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str = ""


class RoleUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = None


class RoleUsersUpdateRequest(BaseModel):
    user_ids: List[int] = Field(default_factory=list)


class RoleAgentsUpdateRequest(BaseModel):
    agent_ids: List[str] = Field(default_factory=list)


class AgentGrantsUpdateRequest(BaseModel):
    user_ids: Optional[List[int]] = None
    role_ids: Optional[List[int]] = None


def _map_account_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AccountNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, IdentityConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, AutoRegisterUnavailableError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Self-service Profile (any signed-in user)
# ---------------------------------------------------------------------------

@router.get("/profile")
def get_profile(user: User = Depends(require_permission("menu:profile"))):
    accessible = filter_accessible_agents(user, list_agent_profiles())
    return {
        **user.to_public_dict(),
        "accessible_agents": [
            {
                "agent_id": str(a.get("agent_id") or ""),
                "name": str(a.get("name") or ""),
                "description": str(a.get("description") or ""),
            }
            for a in accessible
        ],
    }


@router.put("/profile")
def update_profile(
    payload: ProfileUpdateRequest,
    user: User = Depends(require_permission("menu:profile")),
):
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return user.to_public_dict()
    try:
        updated = get_account_store().update_user(
            user.id,
            display_name=fields["display_name"] if "display_name" in fields else None,
            email=fields["email"] if "email" in fields else ...,
            # Manual email changes are always saved as unverified.
            email_verified=False if "email" in fields else None,
        )
    except Exception as exc:
        raise _map_account_error(exc) from exc
    return updated.to_public_dict()


@router.put("/profile/password")
def change_own_password(
    payload: PasswordChangeRequest,
    user: User = Depends(require_permission("menu:profile")),
):
    store = get_account_store()
    stored_hash = store.get_user_password_hash(user.id)
    matched, _ = verify_password(payload.current_password, stored_hash)
    if not matched:
        raise HTTPException(status_code=400, detail="当前密码不正确")
    try:
        store.update_password(user.id, hash_password(payload.new_password))
    except Exception as exc:
        raise _map_account_error(exc) from exc
    return {"ok": True}


# ---------------------------------------------------------------------------
# User Management (users:manage — disable only, never physically delete)
# ---------------------------------------------------------------------------

@router.get("/users")
def list_users(
    keyword: str = Query(default="", max_length=128),
    is_active: Optional[bool] = Query(default=None),
    role_id: Optional[int] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: User = Depends(require_permission("users:manage")),
):
    return get_account_store().list_users(
        keyword=keyword,
        is_active=is_active,
        role_id=role_id,
        page=page,
        page_size=page_size,
    )


def _admin_role_id() -> int | None:
    for role in get_account_store().list_roles():
        if role.get("role_key") == ROLE_ADMIN:
            return int(role["id"])
    return None


_NO_ADMIN_GRANT = "不支持把用户设为管理员；管理员只能通过命令行 create-user --admin 创建"


@router.post("/users", status_code=201)
def create_user(
    payload: UserCreateRequest,
    _: User = Depends(require_permission("users:manage")),
):
    if _admin_role_id() in set(payload.role_ids or []):
        raise HTTPException(status_code=400, detail=_NO_ADMIN_GRANT)
    try:
        pw_hash = hash_default_password()
        created = get_account_store().create_user(
            username=payload.username,
            display_name=payload.display_name,
            email=payload.email,
            email_verified=False,
            password_hash=pw_hash,
            role_ids=payload.role_ids,
        )
    except Exception as exc:
        raise _map_account_error(exc) from exc
    return created.to_public_dict()


@router.get("/users/{user_id}")
def get_user_detail(
    user_id: int,
    _: User = Depends(require_permission("users:manage")),
):
    user = get_account_store().get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user.to_public_dict()


@router.put("/users/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    actor: User = Depends(require_permission("users:manage")),
):
    fields = payload.model_dump(exclude_unset=True)
    target = get_account_store().get_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if fields.get("role_ids") is not None and not target.is_admin and _admin_role_id() in set(fields["role_ids"]):
        raise HTTPException(status_code=400, detail=_NO_ADMIN_GRANT)
    try:
        updated = get_account_store().update_user(
            user_id,
            display_name=fields["display_name"] if "display_name" in fields else None,
            email=fields["email"] if "email" in fields else ...,
            is_active=fields["is_active"] if "is_active" in fields else None,
            role_ids=fields["role_ids"] if "role_ids" in fields else None,
            acting_user_id=actor.id,
        )
    except Exception as exc:
        raise _map_account_error(exc) from exc
    return updated.to_public_dict()


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    _: User = Depends(require_permission("users:manage")),
):
    target = get_account_store().get_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.is_admin:
        raise HTTPException(status_code=400, detail="管理员账号不能重置为默认密码")
    try:
        pw_hash = hash_default_password()
        get_account_store().update_password(user_id, pw_hash)
    except Exception as exc:
        raise _map_account_error(exc) from exc
    return {"ok": True}


# ---------------------------------------------------------------------------
# Role Management (roles:manage — menu/admin permissions are read-only static)
# ---------------------------------------------------------------------------

@router.get("/roles/permissions")
def get_role_permission_catalog(
    _: User = Depends(require_permission("roles:manage")),
):
    return {"items": list_permission_catalog()}


@router.get("/roles")
def list_roles(
    _: User = Depends(require_permission("roles:manage")),
):
    return {"items": get_account_store().list_roles()}


@router.post("/roles", status_code=201)
def create_role(
    payload: RoleCreateRequest,
    _: User = Depends(require_permission("roles:manage")),
):
    try:
        return get_account_store().create_role(
            role_key=payload.role_key,
            name=payload.name,
            description=payload.description,
        )
    except Exception as exc:
        raise _map_account_error(exc) from exc


@router.put("/roles/{role_id}")
def update_role(
    role_id: int,
    payload: RoleUpdateRequest,
    _: User = Depends(require_permission("roles:manage")),
):
    fields = payload.model_dump(exclude_unset=True)
    try:
        return get_account_store().update_role(
            role_id,
            name=fields.get("name"),
            description=fields.get("description"),
        )
    except Exception as exc:
        raise _map_account_error(exc) from exc


@router.delete("/roles/{role_id}")
def delete_role(
    role_id: int,
    _: User = Depends(require_permission("roles:manage")),
):
    try:
        get_account_store().delete_role(role_id)
    except Exception as exc:
        raise _map_account_error(exc) from exc
    return {"ok": True}


@router.get("/roles/{role_id}/users")
def list_role_users(
    role_id: int,
    _: User = Depends(require_permission("roles:manage")),
):
    try:
        return {"items": get_account_store().list_role_users(role_id)}
    except Exception as exc:
        raise _map_account_error(exc) from exc


@router.put("/roles/{role_id}/users")
def set_role_users(
    role_id: int,
    payload: RoleUsersUpdateRequest,
    _: User = Depends(require_permission("roles:manage")),
):
    if role_id == _admin_role_id():
        current = {int(item["id"]) for item in get_account_store().list_role_users(role_id)}
        if any(uid not in current for uid in payload.user_ids):
            raise HTTPException(status_code=400, detail=_NO_ADMIN_GRANT)
    try:
        return {"items": get_account_store().set_role_users(role_id, payload.user_ids)}
    except Exception as exc:
        raise _map_account_error(exc) from exc


@router.put("/roles/{role_id}/agents")
def set_role_agents(
    role_id: int,
    payload: RoleAgentsUpdateRequest,
    _: User = Depends(require_permission("roles:manage")),
):
    try:
        return get_account_store().set_role_agent_grants(role_id, payload.agent_ids)
    except Exception as exc:
        raise _map_account_error(exc) from exc


# ---------------------------------------------------------------------------
# Assistant Grants (da_agent_grant — single source of truth for workbench & roles)
# ---------------------------------------------------------------------------

@router.get("/agents/{agent_id}/grants")
def get_agent_grants(
    agent_id: str,
    _: User = Depends(require_permission("agents:manage")),
):
    try:
        return get_account_store().get_agent_grants(agent_id)
    except Exception as exc:
        raise _map_account_error(exc) from exc


@router.put("/agents/{agent_id}/grants")
def set_agent_grants(
    agent_id: str,
    payload: AgentGrantsUpdateRequest,
    _: User = Depends(require_permission("agents:manage")),
):
    try:
        return get_account_store().set_agent_grants(
            agent_id,
            user_ids=payload.user_ids,
            role_ids=payload.role_ids,
        )
    except Exception as exc:
        raise _map_account_error(exc) from exc
