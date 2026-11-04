import test from "node:test";
import assert from "node:assert/strict";
import { Type, InMemoryCredentialStore, createAssistantMessageEventStream, type AssistantMessage } from "@earendil-works/pi-ai";
import { getModel } from "@earendil-works/pi-ai/compat";
import {
  createAgentSession, createExtensionRuntime, ModelRuntime,
  SessionManager, SettingsManager, type ResourceLoader, type ToolDefinition,
} from "@earendil-works/pi-coding-agent";

// No disk discovery: the production adapter must supply every resource.
const loader: ResourceLoader = {
  getExtensions: () => ({ extensions: [], errors: [], runtime: createExtensionRuntime() }),
  getSkills: () => ({ skills: [], diagnostics: [] }),
  getPrompts: () => ({ prompts: [], diagnostics: [] }),
  getThemes: () => ({ themes: [], diagnostics: [] }),
  getAgentsFiles: () => ({ agentsFiles: [] }),
  getSystemPrompt: () => "SDK contract probe",
  getSystemPromptSource: () => undefined,
  getAppendSystemPrompt: () => [],
  getAppendSystemPromptSources: () => [],
  extendResources: () => {},
  reload: async () => {},
};

const names = ["bash", "read", "write", "edit", "ls", "find", "grep"];
async function makeSession(tools: string[], customTools?: ToolDefinition[], manager = SessionManager.inMemory()) {
  const modelRuntime = await ModelRuntime.create({
    credentials: new InMemoryCredentialStore(), modelsPath: null, refreshOnCreate: false,
  });
  // All requests below use an injected stream; this never leaves the process.
  await modelRuntime.setRuntimeApiKey("anthropic", "sdk-probe-placeholder");
  return createAgentSession({
    cwd: process.cwd(),
    model: getModel("anthropic", "claude-sonnet-4-5"),
    modelRuntime,
    sessionManager: manager,
    settingsManager: SettingsManager.inMemory({ compaction: { enabled: false } }),
    resourceLoader: loader,
    tools,
    customTools: customTools ?? names.map(name => ({
      name, label: name, description: "Probe custom implementation",
      parameters: Type.Object({}),
      execute: async () => ({ content: [{ type: "text" as const, text: "custom:" + name }], details: {} }),
    })),
  });
}

test("SDK empty allowlist disables custom tools as well as builtins", async () => {
  const { session } = await makeSession([]);
  try { assert.deepEqual(session.getActiveToolNames(), []); }
  finally { session.dispose(); }
});

test("card closes a complete tool batch and restored session accepts the answer without replay", async () => {
  let parked = false;
  const executed: string[] = [];
  const definitions: ToolDefinition[] = ["read", "AskUserQuestion", "write"].map(name => ({
    name, label: name, description: name, parameters: Type.Object({}),
    execute: async () => {
      executed.push(name);
      if (name === "AskUserQuestion") parked = true;
      return { content: [{ type: "text", text: parked ? "Waiting for user" : "completed read" }], details: {} };
    },
  }));
  const manager = SessionManager.inMemory();
  const { session } = await makeSession(definitions.map(t => t.name), definitions, manager);
  const model = session.agent.state.model;
  let requests = 0;
  session.agent.toolExecution = "sequential";
  session.agent.beforeToolCall = async () => parked ? { block: true, reason: "Not executed: waiting for user" } : undefined;
  session.agent.finishTurn = () => parked ? { action: "end" } : undefined;
  session.agent.streamFunction = () => {
    requests++;
    assert.equal(requests, 1, "card must prevent a second model request");
    const stream = createAssistantMessageEventStream();
    const message: AssistantMessage = {
      role: "assistant", content: definitions.map((t, i) => ({ type: "toolCall", id: `call-${i}`, name: t.name, arguments: {} })),
      api: model.api, provider: model.provider, model: model.id, stopReason: "toolUse", timestamp: Date.now(),
      usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    };
    queueMicrotask(() => { stream.push({ type: "done", reason: "toolUse", message }); stream.end(); });
    return stream;
  };
  try {
    await session.prompt("start");
    assert.deepEqual(executed, ["read", "AskUserQuestion"]);
    assert.equal(requests, 1);
    const header = manager.getHeader();
    assert.ok(header);
    const restored = SessionManager.inMemory(process.cwd(), undefined, [header, ...manager.getEntries()]);
    restored.branch(manager.getLeafId()!);
    const { session: resumed } = await makeSession([], [], restored);
    try {
      resumed.agent.streamFunction = (_model, context) => {
        assert.ok(context.messages.some(m => m.role === "toolResult" && m.toolCallId === "call-0"));
        const stream = createAssistantMessageEventStream();
        const message: AssistantMessage = { ...session.agent.state.messages.find(m => m.role === "assistant") as AssistantMessage,
          content: [{ type: "text", text: "continued" }], stopReason: "stop" };
        queueMicrotask(() => { stream.push({ type: "done", reason: "stop", message }); stream.end(); });
        return stream;
      };
      await resumed.prompt("the user answered");
      assert.deepEqual(executed, ["read", "AskUserQuestion"]);
    } finally { resumed.dispose(); }
  } finally { session.dispose(); }
});

test("explicit allowlist activates exactly the seven custom implementations", async () => {
  const { session } = await makeSession(names);
  try {
    assert.deepEqual(session.getActiveToolNames().sort(), [...names].sort());
    for (const tool of session.agent.state.tools) {
      const result = await tool.execute("probe", {});
      assert.equal(result.content[0].type, "text");
      assert.equal((result.content[0] as { text: string }).text, "custom:" + tool.name);
    }
  } finally { session.dispose(); }
});

test("in-memory checkpoint imports entries and restores the selected leaf", () => {
  const original = SessionManager.inMemory();
  const leaf = original.appendMessage({ role: "user", content: "original", timestamp: 1 });
  original.appendMessage({ role: "user", content: "other branch", timestamp: 2 });
  const header = original.getHeader();
  assert.ok(header);
  const restored = SessionManager.inMemory(process.cwd(), undefined, [header, ...original.getEntries()]);
  restored.branch(leaf);
  assert.equal(restored.getLeafId(), leaf);
  assert.deepEqual(restored.buildSessionContext().messages.map(m => "content" in m ? m.content : undefined), ["original"]);
});
