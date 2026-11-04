from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

from core.skill_discovery import SkillDiscoveryError, resolve_skill_discovery_root_dir

logger = logging.getLogger(__name__)

# Legacy log directory retained for inspecting old task logs.
LOGS_DIRNAME = "logs"
CONTAINER_RUNTIME_ROOT = Path("/dataagent_runtime")


def _backend_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _safe_topic_id(topic_id: str) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(topic_id or "").strip()).strip(".-")
    if not safe_id:
        raise ValueError("topic_id is required")
    return safe_id


def sanitize_topic_id(topic_id: str) -> str:
    return _safe_topic_id(topic_id)


def _resolve_runtime_root(raw: str | None = None) -> Path:
    value = str(raw or "").strip()
    if not value:
        try:
            from config import get_settings

            settings = get_settings()
            value = str(getattr(settings, "dataagent_runtime_root", "") or "").strip()
            if not value:
                value = str(settings.dataagent_host_root or "").strip()
        except Exception:
            value = ""
    if value:
        path = Path(value).expanduser()
        if path.is_absolute():
            return path.resolve()
        return (_backend_root() / path).resolve()

    return CONTAINER_RUNTIME_ROOT.resolve()


def resolve_topic_root(topic_id: str, *, runtime_root: str | Path | None = None) -> Path:
    """Per-topic root dir ``<runtime_root>/<topic_id>``.

    Workspace and read-only result mounts are separate children. Legacy home
    and logs remain available for rollback until the topic is deleted.
    """
    root = Path(runtime_root).expanduser().resolve() if runtime_root else _resolve_runtime_root()
    return root / _safe_topic_id(topic_id)


def resolve_topic_workspace(topic_id: str, *, runtime_root: str | Path | None = None) -> Path:
    """Agent working directory ``<runtime_root>/<topic_id>/workspace``.

    This is the shared workspace contract used by the agent cwd, the backend
    topic file APIs (``uploads/``, ``output/``), and the sandbox's
    ``/mnt/workspace`` bind source. It is a subdir of the topic root so the
    results sibling never appears inside the writable workspace.
    """
    return resolve_topic_root(topic_id, runtime_root=runtime_root) / "workspace"


def prepare_topic_workspace(
    topic_id: str,
    enabled_folders: list[str] | tuple[str, ...],
    *,
    allow_empty: bool = False,
    runtime_root: str | Path | None = None,
    workspace_dir: str | Path | None = None,
) -> Path:
    workspace = Path(workspace_dir).expanduser().resolve() if workspace_dir else resolve_topic_workspace(topic_id, runtime_root=runtime_root)
    workspace.mkdir(parents=True, exist_ok=True)
    discovery_root = resolve_skill_discovery_root_dir()
    if not enabled_folders and not allow_empty:
        raise SkillDiscoveryError("no enabled skills configured")
    for folder in enabled_folders:
        source = (discovery_root / folder).resolve()
        if not source.is_relative_to(discovery_root.resolve()) or not (source / "SKILL.md").is_file():
            raise SkillDiscoveryError(f"enabled skill missing or invalid: {folder}")
    # Reserved old runtime copies only; user uploads and output are untouched.
    old = workspace / ".claude" / "skills"
    if discovery_root.resolve().is_relative_to(old.resolve()):
        raise SkillDiscoveryError("Skills root cannot be inside a topic workspace")
    if old.is_symlink() or old.is_file():
        old.unlink()
    elif old.is_dir():
        shutil.rmtree(old)
    return workspace


def delete_topic_workspace(topic_id: str, *, runtime_root: str | Path | None = None) -> bool:
    # Physical delete (rmtree) of the whole topic root: workspace/, home/, and
    # logs/ are all removed together. This is a destructive interface; it is only
    # invoked on explicit topic deletion, so do not wire it to other triggers.
    topic_root = resolve_topic_root(topic_id, runtime_root=runtime_root)
    if not topic_root.exists():
        return False
    shutil.rmtree(topic_root)
    return True


def cleanup_orphan_topic_workspaces(
    active_topic_ids: set[str] | list[str] | tuple[str, ...],
    *,
    runtime_root: str | Path | None = None,
) -> list[str]:
    root = Path(runtime_root).expanduser().resolve() if runtime_root else _resolve_runtime_root()
    if not root.is_dir():
        return []

    active = {_safe_topic_id(str(topic_id)) for topic_id in active_topic_ids if str(topic_id or "").strip()}
    removed: list[str] = []
    for child in root.iterdir():
        if not child.is_dir() or child.name in active:
            continue
        shutil.rmtree(child)
        removed.append(child.name)
    return sorted(removed)
