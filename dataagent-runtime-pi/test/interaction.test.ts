import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createAssistantMessageEventStream, type AssistantMessage, type JsonObject } from "@earendil-works/pi-ai";
import { Cell } from "../src/kernel/cell.js";
import { resolveModel } from "../src/providers/stream-fn-resolver.js";
import { sandboxFixture } from "./runtime-fixture.js";
import type { CellInitPayload, NeutralAgentEvent } from "../src/protocol/frames.js";

type Call = { name: string; arguments: JsonObject };
const usage = { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };
function scripted(turns: Call[][], inspect?: (messages: unknown[]) => void) {
  let turn = 0;
  const model = resolveModel("faux", "faux-1", "/v1/messages");
  return () => ({ model, streamFn: ((_model: unknown, context: { messages: unknown[] }) => {
    inspect?.(context.messages);
    const calls = turns[turn++] ?? [];
    const message: AssistantMessage = { role: "assistant", api: model.api, provider: model.provider,
      model: model.id, usage, timestamp: Date.now(), stopReason: calls.length ? "toolUse" : "stop",
      content: calls.length ? calls.map((c,i) => ({ type: "toolCall", id: `call-${turn}-${i}`, ...c })) : [{ type: "text", text: "done" }] };
    const stream = createAssistantMessageEventStream();
    queueMicrotask(() => { stream.push({ type: "done", reason: calls.length ? "toolUse" : "stop", message }); stream.end(); });
    return stream;
  }) as never });
}
function init(root: string, overrides: Partial<CellInitPayload> = {}): CellInitPayload {
  return { ...sandboxFixture(root), run_id: path.basename(root), task_id: "task-1", topic_id: "topic-1",
    system_prompt: "test", messages: [], history: [], prompt: "start", model: { provider_id: "faux", api_format: "/v1/messages", model_id: "faux-1" },
    workspace: { project_cwd: root }, skills: [], runtime_env: {},
    limits: { total_timeout_seconds: 30, idle_timeout_seconds: 10, max_turns: 10, max_tool_calls: 20, bash_timeout_seconds: 10 },
    ...overrides };
}
const question: Call = { name: "AskUserQuestion", arguments: { questions: [{ question: "Choose?", options: [{ label: "A" },{ label: "B" }] }] } };

test("a question ends the segment, preserves completed results, and resumes without replay", async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "oda-card-"));
  t.after(() => { fs.rmSync(root,{recursive:true,force:true}); fs.rmSync(root+"-results",{recursive:true,force:true}); });
  const first = new Cell(scripted([[{name:"bash",arguments:{command:"echo before >before.txt"}}, question,
    {name:"bash",arguments:{command:"echo after >after.txt"}}]]));
  const events: NeutralAgentEvent[] = [];
  const result = await first.run(init(root), e => events.push(e));
  assert.equal(result.terminal_status,"parked",result.error);
  assert.equal(result.interaction?.kind,"question");
  assert.equal(fs.readFileSync(path.join(root,"before.txt"),"utf8"),"before\n");
  assert.equal(fs.existsSync(path.join(root,"after.txt")),false);
  assert.ok(result.checkpoint);
  assert.match(JSON.stringify(result.checkpoint.entries),/Not executed: waiting for user/);
  assert.equal(events.some(e => e.type==="run.completed"),false);
  let restored: unknown[] = [];
  const next = new Cell(scripted([[]], messages => { restored = messages; }));
  const completed = await next.run(init(root,{run_id:"next-segment",session_checkpoint:result.checkpoint,prompt:"User chose A"}),()=>{});
  assert.equal(completed.terminal_status,"success",completed.error);
  assert.match(JSON.stringify(restored),/before.txt/);
  assert.match(JSON.stringify(restored),/User chose A/);
  assert.equal(fs.existsSync(path.join(root,"after.txt")),false);
});

