"""Static role -> permission key table (single source of truth for backend API & frontend menus).

Custom roles grant assistant access via ``da_agent_grant`` only; menu and admin
operation permissions come exclusively from built-in roles.
"""
from __future__ import annotations

from typing import Any, Iterable

ROLE_ADMIN = "admin"
ROLE_USER = "user"
BUILTIN_ROLE_KEYS: tuple[str, ...] = (ROLE_ADMIN, ROLE_USER)

# Each entry defines a permission key, human label, menu/action category, and UI group.
PERMISSION_CATALOG: tuple[dict[str, str], ...] = (
    # Workspace & personal menus
    {"key": "menu:chat", "label": "智能问数 (Chat)", "kind": "menu", "group": "工作区"},
    {"key": "menu:agents", "label": "智能体", "kind": "menu", "group": "工作区"},
    {"key": "menu:skills", "label": "Skills 查看", "kind": "menu", "group": "设置"},
    {"key": "menu:mcp", "label": "MCP 服务查看", "kind": "menu", "group": "设置"},
    {"key": "menu:mcp_tokens", "label": "个人访问令牌", "kind": "menu", "group": "个人"},
    {"key": "menu:profile", "label": "个人资料", "kind": "menu", "group": "个人"},
    # Admin menus & operations
    {"key": "agents:manage", "label": "智能体工作台与授权管理", "kind": "action", "group": "助手管理"},
    {"key": "skills:manage", "label": "Skills 编辑、启停与导入导出", "kind": "action", "group": "设置"},
    {"key": "mcp:manage", "label": "MCP 服务配置管理", "kind": "action", "group": "设置"},
    {"key": "models:manage", "label": "模型管理", "kind": "menu", "group": "设置"},
    {"key": "widget:manage", "label": "Widget 接入管理", "kind": "menu", "group": "接入"},
    {"key": "feedback:view", "label": "用户反馈", "kind": "menu", "group": "工作区"},
    {"key": "experience:manage", "label": "知识与记忆管理", "kind": "menu", "group": "工作区"},
    {"key": "topics:audit", "label": "跨用户会话历史查阅", "kind": "action", "group": "工作区"},
    {"key": "evals:manage", "label": "评测集与评测结果管理", "kind": "menu", "group": "评测"},
    {"key": "mcp_tokens:manage_all", "label": "全部用户访问令牌管理", "kind": "action", "group": "个人"},
    {"key": "users:manage", "label": "用户管理", "kind": "menu", "group": "账号与权限"},
    {"key": "roles:manage", "label": "角色管理", "kind": "menu", "group": "账号与权限"},
)

ALL_PERMISSION_KEYS: frozenset[str] = frozenset(item["key"] for item in PERMISSION_CATALOG)

USER_BASE_PERMISSIONS: frozenset[str] = frozenset(
    {
        "menu:chat",
        "menu:agents",
        "menu:skills",
        "menu:mcp",
        "menu:mcp_tokens",
        "menu:profile",
    }
)

ADMIN_EXTRA_PERMISSIONS: frozenset[str] = frozenset(
    {
        "agents:manage",
        "skills:manage",
        "mcp:manage",
        "models:manage",
        "widget:manage",
        "feedback:view",
        "experience:manage",
        "topics:audit",
        "evals:manage",
        "mcp_tokens:manage_all",
        "users:manage",
        "roles:manage",
    }
)

# Static mapping: role_key -> frozenset of permission keys.
# Custom roles deliberately have no static menu/admin permissions; they only
# hold assistant grants in da_agent_grant.
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    ROLE_USER: USER_BASE_PERMISSIONS,
    ROLE_ADMIN: USER_BASE_PERMISSIONS | ADMIN_EXTRA_PERMISSIONS,
}

# Menu keys exposed to the frontend, derived directly from granted permissions.
_PERMISSION_TO_MENUS: dict[str, tuple[str, ...]] = {
    "menu:chat": ("chat",),
    "menu:agents": ("agents",),
    "menu:skills": ("skills",),
    "menu:mcp": ("mcp",),
    "menu:mcp_tokens": ("mcp-tokens",),
    "menu:profile": ("profile",),
    "models:manage": ("models",),
    "widget:manage": ("widget",),
    "feedback:view": ("feedback",),
    "experience:manage": ("knowledge", "memories"),
    "evals:manage": ("evaluations", "eval-results"),
    "users:manage": ("users",),
    "roles:manage": ("roles",),
}


def permissions_for_roles(role_keys: Iterable[str]) -> frozenset[str]:
    result: set[str] = set()
    for key in role_keys:
        result.update(ROLE_PERMISSIONS.get(str(key or "").strip(), frozenset()))
    return frozenset(result)


def menus_for_permissions(permissions: Iterable[str]) -> tuple[str, ...]:
    perm_set = set(permissions)
    menus: list[str] = []
    for perm_key, menu_names in _PERMISSION_TO_MENUS.items():
        if perm_key in perm_set:
            for name in menu_names:
                if name not in menus:
                    menus.append(name)
    return tuple(menus)


def role_permission_summary(role_key: str) -> dict[str, Any]:
    perms = permissions_for_roles([role_key])
    menus = menus_for_permissions(perms)
    return {
        "permissions": sorted(perms),
        "menus": list(menus),
    }


def list_permission_catalog() -> list[dict[str, str]]:
    return [dict(item) for item in PERMISSION_CATALOG]
