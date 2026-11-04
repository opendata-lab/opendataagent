"""Fresh/reused volumes and rollback with the real backend images and Docker socket.

Requires oda-backend:test, oda-backend:rollback-test, oda-sandbox:test and the
local dataagent-mysql/odw-local-redis containers. Uses a disposable schema and
volumes, never the user's provider settings.
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
NAME='oda-deploy-smoke-'+uuid.uuid4().hex[:8]
SCHEMA=NAME.replace('-','_')
NETWORK=NAME+'-net'
SKILLS=NAME+'-skills'
WORK=Path(tempfile.mkdtemp(prefix=NAME,dir='/tmp'))
WORK.chmod(0o755)
created_topics=[]
BASE='http://127.0.0.1:8902'
API='/api/v1/nl2sql'
provider=None
backend_started=False
MYSQL=os.environ.get('DATAAGENT_SMOKE_MYSQL_CONTAINER','dataagent-mysql')
REDIS=os.environ.get('DATAAGENT_SMOKE_REDIS_CONTAINER','odw-local-redis')


def docker(*args,check=True):
    return subprocess.run(['docker',*args],check=check,capture_output=True,text=True).stdout.strip()


def request(method,path,**kwargs):
    response=httpx.request(method,BASE+path,timeout=20,**kwargs)
    if response.status_code>=400: raise RuntimeError(f'{response.status_code} {response.text[:500]}')
    return response.json() if response.content else None


def wait_ready():
    until=time.monotonic()+60
    while time.monotonic()<until:
        try:
            if httpx.get(BASE+API+'/health',timeout=1).status_code==200: return
        except httpx.HTTPError: pass
        time.sleep(.3)
    raise TimeoutError(docker('logs',NAME,check=False)[-4000:])


def start(image='oda-backend:test',legacy=False):
    global backend_started
    args=['run','-d','--name',NAME,'--network',NETWORK,'-p','127.0.0.1:8902:8900',
        '-e','MYSQL_HOST=smoke-mysql','-e','MYSQL_PORT=3306','-e','MYSQL_USER=dataagent','-e','MYSQL_PASSWORD=dataagent123',
        '-e',f'MYSQL_DATABASE={SCHEMA}','-e',f'SESSION_MYSQL_DATABASE={SCHEMA}',
        '-e','REDIS_HOST=smoke-redis','-e','REDIS_PORT=6379','-e','REDIS_DB=14',
        '-e','SKILLS_ROOT_DIR=/app/.claude/skills','-e','DATAAGENT_HOST_ROOT=/dataagent_runtime',
        '-e','DATAAGENT_RUNTIME_ROOT=/dataagent_runtime','-e','DATAAGENT_SANDBOX_IMAGE=oda-sandbox:test',
        '--mount',f'type=bind,src={WORK},dst=/dataagent_runtime',
        '--mount',f'type=volume,src={SKILLS},dst=/app/.claude/skills',
        '--mount',f'type=bind,src={os.environ.get("DATAAGENT_DOCKER_SOCKET", str(Path.home()/".docker/run/docker.sock") if sys.platform=="darwin" else "/var/run/docker.sock")},dst=/var/run/docker.sock']
    if legacy: args+=['--user','1000:1000']
    docker(*args,image)
    backend_started=True
    wait_ready()


def stop():
    global backend_started
    if backend_started:
        log=docker('logs',NAME,check=False)
        Path('/tmp/'+NAME+'.log').write_text(log)
        docker('rm','-f',NAME,check=False)
        backend_started=False


def run_liveness(topic):
    accepted=request('POST',API+'/tasks',json={'topic_id':topic,'message_type':'text','message_content':'SMOKE:LIVENESS',
        'execution_mode':'auto','provider_id':'local-smoke','model':'smoke'})
    task_id=accepted['task_id'];until=time.monotonic()+30
    while time.monotonic()<until:
        result=request('GET',API+'/tasks/'+task_id)
        if result['task_status'] in {'finished','error','suspended'}:
            assert result['task_status']=='finished',result
            break
        time.sleep(.2)
    else: raise TimeoutError(result)
    assert 'smoke-ok' in request('GET',API+f'/tasks/{task_id}/message')['content']
    return task_id


def main():
    global provider
    log=open('/tmp/'+NAME+'-provider.log','wb')
    provider=subprocess.Popen([sys.executable,'-m','uvicorn','runtime_smoke_provider:app','--app-dir','tests/fixtures',
        '--host','0.0.0.0','--port','8919'],cwd=ROOT/'dataagent-backend',stdout=log,stderr=subprocess.STDOUT)
    docker('network','create',NETWORK)
    docker('network','connect','--alias','smoke-mysql',NETWORK,MYSQL)
    docker('network','connect','--alias','smoke-redis',NETWORK,REDIS)
    docker('exec','-e','MYSQL_PWD=root123',MYSQL,'mysql','-uroot','-e',
        f"CREATE DATABASE {SCHEMA} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; GRANT ALL ON {SCHEMA}.* TO 'dataagent'@'%';")
    start()
    assert docker('exec',NAME,'id','-u')=='0' # docker exec defaults to image user; inspect service separately.
    service_identity=docker('exec',NAME,'sh','-c','grep -E "^(Uid|Gid|Groups):" /proc/1/status')
    assert 'Uid:\t1000\t1000\t1000\t1000' in service_identity,service_identity
    assert 'Gid:\t1000\t1000\t1000\t1000' in service_identity,service_identity
    assert docker('exec','--user','1000:1000',NAME,'test','-w','/dataagent_runtime')==''
    topic=request('POST',API+'/topics',json={'title':'Container smoke'})['topic_id']
    created_topics.append(topic)
    request('POST','/api/v1/nl2sql-admin/providers',json={'provider_id':'local-smoke','name':'Local fixture',
        'api_format':'/v1/chat/completions','enabled':True,'provider_enabled':True,'enabled_models':['smoke'],
        'custom_models':['smoke'],'api_key':'local-fixture-only','base_url':'http://host.docker.internal:8919'})
    first=run_liveness(topic)
    # Exercise daemon mount discovery through a real sandbox tool.
    accepted=request('POST',API+'/tasks',json={'topic_id':topic,'message_type':'text','message_content':'SMOKE:TOOLS',
        'execution_mode':'auto','provider_id':'local-smoke','model':'smoke'})
    task_id=accepted['task_id'];until=time.monotonic()+30
    while time.monotonic()<until:
        result=request('GET',API+'/tasks/'+task_id)
        if result['task_status'] in {'finished','error','suspended'}:
            assert result['task_status']=='finished',result
            break
        time.sleep(.2)
    else: raise TimeoutError(result)
    assert (WORK/topic/'workspace/output/count.txt').read_text()=='20001'
    print('PASS fresh volume: entrypoint, service UID, socket access, skills, real Pi, daemon mount mapping',flush=True)
    docker('exec','--user','1000:1000',NAME,'python','-c',
        "from pathlib import Path; p=Path('/app/.claude/skills/custom-smoke');p.mkdir();(p/'SKILL.md').write_text('---\\nname: custom-smoke\\ndescription: test\\n---\\nTest')")
    stop();start()
    docker('exec',NAME,'test','-f','/app/.claude/skills/custom-smoke/SKILL.md')
    assert request('GET',API+f'/tasks/{first}/message')['content']=='smoke-ok'
    run_liveness(topic)
    print('PASS reused volumes: migrations repeat, session import, retained messages and output',flush=True)
    legacy=WORK/topic/'workspace/.dataagent/results'
    docker('exec','--user','1000:1000',NAME,'python','-c',
        "from pathlib import Path; p=Path('/dataagent_runtime/"+topic+"/workspace/.dataagent/results');p.mkdir(parents=True);(p/'res_legacy.json').write_text('{\"_format\":\"dataagent_jsonl_v1\",\"total_rows\":1}\\n{\"id\":42}\\n')")
    docker('exec',NAME,'python','scripts/migrate_runtime_results.py')
    migrated=WORK/topic/'results/res_legacy.json'
    assert json.loads(migrated.read_text())['rows']==[{'id':42}]
    docker('exec',NAME,'python','scripts/migrate_runtime_results.py')
    assert (legacy/'res_legacy.json').exists()

    docker('exec',NAME,'python','scripts/prepare_runtime_rollback.py')
    stop();start('oda-backend:rollback-test',legacy=True)
    assert 'smoke-ok' in request('GET',API+f'/tasks/{first}/message')['content']
    assert (WORK/topic/'workspace/output/count.txt').read_text()=='20001'
    run_liveness(topic)
    print('PASS rollback image: newer tables ignored, messages/output retained, text-history follow-up works',flush=True)
    stop();start()
    run_liveness(topic)
    assert json.loads(migrated.read_text())['rows']==[{'id':42}]
    print('PASS re-upgrade: retained checkpoint table, fresh SDK state from full text history, result files unchanged',flush=True)


if __name__=='__main__':
    try:main()
    finally:
        if backend_started:
            for topic in created_topics:
                try: request("DELETE",API+"/topics/"+topic)
                except Exception: pass
        stop()
        if provider: provider.terminate();provider.wait(timeout=10)
        docker('network','disconnect',NETWORK,MYSQL,check=False)
        docker('network','disconnect',NETWORK,REDIS,check=False)
        docker('network','rm',NETWORK,check=False)
        docker('volume','rm',SKILLS,check=False)
        docker('exec','-e','MYSQL_PWD=root123',MYSQL,'mysql','-uroot','-e',f'DROP DATABASE IF EXISTS {SCHEMA}',check=False)
        print('Deployment logs:', '/tmp/'+NAME+'.log',flush=True)
