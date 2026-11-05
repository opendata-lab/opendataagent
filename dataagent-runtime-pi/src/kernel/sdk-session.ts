import { InMemoryCredentialStore, InMemoryModelsStore, type Api, type Model } from "@earendil-works/pi-ai";
import type { StreamFn } from "@earendil-works/pi-agent-core";
import { createAgentSession, createExtensionRuntime, ModelRuntime, SessionManager, SettingsManager,
  type ResourceLoader, type ToolDefinition, type FileEntry } from "@earendil-works/pi-coding-agent";
import type { CellInitPayload } from "../protocol/frames.js";

/** No default resource loader, auth file, models file or session file. */
export async function createDataAgentSession(init: CellInitPayload, model: Model<Api>, streamFn: StreamFn, tools: ToolDefinition[]) {
  const manager = SessionManager.inMemory("/mnt/workspace", undefined,
    init.session_checkpoint ? [init.session_checkpoint.header, ...init.session_checkpoint.entries] as FileEntry[] : undefined);
  if (init.session_checkpoint) {
    if (init.session_checkpoint.leaf_id) manager.branch(init.session_checkpoint.leaf_id);
    else manager.resetLeaf();
  } else {
    for (const message of init.history ?? []) {
      if (message.role === "user") manager.appendMessage({ role: "user", content: message.content, timestamp: Date.now() });
      else manager.appendMessage({ role: "assistant", content: [{ type: "text", text: message.content }],
        api: model.api, provider: model.provider, model: model.id, timestamp: Date.now(), stopReason: "stop",
        usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } });
    }
  }
  const runtime = await ModelRuntime.create({ credentials: new InMemoryCredentialStore(),
    modelsStore: new InMemoryModelsStore(), modelsPath: null, refreshOnCreate: false });
  // The stream closure owns the credential. This provider marker satisfies the
  // SDK's configured-provider check; every request, including compaction, uses
  // the same control-plane stream, never SDK environment discovery.
  runtime.registerProvider(model.provider, { api: model.api, baseUrl: model.baseUrl,
    apiKey: "dataagent-managed-stream", models: [model], streamSimple: streamFn as never });
  const loader: ResourceLoader = {
    getExtensions: () => ({ extensions: [], errors: [], runtime: createExtensionRuntime() }),
    getSkills: () => ({ skills: init.skills.map(s => ({ name: s.name, description: s.name,
      filePath: `${s.root_path}/SKILL.md`, baseDir: s.root_path, disableModelInvocation: false,
      sourceInfo: { path: `${s.root_path}/SKILL.md`, source: "dataagent", scope: "temporary" as const, origin: "top-level" as const },
    })), diagnostics: [] }),
    getPrompts: () => ({ prompts: [], diagnostics: [] }),
    getThemes: () => ({ themes: [], diagnostics: [] }),
    getAgentsFiles: () => ({ agentsFiles: [] }),
    getSystemPrompt: () => init.system_prompt + "\n\nEnabled skills (load instructions with read):\n" +
      init.skills.map(s => `- ${s.name}: ${s.root_path}/SKILL.md`).join("\n"),
    getSystemPromptSource: () => undefined, getAppendSystemPrompt: () => [], getAppendSystemPromptSources: () => [],
    extendResources: () => {}, reload: async () => {},
  };
  const { session } = await createAgentSession({ cwd: "/mnt/workspace", model,
    modelRuntime: runtime, resourceLoader: loader, sessionManager: manager,
    settingsManager: SettingsManager.inMemory({ retry: { enabled: false }, compaction: { enabled: true } }),
    tools: tools.map(t => t.name), customTools: tools });
  session.agent.toolExecution = "sequential";
  session.agent.streamFunction = streamFn;
  return { session, manager };
}
