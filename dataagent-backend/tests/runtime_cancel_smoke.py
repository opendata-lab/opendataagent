"""Verify real task cancellation against the running local backend, without paid models.

Requires built Pi, Docker, local MySQL/Redis, backend :8900, and free port 8919.
Uses only its own provider, agent and topics; never changes the default model.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
import httpx
import redis
import signal

ROOT = Path(__file__).resolve().parents[2]
API = '/api/v1/nl2sql'
ADMIN = '/api/v1/nl2sql-admin'
AGENTS = '/api/v1/dataagent'
NAME = 'cancel-smoke-' + uuid.uuid4().hex[:10]
LOGS = Path(tempfile.mkdtemp(prefix=NAME, dir='/tmp'))
BASE = os.environ.get('DATAAGENT_SMOKE_BASE_URL', 'http://127.0.0.1:8900')
client = httpx.Client(base_url=BASE, timeout=10)
topics = []
agent = None


def request(method, path, **kwargs):
    result = client.request(method, path, **kwargs)
    result.raise_for_status()
    return result.json() if result.content else None


def until(check, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.1)
    raise AssertionError('Condition not met within cancellation smoke deadline')


def records(task):
    return request('GET', f'{API}/tasks/{task}/sdk-events?limit=500')['records']


def provider_state():
    return httpx.get('http://127.0.0.1:8919/smoke-state', timeout=3).json()


def cell_pids(topic):
    found = []
    for line in subprocess.check_output(['ps', '-axo', 'pid=,command='], text=True).splitlines():
        if '/dist/src/main.js' not in line or not line.split(None, 1)[1].split()[0].endswith('node'):
            continue
        pid = int(line.split()[0])
        if sys.platform == 'darwin':
            result = subprocess.run(['lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'], capture_output=True, text=True)
            cwd = result.stdout
        else:
            try:
                cwd = str(Path(f'/proc/{pid}/cwd').resolve(strict=True))
            except FileNotFoundError:
                continue
        if topic in cwd:
            found.append(pid)
    return found


def sandbox_ids(topic):
    ids = subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'name=^/oda-sandbox-'], text=True).split()
    if not ids:
        return []
    inspected = json.loads(subprocess.check_output(['docker', 'inspect', *ids], text=True))
    return [item['Id'] for item in inspected if any(topic in m.get('Source', '') for m in item['Mounts'])]


def main():
    global agent
    request('POST', ADMIN+'/providers', json={'provider_id': NAME, 'name': 'Free cancellation fixture',
        'api_format': '/v1/chat/completions', 'provider_enabled': True, 'enabled_models': ['smoke'],
        'custom_models': ['smoke'], 'api_key': 'local-fixture-only', 'base_url': 'http://127.0.0.1:8919'})
    draft = request('POST', AGENTS+'/agents', json={'name': NAME, 'skill_folders': [], 'mcp_server_ids': []})
    agent = draft['agent_id']
    request('POST', f'{AGENTS}/agents/{agent}/publish', json={'expected_revision': draft['revision']})
    coordination = redis.Redis(host='127.0.0.1', port=6379, db=int(os.environ.get('REDIS_DB', '0')))
    results = []
    for scenario in ['CANCEL', 'CANCEL_BASH', 'CRASH_BASH']:
        topic = request('POST', API+'/topics', json={'title': NAME+scenario, 'agent_id': agent})['topic_id']
        topics.append(topic)
        task = request('POST', API+'/tasks', json={'topic_id': topic, 'message_type': 'text',
            'message_content': 'SMOKE:'+scenario, 'provider_id': NAME, 'model': 'smoke'})['task_id']
        until(lambda: any(x['scenario'] == scenario and x['state'] in {'running', 'finished'} for x in provider_state()))
        pids = until(lambda: cell_pids(topic))
        containers = until(lambda: sandbox_ids(topic)) if scenario != 'CANCEL' else []
        started = time.monotonic()
        crashed = scenario == 'CRASH_BASH'
        if crashed:
            for pid in pids: os.kill(pid, signal.SIGKILL)
        else:
            request('POST', f'{API}/tasks/{task}/cancel')
        expected_status = 'error' if crashed else 'suspended'
        terminal_event = 'run.failed' if crashed else 'run.cancelled'
        until(lambda: request('GET', f'{API}/tasks/{task}')['task_status'] == expected_status)
        until(lambda: not cell_pids(topic))
        until(lambda: not sandbox_ids(topic))
        until(lambda: not coordination.exists('da:task:lease:'+task))
        if scenario == 'CANCEL':
            until(lambda: any(x['scenario'] == scenario and x['state'] == 'disconnected' for x in provider_state()))
        elapsed = round(time.monotonic()-started, 2)
        message = request('GET', f'{API}/tasks/{task}/message')
        assert message['status'] == expected_status, message
        before = records(task)
        assert any(x['event_type'] == terminal_event for x in before), before
        with client.stream('GET', f'{API}/tasks/{task}/sdk-events/stream', timeout=5) as stream:
            stream.raise_for_status()
            assert any(terminal_event in line for line in stream.iter_lines())
        time.sleep(1)
        assert len(records(task)) == len(before), 'Events continued after cancellation'
        assert not cell_pids(topic) and not sandbox_ids(topic)
        assert sum(x['scenario'] == scenario for x in provider_state()) == 1, 'Unexpected model retry'
        for pid in pids:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            raise AssertionError(f'Pi child {pid} survived cancellation')
        result = {'scenario': scenario, 'cancel_seconds': elapsed, 'cell_pids_gone': pids,
            'containers_removed': len(containers), 'lease_released': True, 'terminal_sse': True,
            'events_stable': True, 'provider_disconnected': scenario == 'CANCEL'}
        results.append(result)
        print('PASS', json.dumps(result), flush=True)
    (LOGS/'result.json').write_text(json.dumps(results, indent=2))


if __name__ == '__main__':
    provider = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'runtime_smoke_provider:app',
        '--app-dir', 'tests/fixtures', '--host', '127.0.0.1', '--port', '8919'],
        cwd=ROOT/'dataagent-backend', stdout=(LOGS/'provider.log').open('wb'), stderr=subprocess.STDOUT)
    try:
        until(lambda: provider.poll() is None and subprocess.run(['curl', '-sf', 'http://127.0.0.1:8919/smoke-state'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0)
        until(lambda: subprocess.run(['curl', '-sf', BASE + '/api/v1/nl2sql/health'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0)
        main()
    finally:
        for topic in topics:
            try:
                active = request('GET', API+'/topics/'+topic).get('current_task_id')
                if active:
                    request('POST', f'{API}/tasks/{active}/cancel')
                    until(lambda: request('GET', f'{API}/tasks/{active}')['task_status'] in {'finished', 'error', 'suspended'})
                request('DELETE', API+'/topics/'+topic)
            except Exception as exc:
                print('Topic cleanup:', type(exc).__name__, file=sys.stderr)
        for path in ([f'{AGENTS}/agents/{agent}'] if agent else [])+[f'{ADMIN}/providers/{NAME}']:
            try:
                request('DELETE', path)
            except Exception:
                pass
        provider.terminate()
        provider.wait(timeout=10)
        print('Logs:', LOGS, flush=True)
