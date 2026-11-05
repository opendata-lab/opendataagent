"""Credential-free HTTP smoke using real Pi, Docker, MySQL, Redis and local fixtures.

Run after building Pi and oda-sandbox:test, with local MySQL/Redis running and
port 8900 free. Creates and deletes only its own providers, MCP server, agents
and topics. Logs stay in /tmp/dataagent-http-smoke-*.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
import httpx

ROOT=Path(__file__).resolve().parents[2]
BACKEND=ROOT/'dataagent-backend'
PORT=os.environ.get('DATAAGENT_SMOKE_PORT','8900')
BASE='http://127.0.0.1:'+PORT
API='/api/v1/nl2sql'
ADMIN='/api/v1/nl2sql-admin'
AGENTS='/api/v1/dataagent'
SUFFIX=uuid.uuid4().hex[:10]
PROVIDER='smoke-'+SUFFIX
MCP='smoke-mcp-'+SUFFIX
LOGS=Path(tempfile.mkdtemp(prefix='dataagent-http-smoke-',dir='/tmp'))
STATE=LOGS/'writes.json'
processes=[]
topics=[]
agent_id=None
backend=None
client=httpx.Client(base_url=BASE,timeout=20)


def request(method,path,**kwargs):
    result=client.request(method,path,**kwargs)
    if result.status_code>=400: raise RuntimeError(f'{method} {path}: {result.status_code} {result.text[:500]}')
    return result.json() if result.content else None


def spawn(name,args,env=None):
    process=subprocess.Popen(args,cwd=BACKEND,env=env or os.environ.copy(),stdout=(LOGS/(name+'.log')).open('ab'),stderr=subprocess.STDOUT)
    processes.append(process)
    return process


def stop(process):
    if process and process.poll() is None:
        process.terminate()
        try: process.wait(timeout=15)
        except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)


def wait_http(url):
    until=time.monotonic()+30
    while time.monotonic()<until:
        try:
            if httpx.get(url,timeout=1).status_code<500: return
        except httpx.HTTPError: pass
        time.sleep(.2)
    raise TimeoutError(url)


def start_backend():
    global backend
    env=os.environ.copy()
    env.pop('DATAAGENT_CONFIG',None)
    env.update(MYSQL_HOST='127.0.0.1',MYSQL_PORT='3326',MYSQL_USER='dataagent',MYSQL_PASSWORD='dataagent123',
        SESSION_MYSQL_DATABASE=os.environ.get('DATAAGENT_SMOKE_SCHEMA','dataagent'),REDIS_HOST='127.0.0.1',REDIS_PORT='6379',
        SKILLS_ROOT_DIR=str(ROOT/'.claude/skills'),DATAAGENT_HOST_ROOT=str(ROOT),
        DATAAGENT_RUNTIME_PI_DIR=str(ROOT/'dataagent-runtime-pi'),DATAAGENT_SANDBOX_IMAGE='oda-sandbox:test',
        DATAAGENT_NODE_BIN=os.environ.get('DATAAGENT_NODE_BIN',str(Path.home()/'.nvm/versions/node/v22.22.0/bin/node')),
        AGENT_BACKGROUND_TIMEOUT_SECONDS='60',AGENT_BACKGROUND_BASH_TIMEOUT_SECONDS='2',DATAAGENT_RUN_IDLE_TIMEOUT_SECONDS='5')
    backend=spawn('backend',[sys.executable,'-m','uvicorn','main:app','--host','127.0.0.1','--port',PORT],env)
    wait_http(BASE+API+'/health')


def records(task_id): return request('GET',API+f'/tasks/{task_id}/sdk-events?limit=500')['records']


def wait_task(task_id,statuses={'finished','error','suspended'},seconds=30):
    until=time.monotonic()+seconds
    observed=[]
    while time.monotonic()<until:
        task=request('GET',API+f'/tasks/{task_id}')
        status=task['task_status']
        if not observed or status!=observed[-1]: observed.append(status)
        if status in statuses: return task,observed
        time.sleep(.1)
    raise TimeoutError(f'{task_id}: {observed}')


def submit(scenario,mode='default'):
    topic=request('POST',API+'/topics',json={'title':'SMOKE:'+scenario,'agent_id':agent_id,'permission_mode':mode})
    topics.append(topic['topic_id'])
    accepted=request('POST',API+'/tasks',json={'topic_id':topic['topic_id'],'message_type':'text','message_content':'SMOKE:'+scenario,
        'execution_mode':'auto','provider_id':PROVIDER,'model':'smoke','agent_id':agent_id})
    assert accepted['accepted'] and accepted['task_id']
    return topic['topic_id'],accepted['task_id']


def card(task_id,kind): return next(r['data'] for r in reversed(records(task_id)) if r['record_type']==kind)


def assert_finished(task_id,text):
    result,observed=wait_task(task_id)
    assert result['task_status']=='finished',(result,observed)
    message=request('GET',API+f'/tasks/{task_id}/message')
    assert text in message['content'],message
    with client.stream('GET',API+f'/tasks/{task_id}/sdk-events/stream',timeout=10) as stream:
        stream.raise_for_status(); events=[json.loads(line[6:]) for line in stream.iter_lines() if line.startswith('data: ')]
    assert any(e['event_type']=='run.completed' for e in events)
    print(f'PASS {text}: accepted, {observed}, persisted message, terminal SSE',flush=True)


def write_count(): return len(json.loads(STATE.read_text())) if STATE.exists() else 0


def main():
    global agent_id
    spawn('provider',[sys.executable,'-m','uvicorn','runtime_smoke_provider:app','--app-dir','tests/fixtures','--host','127.0.0.1','--port','8919'])
    env=os.environ.copy();env['DATAAGENT_SMOKE_STATE']=str(STATE)
    spawn('mcp',[sys.executable,'tests/fixtures/runtime_smoke_mcp.py'],env)
    wait_http('http://127.0.0.1:8919/docs');wait_http('http://127.0.0.1:8920/mcp')
    start_backend()
    request('POST',ADMIN+'/providers',json={'provider_id':PROVIDER,'name':'Local smoke fixture','api_format':'/v1/chat/completions',
        'enabled':True,'provider_enabled':True,'enabled_models':['smoke'],'custom_models':['smoke'],
        'api_key':'local-fixture-only','base_url':'http://127.0.0.1:8919'})
    request('POST',AGENTS+'/mcp/servers',json={'server_id':MCP,'name':'Local smoke MCP','transport':'http','url':'http://127.0.0.1:8920/mcp','enabled':True})
    draft=request('POST',AGENTS+'/agents',json={'name':'Runtime smoke '+SUFFIX,'skill_folders':['chart-visualization','report-generation'], 'mcp_server_ids':[MCP]})
    assert 'allowed_tools' not in draft
    agent_id=draft['agent_id']
    request('POST',AGENTS+f'/agents/{agent_id}/publish',json={'expected_revision':draft['revision']})
    _,task=submit('LIVENESS');assert_finished(task,'smoke-ok')
    topic,task=submit('TOOLS');assert_finished(task,'tools-ok')
    assert (ROOT/topic/'workspace/output/count.txt').read_text()=='20001'
    topic,task=submit('MCP_READ');assert_finished(task,'mcp-read-ok')
    assert (ROOT/topic/'workspace/output/rows.txt').read_text()=='1000'
    from zipfile import ZipFile
    from xml.etree import ElementTree
    with ZipFile(ROOT/topic/'workspace/output/full-report.xlsx') as workbook:
        sheet=ElementTree.fromstring(workbook.read('xl/worksheets/sheet1.xml'))
        assert len(sheet.findall('.//{*}sheetData/{*}row'))==1001
    persisted=json.dumps(records(task),ensure_ascii=False)
    assert 'report_generation' in persisted and 'chart_spec' in persisted
    assert 'Smoke full chart' in persisted
    print('PASS bundled skills: complete 1000-row Excel and chart persisted through real sandbox Bash',flush=True)
    raw=[json.loads(p.read_text()) for p in (ROOT/topic/'results').glob('*.json')]
    assert any(isinstance(d,dict) and d.get('warning')=='metadata-preserved' and len(d.get('rows',[]))==1000 for d in raw)
    topic,task=submit('QUESTION');waiting,_=wait_task(task,{'waiting_input'})
    question=card(task,'question_request');assert not (ROOT/topic/'workspace/after.txt').exists()
    _,other=submit('LIVENESS');assert_finished(other,'smoke-ok')
    stop(backend);start_backend()
    reply={'request_id':question['request_id'],'answers':[{'question':'Choose?','selected':['A']}]}
    assert request('POST',API+f'/tasks/{task}/question-answer',json=reply)['accepted']
    assert request('POST',API+f'/tasks/{task}/question-answer',json=reply)['accepted']
    assert_finished(task,'resumed-ok');assert not (ROOT/topic/'workspace/after.txt').exists()
    assistant=request('GET',API+f'/tasks/{task}/message')
    attached={a['rel_path'] for a in assistant['attachments']}
    assert {'output/before-card.txt','output/after-card.txt'} <= attached, attached
    usage_events=[r['data']['usage'] for r in records(task) if r['event_type']=='usage.updated']
    for field in ('input_tokens','output_tokens'):
        assert assistant['usage'][field] == sum(u[field] for u in usage_events), (assistant['usage'],usage_events)
    print('PASS question restart, slot release, duplicate answer, no batch replay, attachments and token totals across segments',flush=True)
    for scenario,decision in [('PLAN','allow'),('PLAN_REJECT','deny')]:
        topic,task=submit(scenario,'plan');wait_task(task,{'waiting_permission'})
        permission=card(task,'permission_request')
        request('POST',API+f'/tasks/{task}/permission-decision',json={'request_id':permission['request_id'],'decision':decision})
        assert_finished(task,'plan-ok')
        assert (ROOT/topic/'workspace/approved.txt').exists()==(decision=='allow')
        assert not (ROOT/topic/'workspace/denied-before-plan.txt').exists()
    for scenario,decision in [('MCP_WRITE','allow'),('MCP_DENY','deny')]:
        before=write_count();_,task=submit(scenario);wait_task(task,{'waiting_permission'})
        permission=card(task,'permission_request');stop(backend);start_backend()
        reply={'request_id':permission['request_id'],'decision':decision}
        request('POST',API+f'/tasks/{task}/permission-decision',json=reply)
        request('POST',API+f'/tasks/{task}/permission-decision',json=reply)
        assert_finished(task,'write-resumed-ok');assert write_count()==before+(decision=='allow')
    _,task=submit('MCP_ERROR');wait_task(task,{'waiting_permission'})
    permission=card(task,'permission_request')
    request('POST',API+f'/tasks/{task}/permission-decision',json={'request_id':permission['request_id'],'decision':'allow'})
    assert_finished(task,'write-error-reported')
    error_tools=[r for r in records(task) if r['event_type']=='tool.completed' and r['data'].get('is_error')]
    assert any('Validation rejected' in json.dumps(r['data']) for r in error_tools)
    print('PASS explicit MCP server error acknowledged and reported without unknown result',flush=True)
    before=write_count();_,task=submit('MCP_UNKNOWN');wait_task(task,{'waiting_permission'})
    permission=card(task,'permission_request')
    request('POST',API+f'/tasks/{task}/permission-decision',json={'request_id':permission['request_id'],'decision':'allow'})
    until=time.monotonic()+10
    while write_count()==before and time.monotonic()<until: time.sleep(.1)
    assert write_count()==before+1
    stop(backend);start_backend()
    result,_=wait_task(task);assert result['task_status']=='suspended',result
    assert write_count()==before+1
    print('PASS unknown external write is suspended without replay',flush=True)
    _,task=submit('CANCEL');wait_task(task,{'running'})
    request('POST',API+f'/tasks/{task}/cancel');result,_=wait_task(task);assert result['task_status']=='suspended',result
    _,task=submit('BASH_TIMEOUT');result,_=wait_task(task);assert result['task_status']=='error',result
    assert 'SANDBOX_COMMAND_TIMEOUT' in str(result)
    _,task=submit('IDLE');result,_=wait_task(task);assert result['task_status']=='error',result
    assert 'PI_RUN_IDLE_TIMEOUT' in str(result)
    assert not subprocess.check_output(['docker','ps','-aq','--filter','name=^/oda-sandbox-']).strip()
    print('PASS cancel, command timeout, idle timeout, sandbox cleanup',flush=True)


if __name__=='__main__':
    try:
        main()
    finally:
        if backend and backend.poll() is None:
            for topic in topics:
                try: request('DELETE',API+'/topics/'+topic)
                except Exception as error: print('cleanup topic:',error,file=sys.stderr)
            for path in ([AGENTS+f'/agents/{agent_id}'] if agent_id else [])+[AGENTS+'/mcp/servers/'+MCP,ADMIN+'/providers/'+PROVIDER]:
                try: request('DELETE',path)
                except Exception: pass
        for process in reversed(processes): stop(process)
        print('Logs:',LOGS,flush=True)
