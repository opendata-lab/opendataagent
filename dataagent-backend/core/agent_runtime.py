"""Skill-agnostic prompt, resource selection and provider configuration."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from core.provider_runtime import build_provider_env as _build_provider_env
from core.mcp_admin_service import resolve_runtime_mcp_servers
from core.skill_admin_service import resolve_enabled_skill_runtime, resolve_runtime_provider_selection
from core.skill_discovery import resolve_builtin_skill_root_dir, resolve_skill_discovery_root_dir
SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "data_agent_system_prompt.md"


def _load_system_prompt_template() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()

def _dedupe_strings(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    result: list[str] = []
    seen: set[str] = set()
    iterable = values if isinstance(values, (list, tuple, set)) else []
    for value in iterable:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result

def _build_system_prompt(
    database_hint: str | None,
    skill_runtime: dict[str, Any] | None = None,
    agent_snapshot: dict[str, Any] | None = None,
) -> str:
    enabled_skills = list((skill_runtime or {}).get("enabled_folders") or [])
    enabled_skills_text = "、".join(enabled_skills) if enabled_skills else "未配置"
    lines = [_load_system_prompt_template(), "", "# 运行时上下文", f"- 已启用 Skills：当前已启用：{enabled_skills_text}。"]
    lines.append("- 可写临时目录：/tmp。仅存放中间过程文件，运行结束即可能失效；最终交付文件仍必须写入工作区 `output/`。")
    custom_prompt = str((agent_snapshot or {}).get("system_prompt") or "").strip()
    if custom_prompt:
        lines.extend(["", "# 智能体系统提示词", custom_prompt])
    if database_hint:
        lines.append(f"- 用户显式提供的 database hint: {database_hint}")
    return "\n".join(lines)

def resolve_agent_skill_runtime(
    agent_snapshot: dict[str, Any] | None,
    fallback_runtime: dict[str, Any],
) -> dict[str, Any]:
    selected = _dedupe_strings((agent_snapshot or {}).get("skill_folders"))
    if not selected:
        if agent_snapshot:
            return {
                "primary_folder": "",
                "primary_root": str(resolve_builtin_skill_root_dir()),
                "enabled_folders": [],
                "enabled_roots": {},
            }
        return fallback_runtime
    discovery_root = resolve_skill_discovery_root_dir()
    roots = {folder: str((discovery_root / folder).resolve()) for folder in selected}
    primary_folder = selected[0]
    return {
        "primary_folder": primary_folder,
        "primary_root": roots.get(primary_folder, str(resolve_builtin_skill_root_dir())),
        "enabled_folders": selected,
        "enabled_roots": roots,
    }

def _build_mcp_servers(
    cfg: Any,
    mcp_server_ids: list[str] | tuple[str, ...] | None = None,
    agent_snapshot: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    del cfg
    return resolve_runtime_mcp_servers(mcp_server_ids, agent_snapshot=agent_snapshot)