test("plan approval is restored explicitly and gates write/edit across segments", async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "oda-plan-"));
  t.after(() => { fs.rmSync(root,{recursive:true,force:true}); fs.rmSync(root+"-results",{recursive:true,force:true}); });
  const submit = new Cell(scripted([[{name:"write",arguments:{path:"denied.txt",content:"no"}},
    {name:"ExitPlanMode",arguments:{plan:"# Plan\nCreate the file"}}]]));
  const result = await submit.run(init(root,{permission_mode:"plan"}),()=>{});
  assert.equal(result.terminal_status,"parked",result.error);
  assert.equal(fs.existsSync(path.join(root,"denied.txt")),false);
  const events: NeutralAgentEvent[] = [];
  const reject = new Cell(scripted([[{name:"write",arguments:{path:"still-denied.txt",content:"no"}}],[]]));
  const rejected = await reject.run(init(root,{run_id:"reject",permission_mode:"plan",plan_approved:false,
    session_checkpoint:result.checkpoint,prompt:"User rejected; revise"}), e=>events.push(e));
  assert.equal(rejected.terminal_status,"success",rejected.error);
  assert.equal(fs.existsSync(path.join(root,"still-denied.txt")),false);
  assert.ok(events.some(e=>e.type==="tool.denied"));
  const approve = new Cell(scripted([[{name:"write",arguments:{path:"approved.txt",content:"yes"}}],[]]));
  const approved = await approve.run(init(root,{run_id:"approve",permission_mode:"plan",plan_approved:true,
    session_checkpoint:result.checkpoint,prompt:"User approved this plan"}),()=>{});
  assert.equal(approved.terminal_status,"success",approved.error);
  assert.equal(fs.readFileSync(path.join(root,"approved.txt"),"utf8"),"yes");
});

test("a non-interactive run offers no interaction tools and never parks", async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "oda-noninteractive-"));
  t.after(() => { fs.rmSync(root,{recursive:true,force:true}); fs.rmSync(root+"-results",{recursive:true,force:true}); });
  let seen: unknown[] = [];
  const cell = new Cell(scripted([[question], []], messages => { seen = messages; }));
  const result = await cell.run(init(root,{interactive:false,permission_mode:"plan"}),()=>{});
  assert.equal(result.terminal_status,"success",result.error);
  assert.equal(result.interaction,undefined);
  // The model asked anyway; the tool is not registered, so the call failed and the run went on.
  assert.match(JSON.stringify(seen),/AskUserQuestion/);
  assert.doesNotMatch(JSON.stringify(seen),/Waiting for user/);
});

const MCP_WRITE_FIXTURE = String.raw`
const readline = require("node:readline");
const lines = readline.createInterface({ input: process.stdin });
const send = (message) => process.stdout.write(JSON.stringify(message) + "\n");
lines.on("line", (line) => {
  const message = JSON.parse(line);
  if (message.method === "initialize") {
    send({ jsonrpc: "2.0", id: message.id, result: { protocolVersion: "2025-03-26", capabilities: { tools: {} }, serverInfo: { name: "write-fixture", version: "1.0.0" } } });
  } else if (message.method === "tools/list") {
    send({
      jsonrpc: "2.0",
      id: message.id,
      result: {
        tools: [
          { name: "create_table", title: "创建表", description: "create table", annotations: { readOnlyHint: false }, inputSchema: { type: "object", properties: { name: { type: "string" } } } },
          { name: "drop_table", title: "删除表", description: "drop table", annotations: { readOnlyHint: false, destructiveHint: true }, inputSchema: { type: "object", properties: { name: { type: "string" } } } }
        ]
      }
    });
  } else if (message.method === "tools/call") {
    send({ jsonrpc: "2.0", id: message.id, result: { content: [{ type: "text", text: "executed:" + message.params.name + ":" + (message.params.arguments?.name || "") }] } });
  }
});`;

function writeMcpServer(name = "write-fixture") {
  return { name, type: "stdio", command: process.execPath, args: ["-e", MCP_WRITE_FIXTURE] };
}

test("a call skipped because another call in the same turn parked completes as not_executed, not an error", async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "oda-approve-"));
  t.after(() => { fs.rmSync(root,{recursive:true,force:true}); fs.rmSync(root+"-results",{recursive:true,force:true}); });
  const events: NeutralAgentEvent[] = [];
  const cell = new Cell(scripted([[{ name: "create_table", arguments: { name: "a" } }, { name: "create_table", arguments: { name: "b" } }]]));
  const parked = await cell.run(init(root, { mcp_servers: [writeMcpServer()] }), e => events.push(e));

  assert.equal(parked.terminal_status, "parked", parked.error);
  const completed = events.filter(e => e.type === "tool.completed");
  assert.equal(completed.length, 2);
  const [waiting, skipped] = completed;
  assert.equal(waiting.payload.awaiting_approval, true);
  assert.equal(skipped.payload.not_executed, true);
  assert.equal(skipped.payload.is_error, false);
  assert.equal(skipped.payload.awaiting_approval, undefined);
});

