import json
import subprocess
from types import SimpleNamespace

import pytest

from core import sandbox_config as sandbox
from config import Settings, _warn_legacy_sandbox_network


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
        sandbox._discover_mount_root('docker', 'other')
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


def test_host_mount_discovery_never_inspects_a_container(monkeypatch):
    monkeypatch.setattr(sandbox, '_running_in_container', lambda: False)
    def unexpected(*args, **kwargs): raise AssertionError('host mode must not inspect')
    monkeypatch.setattr(sandbox.subprocess, 'run', unexpected)
    assert sandbox.backend_mounts() == []


@pytest.mark.asyncio
async def test_cleanup_accepts_an_absent_docker_container(monkeypatch):
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
    await sandbox.remove_segment_sandbox({'cli': 'docker', 'runId': 'review'})
    pattern = commands[1][-1].removeprefix('name=')
    name = commands[0][-1]
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


@pytest.mark.parametrize("network_enabled", [False, True])
@pytest.mark.parametrize("network_mode", ["bridge", "host"])
def test_sandbox_config_carries_resource_limits(monkeypatch, tmp_path, network_enabled, network_mode):
    monkeypatch.setattr(sandbox.shutil, 'which', lambda _: '/usr/bin/docker')
    monkeypatch.setattr(sandbox, 'backend_mounts', lambda: [])
    cfg = SimpleNamespace(dataagent_sandbox_image='img', dataagent_sandbox_memory='1g',
                          dataagent_sandbox_cpus='1.5', dataagent_sandbox_pids_limit=256,
                          dataagent_sandbox_network_enabled=network_enabled,
                          dataagent_sandbox_network_mode=network_mode)
    config, _ = sandbox.build_sandbox_config(cfg, run_id='r', workspace=tmp_path / 'topic' / 'workspace',
                                             enabled_roots={}, lifetime=60, env={})
    assert (config['memory'], config['cpus'], config['pidsLimit'], config['networkEnabled'], config['networkMode']) == (
        '1g', '1.5', 256, network_enabled, network_mode
    )
    assert 'network' not in config


def test_in_container_path_without_host_mount_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox.shutil, 'which', lambda _: '/usr/bin/docker')
    monkeypatch.setattr(sandbox, '_running_in_container', lambda: True)
    # A tmpfs has no host Source and must never be used as a bind source.
    monkeypatch.setattr(sandbox, 'backend_mounts', lambda: [{'Source': '', 'Destination': '/'}])
    cfg = SimpleNamespace(dataagent_sandbox_image='img', dataagent_sandbox_memory='1g',
                          dataagent_sandbox_cpus='1', dataagent_sandbox_pids_limit=64,
                          dataagent_sandbox_network_enabled=False)
    with pytest.raises(ValueError, match='No daemon-visible mount source'):
        sandbox.build_sandbox_config(cfg, run_id='r', workspace=tmp_path / 'workspace',
                                     enabled_roots={}, lifetime=60, env={})


def test_sandbox_gets_runtime_pip_index_only_when_set(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox.shutil, 'which', lambda _: '/usr/bin/docker')
    monkeypatch.setattr(sandbox, 'backend_mounts', lambda: [])
    base = dict(dataagent_sandbox_image='img', dataagent_sandbox_memory='1g', dataagent_sandbox_cpus='1',
                dataagent_sandbox_pids_limit=64, dataagent_sandbox_network_enabled=True)
    mirror = SimpleNamespace(**base, dataagent_sandbox_pip_index_url='http://mirror.local/simple',
                             dataagent_sandbox_pip_trusted_host='mirror.local')
    config, _ = sandbox.build_sandbox_config(mirror, run_id='r', workspace=tmp_path / 'a' / 'workspace',
                                             enabled_roots={}, lifetime=60, env={'X': '1'})
    assert config['env'] == {'PIP_INDEX_URL': 'http://mirror.local/simple', 'PIP_TRUSTED_HOST': 'mirror.local', 'X': '1'}
    plain = SimpleNamespace(**base, dataagent_sandbox_pip_index_url='', dataagent_sandbox_pip_trusted_host='')
    config, _ = sandbox.build_sandbox_config(plain, run_id='r', workspace=tmp_path / 'b' / 'workspace',
                                             enabled_roots={}, lifetime=60, env={'X': '1'})
    assert config['env'] == {'X': '1'}


