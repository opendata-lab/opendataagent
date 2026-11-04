"""Resolve trusted control-plane paths into Docker daemon mount sources."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


def build_sandbox_config(cfg: Any, *, run_id: str, workspace: Path,
                         enabled_roots: dict[str, str], lifetime: int,
                         env: dict[str, str]) -> tuple[dict[str, Any], Path]:
    cli = str(cfg.dataagent_sandbox_backend or "docker")
    if cli not in {"docker", "podman"}:
        raise ValueError("Unsupported container CLI")
    if not shutil.which(cli):
        # Only a confirmed missing Docker executable selects Podman.
        if cli == "docker" and shutil.which("podman"):
            cli = "podman"
        else:
            raise RuntimeError("SANDBOX_UNAVAILABLE: Docker/Podman CLI missing")
    mounts: list[dict[str, Any]] = []
    if Path("/.dockerenv").exists() or os.environ.get("DATAAGENT_CONTAINER_ID"):
        own_id = os.environ.get("DATAAGENT_CONTAINER_ID") or os.environ.get("HOSTNAME", "")
        inspected = subprocess.run([cli, "inspect", own_id], check=True, capture_output=True, text=True, timeout=15)
        mounts = json.loads(inspected.stdout)[0]["Mounts"]

    def source(local: Path) -> str:
        local = local.resolve()
        if not mounts:
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
            "workspaceSource": source(workspace), "resultsSource": source(results),
            "skills": [{"folder": name, "source": source(Path(root))} for name, root in enabled_roots.items()],
            "env": env}, results


async def remove_segment_sandbox(config: dict[str, Any]) -> None:
    """Backend safety net when the Cell is killed before its finally block."""
    import asyncio
    import hashlib
    name = "oda-sandbox-" + hashlib.sha256(config["runId"].encode()).hexdigest()[:24]
    process = await asyncio.create_subprocess_exec(config["cli"], "rm", "-f", name,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        await asyncio.wait_for(process.wait(), timeout=15)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("SANDBOX_CLEANUP_FAILED")
    if process.returncode:
        check = await asyncio.create_subprocess_exec(config["cli"], "ps", "-aq", "--filter", f"name=^/{name}$",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        output, _ = await asyncio.wait_for(check.communicate(), timeout=15)
        if check.returncode or output.strip():
            raise RuntimeError("SANDBOX_CLEANUP_FAILED")
