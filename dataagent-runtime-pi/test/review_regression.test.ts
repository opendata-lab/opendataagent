import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import { createAssistantMessageEventStream, type AssistantMessage } from "@earendil-works/pi-ai";
import { Cell } from "../src/kernel/cell.js";
import { resolveModel } from "../src/providers/stream-fn-resolver.js";
import { sandboxFixture } from "./runtime-fixture.js";
import type { CellInitPayload, McpServerConfig, NeutralAgentEvent } from "../src/protocol/frames.js";

function mcpFixture(outcome: "success" | "error" | "transport" | "large"): McpServerConfig {
  const script = `
const readline = require('node:readline');
readline.createInterface({input:process.stdin}).on('line', line => {
  const req=JSON.parse(line); let result;
  if(req.method==='initialize') result={protocolVersion:'2025-03-26',capabilities:{tools:{}},serverInfo:{name:'review',version:'1'}};
  else if(req.method==='tools/list') result={tools:[{name:'operation',inputSchema:{type:'object'},annotations:{readOnlyHint:${outcome === "large"}}}]};
  else if(req.method==='tools/call') {
    if(${JSON.stringify(outcome)}==='transport') return process.exit(1);
    result={isError:${outcome === "error"},content:[{type:'text',text:${outcome === "large" ? "JSON.stringify({rows:Array.from({length:800},(_,id)=>({id,value:'x'.repeat(80)})),warning:'metadata-kept'})" : JSON.stringify(outcome === "error" ? "Validation rejected: invalid value" : "operation-ok")}}]};
  }
  if(req.id!==undefined && result) process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:req.id,result})+'\\n');
});`;
  return { name: "review", type: "stdio", command: process.execPath, args: ["-e", script] };
}

function init(root: string, server: McpServerConfig): CellInitPayload {
  return { ...sandboxFixture(root), run_id: "review", task_id: "task-review", topic_id: "topic-review",
    system_prompt: "test", messages: [], prompt: "continue", model: {provider_id:"faux",model_id:"faux-1",api_format:"/v1/messages"},
    workspace: {project_cwd:root}, skills: [], runtime_env: {}, mcp_servers: [server], turn_offset: 4,
    limits: {total_timeout_seconds:20,idle_timeout_seconds:10,max_turns:10,max_tool_calls:10,bash_timeout_seconds:10} };
}
function modelFactory(onContext: (text:string)=>void, callTool = false) {
  let turn=0;
  const model=resolveModel("faux","faux-1","/v1/messages");
  return () => ({model, streamFn: ((_m:unknown, ctx:unknown) => {
    onContext(JSON.stringify(ctx));
    const call=callTool && turn++===0;
    const message: AssistantMessage={role:"assistant",api:model.api,provider:model.provider,model:model.id,timestamp:Date.now(),
      stopReason:call?"toolUse":"stop",usage:{input:1,output:1,cacheRead:0,cacheWrite:0,totalTokens:2,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}},
      content:call?[{type:"toolCall",id:"fold-call",name:"operation",arguments:{}}]:[{type:"text",text:"done"}]};
    const stream=createAssistantMessageEventStream();
    queueMicrotask(()=>{stream.push({type:"done",reason:call?"toolUse":"stop",message});stream.end();});
    return stream;
  }) as never});
}

for(const outcome of ["success","error","transport"] as const) {
  test(`approved MCP ${outcome}: persist known results and keep transport outcome unknown`, async t => {
    const root=fs.mkdtempSync(path.join(os.tmpdir(),"oda-review-"));
    t.after(()=>{fs.rmSync(root,{recursive:true,force:true});fs.rmSync(root+"-results",{recursive:true,force:true});});
    const server=mcpFixture(outcome); const payload=init(root,server);
    payload.resume_operation={request_id:"approved-1",operation_status:"executing",payload:{tool_name:"operation",server_name:server.name,
      server_identity:createHash("sha256").update(JSON.stringify(server)).digest("hex"),arguments:{}}};
    let saved:any;let context="";const events:NeutralAgentEvent[]=[];
    const result=await new Cell(modelFactory(text=>{context=text;})).run(payload,e=>events.push(e),()=>{},async(_id,value)=>{saved=value;});
    if(outcome==="transport") {
      assert.equal(result.terminal_status,"failed");assert.equal(saved,undefined);assert.match(result.error!,/MCP_TRANSPORT_ERROR/);
      assert.equal(events.filter(e=>e.type==="tool.completed").length,0);
    } else {
      assert.equal(result.terminal_status,"success",result.error);assert.equal(Boolean(saved.isError),outcome==="error");
      assert.match(context,outcome==="error"?/Validation rejected/:/operation-ok/);
      const started=events.findIndex(e=>e.type==="tool.started");
      assert.equal(events[started-1].type,"turn.started");
      const completed=events.find(e=>e.type==="tool.completed")!;
      assert.equal(completed.payload.is_error,outcome==="error");assert.equal(completed.payload.turn_id,"turn-5");
      assert.equal(events[started].payload.turn_id,"turn-5");
      assert.equal(events.filter(e=>e.type==="turn.started").length,1);
      assert.equal(events.find(e=>e.type==="turn.completed")?.payload.turn_id,"turn-5");
      assert.equal(result.turns,1,"approved call must not consume an extra model turn");
    }
  });
}

test("MCP folding passes full output through afterToolCall and the UI registry", async t => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),"oda-review-fold-"));
  t.after(()=>{fs.rmSync(root,{recursive:true,force:true});fs.rmSync(root+"-results",{recursive:true,force:true});});
  const contexts:string[]=[];const events:NeutralAgentEvent[]=[];
  const result=await new Cell(modelFactory(text=>contexts.push(text),true)).run(init(root,mcpFixture("large")),e=>events.push(e));
  assert.equal(result.terminal_status,"success",result.error);
  const completed=events.find(e=>e.type==="tool.completed")!;
  assert.match(JSON.stringify(completed.payload.output),/x{80}/);
  assert.match(JSON.stringify(completed.payload.output),/metadata-kept/);
  const fold=(completed.payload.output_meta as any).fold;
  assert.ok(fold.result_ref);assert.ok(fold.original_bytes>16384);
  assert.match(contexts.at(-1)!,/\/mnt\/results\/res_/);
  assert.ok(contexts.at(-1)!.length < JSON.stringify(completed.payload.output).length / 2,"model sees the digest while UI sees raw rows");
  const files=fs.readdirSync(root+"-results");assert.equal(files.length,1);
  const raw=JSON.parse(fs.readFileSync(path.join(root+"-results",files[0]),"utf8"));assert.equal(raw.rows.length,800);
});
