"""Session permission policy using MCP tools/list annotations."""
from __future__ import annotations
from typing import Any
from core.agent_profile_service import normalize_permission_mode

PLAN_DENIED_BUILTIN_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
EXIT_PLAN_MODE_TOOL_NAME = "ExitPlanMode"
POST_PLAN_MODE = "acceptEdits"
CARD_ANNOTATION_KEYS = frozenset({"title", "summary"})

def strip_card_annotations(tool_input: dict[str, Any]) -> dict[str, Any]:
    """Drop confirmation-card annotation keys from a tool input before it is
    forwarded to the underlying tool."""
    return {k: v for k, v in tool_input.items() if k not in CARD_ANNOTATION_KEYS}


def normalize_permission_decision(decision: Any) -> str:
    """Coerce a permission decision to its canonical persisted form.

    ``allow``/``deny``/``timeout`` stay as-is; anything unknown collapses to
    ``deny`` (fail-closed)."""
    value = str(decision or "").strip().lower()
    return value if value in {"allow", "deny", "timeout"} else "deny"


def _bare_tool_name(tool_name: str) -> str:
    """Reduce an SDK-qualified MCP tool name to its bare tool name.

    ``mcp__example__publish`` -> ``publish``.
    """
    name = str(tool_name or "").strip()
    if name.startswith("mcp__"):
        parts = name.split("__")
        if parts:
            return parts[-1]
    return name


def is_exit_plan_mode(tool_name: str) -> bool:
    """Whether ``tool_name`` is the built-in plan-presentation/approval tool."""
    return _bare_tool_name(tool_name) == EXIT_PLAN_MODE_TOOL_NAME


def post_plan_mode() -> str:
    """Permission mode a session adopts after the user approves a plan."""
    return POST_PLAN_MODE



def is_write_tool(tool_name: str, annotations: dict[str, Any] | None = None) -> bool:
    return tool_name.startswith("mcp__") and (
        is_high_risk_tool(tool_name, annotations) or (annotations or {}).get("readOnlyHint") is not True
    )


def is_high_risk_tool(tool_name: str, annotations: dict[str, Any] | None = None) -> bool:
    return tool_name.startswith("mcp__") and (annotations or {}).get("destructiveHint") is True


def plan_denies_tool(tool_name: str, annotations: dict[str, Any] | None = None) -> bool:
    return is_write_tool(tool_name, annotations) or _bare_tool_name(tool_name) in PLAN_DENIED_BUILTIN_TOOLS


def requires_confirmation(tool_name: str, permission_mode: str | None, annotations: dict[str, Any] | None = None) -> bool:
    mode = normalize_permission_mode(permission_mode)
    if mode in {"bypassPermissions", "plan"}:
        return False
    if mode == "acceptEdits":
        return is_high_risk_tool(tool_name, annotations)
    return is_write_tool(tool_name, annotations)
