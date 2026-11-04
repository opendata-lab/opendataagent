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