test("write approval marks awaiting_approval without error and resumes with original tool_call_id", async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "oda-approve-"));
  t.after(() => { fs.rmSync(root,{recursive:true,force:true}); fs.rmSync(root+"-results",{recursive:true,force:true}); });
  const server = writeMcpServer();
  const parkEvents: NeutralAgentEvent[] = [];
  const first = new Cell(scripted([[{ name: "create_table", arguments: { name: "dwd_orders" } }]]));
  const parked = await first.run(init(root, { mcp_servers: [server] }), e => parkEvents.push(e));

  assert.equal(parked.terminal_status, "parked", parked.error);
  assert.equal(parked.interaction?.kind, "permission");
  assert.equal(parked.interaction?.payload.tool_call_id, "call-1-0");
  assert.equal(parked.interaction?.request_id, `${path.basename(root)}:call-1-0`);

  const completed = parkEvents.find(e => e.type === "tool.completed" && e.payload.tool_call_id === "call-1-0");
  assert.ok(completed, "expected tool.completed for intercepted call");
  assert.equal(completed.payload.awaiting_approval, true);
  assert.equal(completed.payload.is_error, false);
  assert.equal(completed.payload.output, null);

  const resumeEvents: NeutralAgentEvent[] = [];
  const persistedOps: Array<{ requestId: string; result: unknown }> = [];
  const second = new Cell(scripted([[]]));
  const resumed = await second.run(
    init(root, {
      run_id: "segment-2",
      mcp_servers: [server],
      session_checkpoint: parked.checkpoint,
      prompt: JSON.stringify({ request_id: parked.interaction!.request_id, kind: "permission", user_response: { decision: "allow" } }),
      resume_operation: {
        request_id: parked.interaction!.request_id,
        payload: parked.interaction!.payload as never,
        operation_status: "executing",
      },
    }),
    e => resumeEvents.push(e),
    undefined,
    async (requestId, result) => { persistedOps.push({ requestId, result }); }
  );

  assert.equal(resumed.terminal_status, "success", resumed.error);
  assert.equal(persistedOps.length, 1);
  assert.equal(persistedOps[0].requestId, parked.interaction!.request_id);

  const resumedStart = resumeEvents.find(e => e.type === "tool.started");
  const resumedEnd = resumeEvents.find(e => e.type === "tool.completed");
  assert.equal(resumedStart?.payload.tool_call_id, "call-1-0");
  assert.equal(resumedStart?.payload.tool_title, "创建表");
  assert.equal(resumedEnd?.payload.tool_call_id, "call-1-0");
  assert.equal(resumedEnd?.payload.is_error, false);
  assert.equal(resumedEnd?.payload.awaiting_approval, undefined);
  assert.match(JSON.stringify(resumedEnd?.payload.output), /executed:create_table:dwd_orders/);
});

test("approved_tools skips approval for non-destructive writes but never for destructive tools", async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "oda-session-allow-"));
  t.after(() => { fs.rmSync(root,{recursive:true,force:true}); fs.rmSync(root+"-results",{recursive:true,force:true}); });
  const server = writeMcpServer();

  // First park once to obtain the exact server_identity hash.
  const probe = await new Cell(scripted([[{ name: "create_table", arguments: { name: "t0" } }]]))
    .run(init(root, { mcp_servers: [server] }), () => {});
  assert.equal(probe.terminal_status, "parked");
  const identity = String(probe.interaction?.payload.server_identity || "");
  assert.ok(identity.length > 0);

  // Non-destructive tool in approved_tools executes directly without parking.
  const allowEvents: NeutralAgentEvent[] = [];
  const allowed = await new Cell(scripted([[{ name: "create_table", arguments: { name: "t1" } }], []]))
    .run(init(root, { run_id: "allow-run", mcp_servers: [server], approved_tools: [`${identity}:create_table`] }), e => allowEvents.push(e));
  assert.equal(allowed.terminal_status, "success", allowed.error);
  const doneEvent = allowEvents.find(e => e.type === "tool.completed" && e.payload.tool_name === "create_table");
  assert.ok(doneEvent);
  assert.equal(doneEvent.payload.is_error, false);
  assert.equal(doneEvent.payload.awaiting_approval, undefined);
  assert.match(JSON.stringify(doneEvent.payload.output), /executed:create_table:t1/);

  // Destructive tool MUST still park even when listed in approved_tools.
  const destructiveEvents: NeutralAgentEvent[] = [];
  const destructive = await new Cell(scripted([[{ name: "drop_table", arguments: { name: "t1" } }]]))
    .run(init(root, { run_id: "destructive-run", mcp_servers: [server], approved_tools: [`${identity}:drop_table`] }), e => destructiveEvents.push(e));
  assert.equal(destructive.terminal_status, "parked", destructive.error);
  assert.equal(destructive.interaction?.kind, "permission");
  assert.equal(destructive.interaction?.payload.tool_name, "drop_table");
  const waitingEvent = destructiveEvents.find(e => e.type === "tool.completed" && e.payload.tool_name === "drop_table");
  assert.equal(waitingEvent?.payload.awaiting_approval, true);
  assert.equal(waitingEvent?.payload.is_error, false);
});
