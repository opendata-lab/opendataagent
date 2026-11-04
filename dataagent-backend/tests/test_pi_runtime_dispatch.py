"""Pi-only dispatch, identity, credentials and cumulative execution budgets."""
from pathlib import Path
import sys
import pytest
from pydantic import ValidationError
sys.path.insert(0,str(Path(__file__).parents[1]))
from config import Settings,resolve_runtime_kind
from core import task_executor as executor
from core.agent_identity import current_headers
from core.pi_runtime import PiRunOutcome
from core.task_executor import TaskExecutionInput


def test_only_pi_is_accepted():
    assert resolve_runtime_kind(Settings(_env_file=None))=='pi_agent_core'
    for value in ('claude_code','typo'):
        with pytest.raises(ValidationError): Settings(_env_file=None,dataagent_runtime_kind=value)


@pytest.fixture
def setup(monkeypatch,tmp_path):
    calls={}
    class Store:
        saved=None
        def get_session_checkpoint(self,_): return self.saved
        def record_task_system_prompt(self,*args): pass
        def commit_session_segment(self,**kw): calls['commit']=kw
    store=Store()
    monkeypatch.setattr(executor,'get_topic_task_store',lambda:store)
    monkeypatch.setattr(executor,'resolve_runtime_provider_selection',lambda *a:{'provider_id':'local-test','model':'stub','api_format':'/v1/chat/completions','api_key':'control-only-token','base_url':'http://127.0.0.1:9999'})
    monkeypatch.setattr(executor,'resolve_enabled_skill_runtime',lambda:{})
    monkeypatch.setattr(executor,'resolve_agent_skill_runtime',lambda *a:{'primary_folder':'sample','enabled_roots':{'sample':str(tmp_path/'skill')}})
    monkeypatch.setattr(executor,'prepare_topic_workspace',lambda *a,**kw:tmp_path)
    monkeypatch.setattr(executor,'_build_system_prompt',lambda *a:'system')
    def servers(*a,**kw): calls['identity']=current_headers(); return {}
    monkeypatch.setattr(executor,'_build_mcp_servers',servers)
    monkeypatch.setattr(executor,'build_sandbox_config',lambda cfg,**kw:({'runId':kw['run_id']},tmp_path/'results'))
    params=TaskExecutionInput(task_id='task-test',topic_id='topic-test',question='hello',history=[{'role':'user','content':'old'}],
        resume_session_id=None,provider_id='local-test',model='stub',database_hint=None,execution_mode='auto',
        agent_snapshot={'agent_id':'agent_default','name':'Test','skill_folders':[],'mcp_server_ids':[]},auth_user_id='local:tester')
    return calls,store,params


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome,expected',[('success','finished'),('failed','error'),('cancelled','suspended'),('parked','parked')])
async def test_outcomes_and_checkpoint_transaction(setup,monkeypatch,outcome,expected):
    calls,store,params=setup
    async def execute(ctx,**kw):
        calls['ctx']=ctx
        if outcome in {'success','parked'}:
            request={'request_id':'r1','kind':'question','payload':{'questions':[]}} if outcome=='parked' else None
            await kw['segment_handler']({'header':{},'entries':[],'leaf_id':None,'turns':2,'tool_calls':3},request,2.5)
        return PiRunOutcome(terminal_status=outcome,answer='answer',error_code='test_error' if outcome=='failed' else '')
    monkeypatch.setattr(executor,'execute_pi_run',execute)
    result=await executor.execute_task_stream(params,emit=lambda _:None)
    assert result.task_status==expected
    ctx=calls['ctx']; assert ctx.history==params.history
    assert ctx.to_init_payload()['sandbox']['runId']==ctx.run_id
    assert 'control-only-token' not in str(ctx.to_init_payload())
    assert calls['identity']=={'X-Agent-User-Id':'local:tester','X-Agent-Id':'agent_default'}
    assert ctx.runtime_env['DATAAGENT_PYTHON_BIN']=='/usr/local/bin/python'
    assert ctx.runtime_env['SKILLS_ROOT_DIR']=='/opt/skills'
    assert 'MYSQL_PASSWORD' not in ctx.runtime_env
    if outcome in {'success','parked'}:
        assert calls['commit']['state']['usage']=={'elapsed_seconds':2.5,'turns':2,'tool_calls':3}
        assert bool(calls['commit']['card'])==(outcome=='parked')


@pytest.mark.asyncio
async def test_resume_subtracts_task_budget_and_does_not_replay_history(setup,monkeypatch):
    calls,store,params=setup
    store.saved={'task_id':params.task_id,'version':4,'header':{},'entries':[],'leaf_id':None,'state':{
        'usage':{'elapsed_seconds':100,'turns':5,'tool_calls':8},'plan_approved':False,
        'interaction':{'request_id':'r1','kind':'question','result':{'answers':[{'selected':['A']}]}}}}
    async def execute(ctx,**kw): calls['ctx']=ctx; return PiRunOutcome(terminal_status='success',answer='continued')
    monkeypatch.setattr(executor,'execute_pi_run',execute)
    await executor.execute_task_stream(params,emit=lambda _:None)
    ctx=calls['ctx']; assert ctx.total_timeout_seconds==1700
    assert ctx.max_turns==35 and ctx.max_tool_calls==192 and ctx.history==[]
    assert '"request_id": "r1"' in ctx.prompt
    store.saved['state']['usage']['elapsed_seconds']=1800
    with pytest.raises(ValueError,match='budget exhausted'):
        await executor.execute_task_stream(params,emit=lambda _:None)