@pytest.mark.parametrize('raw, expected', [
    ('true', True), ('TRUE', True), ('TrUe', True), ('1', True), ('yes', True), ('YeS', True),
    ('false', False), ('FALSE', False), ('FaLsE', False), ('0', False), ('no', False), ('No', False),
])
def test_network_enabled_parses_environment(monkeypatch, raw, expected):
    monkeypatch.setenv('DATAAGENT_SANDBOX_NETWORK_ENABLED', raw)
    assert Settings(_env_file=None).dataagent_sandbox_network_enabled is expected


@pytest.mark.parametrize('raw', ['', 'bridge', 'none', 'on', 'off', '2', ' true ', 'container:abc'])
def test_network_enabled_rejects_invalid_environment(monkeypatch, raw):
    monkeypatch.setenv('DATAAGENT_SANDBOX_NETWORK_ENABLED', raw)
    with pytest.raises(ValueError, match='DATAAGENT_SANDBOX_NETWORK_ENABLED must be true/false/1/0/yes/no'):
        Settings(_env_file=None)


def test_network_enabled_defaults_to_false(monkeypatch):
    monkeypatch.delenv('DATAAGENT_SANDBOX_NETWORK_ENABLED', raising=False)
    assert Settings(_env_file=None).dataagent_sandbox_network_enabled is False


@pytest.mark.parametrize('raw, expected', [
    ('bridge', 'bridge'), ('BRIDGE', 'bridge'), ('Bridge', 'bridge'), (' bridge ', 'bridge'),
    ('host', 'host'), ('HOST', 'host'), ('Host', 'host'), (' host ', 'host'),
])
def test_network_mode_parses_environment(monkeypatch, raw, expected):
    monkeypatch.setenv('DATAAGENT_SANDBOX_NETWORK_MODE', raw)
    assert Settings(_env_file=None).dataagent_sandbox_network_mode == expected


@pytest.mark.parametrize('raw', ['', 'none', 'container:abc', 'default', 'true', 'false', 'overlay', 'macvlan'])
def test_network_mode_rejects_invalid_environment(monkeypatch, raw):
    monkeypatch.setenv('DATAAGENT_SANDBOX_NETWORK_MODE', raw)
    with pytest.raises(ValueError, match="DATAAGENT_SANDBOX_NETWORK_MODE must be 'bridge' or 'host'"):
        Settings(_env_file=None)


def test_network_mode_defaults_to_bridge(monkeypatch):
    monkeypatch.delenv('DATAAGENT_SANDBOX_NETWORK_MODE', raising=False)
    assert Settings(_env_file=None).dataagent_sandbox_network_mode == 'bridge'


@pytest.mark.parametrize('source', ['environment', 'dotenv'])
@pytest.mark.parametrize('enabled', [None, 'true'])
def test_legacy_network_is_ignored_and_warns_once(monkeypatch, tmp_path, caplog, source, enabled):
    monkeypatch.delenv('DATAAGENT_SANDBOX_NETWORK', raising=False)
    monkeypatch.delenv('DATAAGENT_SANDBOX_NETWORK_ENABLED', raising=False)
    env_file = tmp_path / '.env'
    if source == 'environment':
        monkeypatch.setenv('DATAAGENT_SANDBOX_NETWORK', 'container:ignored')
    else:
        env_file.write_text('DATAAGENT_SANDBOX_NETWORK=container:ignored\n')
    if enabled is not None:
        monkeypatch.setenv('DATAAGENT_SANDBOX_NETWORK_ENABLED', enabled)
    _warn_legacy_sandbox_network.cache_clear()
    try:
        with caplog.at_level('WARNING', logger='config'):
            for _ in range(2):
                cfg = Settings(_env_file=env_file if source == 'dotenv' else None)
                assert cfg.dataagent_sandbox_network_enabled is (enabled == 'true')
                assert 'dataagent_sandbox_network' not in cfg.model_dump()
        warnings = [record for record in caplog.records if record.name == 'config']
        assert len(warnings) == 1
        assert warnings[0].getMessage() == (
            'DATAAGENT_SANDBOX_NETWORK is ignored; use DATAAGENT_SANDBOX_NETWORK_ENABLED instead'
        )
    finally:
        _warn_legacy_sandbox_network.cache_clear()
