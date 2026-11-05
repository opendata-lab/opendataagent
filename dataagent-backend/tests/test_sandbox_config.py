import json
import subprocess
from types import SimpleNamespace

import pytest

from core import sandbox_config as sandbox


def test_mount_inspection_is_cached_by_container_identity(monkeypatch):
    sandbox._discover_mount_root.cache_clear()
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps([{'Mounts': [{'Source': '/host', 'Destination': '/runtime'}]}]))
    monkeypatch.setattr(sandbox.subprocess, 'run', run)
    try:
        assert sandbox._discover_mount_root('docker', 'backend') == sandbox._discover_mount_root('docker', 'backend')
        assert len(calls) == 1
        sandbox._discover_mount_root('podman', 'other')
        assert len(calls) == 2
    finally:
        sandbox._discover_mount_root.cache_clear()


@pytest.mark.parametrize('error', [subprocess.CalledProcessError(1, ['docker']), subprocess.TimeoutExpired(['docker'], 15), FileNotFoundError()])
def test_inspection_errors_have_actionable_code(monkeypatch, error):
    sandbox._discover_mount_root.cache_clear()
    def run(*args, **kwargs): raise error
    monkeypatch.setattr(sandbox.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='SANDBOX_UNAVAILABLE'):
        sandbox._discover_mount_root('docker', 'backend')
    assert sandbox._discover_mount_root.cache_info().currsize == 0


def test_podman_container_environment_is_recognized(monkeypatch):
    monkeypatch.delenv('DATAAGENT_CONTAINER_ID', raising=False)
    monkeypatch.setattr(sandbox.Path, 'exists', lambda p: str(p) == '/run/.containerenv')
    assert sandbox._running_in_container()


@pytest.mark.asyncio
async def test_cleanup_accepts_absent_container_for_both_name_formats(monkeypatch):
    import re
    commands = []
    class Process:
        def __init__(self, code): self.returncode = code
        async def communicate(self): return b'', b''
    async def spawn(*args, **kwargs):
        commands.append(args)
        return Process(1 if args[1] == 'rm' else 0)
    import asyncio
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    await sandbox.remove_segment_sandbox({'cli': 'podman', 'runId': 'review'})
    pattern = commands[1][-1].removeprefix('name=')
    name = commands[0][-1]
    assert re.fullmatch(pattern, name)
    assert re.fullmatch(pattern, '/' + name)
    assert not re.fullmatch(pattern, name + '-other')


@pytest.mark.asyncio
async def test_cleanup_reports_an_unavailable_daemon(monkeypatch):
    import asyncio
    class Process:
        returncode = 1
        async def communicate(self): return b'', b''
    async def spawn(*args, **kwargs): return Process()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    with pytest.raises(RuntimeError, match='SANDBOX_CLEANUP_FAILED'):
        await sandbox.remove_segment_sandbox({'cli': 'docker', 'runId': 'review'})
