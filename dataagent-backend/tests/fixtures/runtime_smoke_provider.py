"""Deterministic OpenAI-compatible local provider, with no external credentials."""
import asyncio
import json
import re
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app=FastAPI()

@app.post('/v1/chat/completions')
async def complete(request:Request):
    payload=await request.json()
    messages=payload.get('messages',[])
    transcript=json.dumps(messages,ensure_ascii=False)
    matches=re.findall(r'SMOKE:([A-Z_]+)',transcript)
    scenario=matches[-1] if matches else 'LIVENESS'
    tools=[m for m in messages if m.get('role')=='tool']
    calls=[]; answer='smoke-ok'
    def call(name,**args): calls.append({'id':f'call-{len(tools)}-{len(calls)}','type':'function','function':{'name':name,'arguments':json.dumps(args)}})
    if scenario=='TOOLS':
        if not tools:
            call('write',path='sample.txt',content='alpha\nbeta\n')
            call('edit',path='sample.txt',edits=[{'oldText':'beta','newText':'gamma'}])
            call('read',path='sample.txt');call('ls',path='.')
            call('find',pattern='*.txt');call('grep',pattern='gamma')
            call('bash',command='python -c \'print("x"*20000)\'')
        elif len(tools)==7:
            file=re.search(r'/mnt/results/res_[a-z0-9]+\.txt',json.dumps(tools[-1]))[0]
            call('bash',command=f'python -c \'from pathlib import Path; p=Path("{file}"); Path("output").mkdir(exist_ok=True); Path("output/count.txt").write_text(str(len(p.read_text()))); print(len(p.read_text()))\'')
        else: answer='tools-ok'
    elif scenario=='QUESTION':
        if not tools:
            call('bash',command='echo before >before.txt')
            call('AskUserQuestion',questions=[{'question':'Choose?','options':[{'label':'A','recommended':True},{'label':'B'}]}])
            call('bash',command='echo forbidden >after.txt')
        else: answer='resumed-ok'
    elif scenario in {'PLAN','PLAN_REJECT'}:
        if not tools:
            call('write',path='denied-before-plan.txt',content='forbidden')
            call('ExitPlanMode',plan='# Smoke plan\nCreate the approved file')
        elif not any('approved.txt' in json.dumps(m) for m in messages):
            call('write',path='approved.txt',content='approved')
        else: answer='plan-ok'
    elif scenario=='MCP_READ':
        if not tools: call('smoke_read_large')
        elif len(tools)==1:
            file=re.search(r'/mnt/results/res_[a-z0-9]+\.json',json.dumps(tools[-1]))[0]
            call('bash',command=f'python -c \'import json; from pathlib import Path; d=json.loads(Path("{file}").read_text()); Path("output").mkdir(exist_ok=True); Path("output/rows.txt").write_text(str(len(d["rows"]))); print(len(d["rows"]))\'')
            call('bash',command=f'"$DATAAGENT_PYTHON_BIN" "${{SKILLS_ROOT_DIR}}/report-generation/scripts/generate_report.py" --input-file {file} --output output/full-report.xlsx --title "Smoke full data"')
            call('bash',command=f'"$DATAAGENT_PYTHON_BIN" "${{SKILLS_ROOT_DIR}}/chart-visualization/scripts/build_chart_spec.py" --input-file {file} --chart-type line --x-field id --y-field id --title "Smoke full chart"')
        else: answer='mcp-read-ok'
    elif scenario in {'MCP_WRITE','MCP_DENY','MCP_UNKNOWN'}:
        if not tools: call('smoke_write_record',value=scenario,hold=scenario=='MCP_UNKNOWN')
        else: answer='write-resumed-ok'
    elif scenario=='BASH_TIMEOUT':
        if not tools: call('bash',command='sleep 20 & wait')
    async def stream():
        if scenario in {'IDLE','CANCEL'}: await asyncio.sleep(60)
        base={'id':'local-smoke','object':'chat.completion.chunk','created':1,'model':payload.get('model','smoke')}
        yield 'data: '+json.dumps({**base,'choices':[{'index':0,'delta':{'role':'assistant'},'finish_reason':None}]})+'\n\n'
        if calls:
            for i,c in enumerate(calls):
                yield 'data: '+json.dumps({**base,'choices':[{'index':0,'delta':{'tool_calls':[{'index':i,**c}]},'finish_reason':None}]})+'\n\n'
            finish='tool_calls'
        else:
            yield 'data: '+json.dumps({**base,'choices':[{'index':0,'delta':{'content':answer},'finish_reason':None}]})+'\n\n'
            finish='stop'
        yield 'data: '+json.dumps({**base,'choices':[{'index':0,'delta':{},'finish_reason':finish}],'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}})+'\n\n'
        yield 'data: [DONE]\n\n'
    return StreamingResponse(stream(),media_type='text/event-stream')
