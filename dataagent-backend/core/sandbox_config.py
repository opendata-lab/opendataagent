"""Resolve trusted control-plane paths into Docker daemon mount sources."""
from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


@lru_cache(maxsize=8)
def _discover_mount_root(cli: str, own_id: str) -> list[dict[str, Any]]:
    try:
        inspected = subprocess.run([cli, "inspect", own_id], check=True, capture_output=True, text=True, timeout=15)
        return json.loads(inspected.stdout)[0]["Mounts"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError) as exc:
        raise RuntimeError("SANDBOX_UNAVAILABLE: cannot inspect backend container mounts") from exc


def _running_in_container() -> bool:
    return Path("/.dockerenv").exists() or bool(os.environ.get("DATAAGENT_CONTAINER_ID"))


def backend_mounts() -> list[dict[str, Any]]:
    if not _running_in_container():
        return []
    own_id = os.environ.get("DATAAGENT_CONTAINER_ID") or os.environ.get("HOSTNAME", "")
    return _discover_mount_root("docker", own_id)


def build_sandbox_config(cfg: Any, *, run_id: str, workspace: Path,
                         enabled_roots: dict[str, str], lifetime: int,
                         env: dict[str, str]) -> tuple[dict[str, Any], Path]:
    cli = "docker"
    if not shutil.which(cli):
        raise RuntimeError("SANDBOX_UNAVAILABLE: Docker CLI missing")
    in_container = _running_in_container()
    # Only host-backed mounts can be re-mounted by the daemon; tmpfs has no Source.
    mounts = [m for m in backend_mounts() if str(m.get("Source") or "").startswith("/")]

    def source(local: Path) -> str:
        local = local.resolve()
        if not in_container:
            return str(local)
        # Most-specific mount wins for nested mounts. No host execution fallback.
        for mount in sorted(mounts, key=lambda m: len(m["Destination"]), reverse=True):
            destination = Path(mount["Destination"])
            if local.is_relative_to(destination):
                return str(Path(mount["Source"]) / local.relative_to(destination))
        raise ValueError(f"No daemon-visible mount source for {local}")

    results = workspace.parent / "results"
    workspace.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    return {"runId": run_id, "image": cfg.dataagent_sandbox_image or "oda-sandbox:latest", "cli": cli,
            "uid": os.getuid(), "gid": os.getgid(), "lifetimeSeconds": lifetime,
            "memory": cfg.dataagent_sandbox_memory, "cpus": cfg.dataagent_sandbox_cpus,
            "pidsLimit": cfg.dataagent_sandbox_pids_limit,
            "network": cfg.dataagent_sandbox_network,
            "workspaceSource": source(workspace), "resultsSource": source(results),
            "skills": [{"folder": name, "source": source(Path(root))} for name, root in enabled_roots.items()],
            "env": env}, results


async def remove_segment_sandbox(config: dict[str, Any]) -> None:
    """Backend safety net when the Cell is killed before its finally block."""
    import asyncio
    import hashlib
    name = "oda-sandbox-" + hashlib.sha256(config["runId"].encode()).hexdigest()[:24]
    async def command(*args: str) -> tuple[int, bytes]:
        try:
            process = await asyncio.create_subprocess_exec(config["cli"], *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        except OSError as exc:
            raise RuntimeError("SANDBOX_CLEANUP_FAILED") from exc
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise RuntimeError("SANDBOX_CLEANUP_FAILED") from exc
        return process.returncode, output

    status, _ = await command("rm", "-f", name)
    if status:
        status, output = await command("ps", "-aq", "--filter", f"name=^/{name}$")
        if status or output.strip():
            raise RuntimeError("SANDBOX_CLEANUP_FAILED")
