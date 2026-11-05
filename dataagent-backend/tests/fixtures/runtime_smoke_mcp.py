"""Real MCP HTTP transport with deterministic read/write side effects."""
import asyncio
import json
import os
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp=FastMCP('runtime-smoke',host='127.0.0.1',port=8920)
state=Path(os.environ['DATAAGENT_SMOKE_STATE'])

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False))
def smoke_read_large()->dict:
    return {'kind':'query_result','rows':[{'id':i,'value':'x'*80} for i in range(1000)],'total':1000,'truncated':False,'warning':'metadata-preserved'}

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False))
async def smoke_write_record(value:str,hold:bool=False)->dict:
    entries=json.loads(state.read_text()) if state.exists() else []
    entries.append(value);state.write_text(json.dumps(entries))
    if hold: await asyncio.sleep(60)
    return {'ok':True,'count':len(entries)}

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False))
def smoke_write_rejected()->dict:
    raise ValueError('Validation rejected: invalid smoke value')

if __name__=='__main__': mcp.run(transport='streamable-http')
