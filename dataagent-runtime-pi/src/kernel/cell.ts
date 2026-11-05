/**
 * One run on one Cell.
 *
 * Owns the agent lifecycle for a single cell.init and guarantees the control
 * plane always sees exactly one terminal event, whatever happens: normal
 * completion, model error, policy denial, cancel, or a limit being hit.
 */

import type { Agent } from "@earendil-works/pi-agent-core";
import type { AgentSession, SessionManager, ToolDefinition } from "@earendil-works/pi-coding-agent";
import { Type } from "@earendil-works/pi-ai";
import { createDataAgentSession } from "./sdk-session.js";
import { DockerSandboxManager } from "../sandbox/docker-sandbox-manager.js";
import { createSandboxTools } from "../sandbox/sandbox-tools.js";
import type { AgentEvent as PiAgentEvent, StreamFn } from "@earendil-works/pi-agent-core";
import type { Api, Model, Usage } from "@earendil-works/pi-ai";
import type { CellInitPayload, NeutralAgentEvent } from "../protocol/frames.js";
import { RunStateMachine } from "./run-state-machine.js";
import { EventNormalizer, unwrapToolResult } from "./event-normalizer.js";


import { connectMcpServers, McpToolError, type McpBridgeResult } from "../mcp/mcp-client.js";
import { logDiagnostic } from "../protocol/channel.js";
import { saveResult } from "../context/result-files.js";
import {
  shouldFold,
  extractDigest,
  DEFAULT_FOLD_THRESHOLD_BYTES,
  formatDigestText,
  STRUCTURED_OUTPUT_MAX_BYTES,
} from "../context/tabular-digest.js";

import { UiToolResultRegistry } from "./ui-tool-result-registry.js";



/**
 * Attach fold provenance without touching anything the tool wrote.
 *
 * The fold used to claim top-level names like `result_ref`, which collides with
 * what a tool reports about itself — fetch_tool_result names the source it read
 * that way. Displacing the loser under a `source_` prefix worked but needed a
 * closed field list, an alias rule, and a mirror of both in Python, all to
 * arbitrate a collision. Nesting the fold's own fields cannot collide at all.
 */
export function withFoldProvenance(
  toolDetails: unknown,
  fold: Record<string, unknown>
): Record<string, unknown> {
  const isPlainObject = (value: unknown): value is Record<string, unknown> =>
    typeof value === "object" && value !== null && !Array.isArray(value);

  // A tool's details are typed as anything. Spreading a string would turn it
  // into numeric keys and a number into nothing, so keep it whole instead.
  const base = isPlainObject(toolDetails)
    ? { ...toolDetails }
    : toolDetails != null
      ? { raw_details: toolDetails }
      : {};

  return { ...base, dataagent_fold: fold };
}

export type EventSink = (event: NeutralAgentEvent) => void;
/** Liveness signal during a slow tool. Carries no state the UI renders. */
export type HeartbeatSink = (detail: Record<string, unknown>) => void;

export interface RunModelFactory {
  (
    providerId: string,
    modelId: string,
    apiFormat: string,
    cacheRetention?: string,
    maxOutputTokens?: number,
    contextWindow?: number
  ): {
    model: Model<Api>;
    streamFn: StreamFn;
  };
}

export interface CellRunResult {
  terminal_status: "success" | "failed" | "cancelled" | "parked";
  checkpoint?: { header: unknown; entries: unknown[]; leaf_id: string | null };
  interaction?: { request_id: string; kind: string; payload: Record<string, unknown> };
  tool_calls?: number;
  turns?: number;
  last_sequence: number;
  error?: string;
}

export class Cell {
  private agent: Agent | null = null;
  private cancelled = false;
  private readonly cancellation = new AbortController();

  constructor(private readonly modelFactory: RunModelFactory) {}

  public cancel(): void {
    this.cancelled = true;
    this.cancellation.abort();
    this.agent?.abort();
  }

  public async run(
    init: CellInitPayload,
    sink: EventSink,
    onHeartbeat: HeartbeatSink = () => {},
    onOperationResult?: (requestId: string, result: unknown) => Promise<void>
  ): Promise<CellRunResult> {
    const heartbeat = onHeartbeat;
    const sm = new RunStateMachine(init.run_id, init.task_id, init.run_id);
    // Holds the UI copy of a tool result between afterToolCall and the
    // normalizer. Scoped to this run so nothing survives it.
    const uiResults = new UiToolResultRegistry();
    const normalizer = new EventNormalizer(sm, uiResults, init.turn_offset ?? 0);

    const emit = (event: NeutralAgentEvent | null) => {
      if (event) {
        sink(event);
      }
    };

    emit(sm.createEvent("run.started", { topic_id: init.topic_id }));

    let toolCalls = 0;
    let turnCount = 0;
    let limitDenial: string | null = null;
    let mcpBridge: McpBridgeResult | null = null;
    let activeToolTimer: NodeJS.Timeout | null = null;
    // Plan mode holds the listed tools until the user approves a plan.
    const planMode = init.permission_mode === "plan";
    let planApproved = Boolean(init.plan_approved);
    let parked: CellRunResult["interaction"];
    let sdk: { session: AgentSession; manager: SessionManager } | undefined;
    let sandbox: DockerSandboxManager | undefined;
    let segmentError: string | undefined;
    const park = (kind: string, request_id: string, payload: Record<string, unknown>) => {
      if (parked) throw new Error("Interaction already pending");
      parked = { kind, request_id: `${init.run_id}:${request_id}`, payload };
      return { content: [{ type: "text" as const, text: "Waiting for user; this execution segment has ended." }], details: { waiting: true } };
    };

    try {
      const deadline = Date.now() + init.limits.total_timeout_seconds * 1000;
      mcpBridge = await connectMcpServers(init.mcp_servers, { connectTimeoutMs: 10_000,
        remainingMs: () => deadline - Date.now(),
        readTimeoutMs: Number(init.runtime_env.DATAAGENT_SQL_READ_TIMEOUT_SECONDS || 300) * 1000,
        writeTimeoutMs: Number(init.runtime_env.DATAAGENT_SQL_WRITE_TIMEOUT_SECONDS || 60) * 1000 });
      const mcpAnnotations = new Map(mcpBridge.tools.map((tool: any) => [String(tool.name), tool.mcpAnnotations ?? {}]));
      // Cache retention must reach the stream options, not merely the init
      // frame: pi-ai defaults to "short" whenever the option is absent.
      const { model, streamFn } = this.modelFactory(
        init.model.provider_id,
        init.model.model_id,
        init.model.api_format,
        init.governance_settings?.cache_retention,
        init.model.max_output_tokens,
        // frames.ts contract: an unconfigured model window uses governance's
        // max_context_tokens, so SDK compaction triggers before overflow.
        init.model.context_window ?? init.governance_settings?.max_context_tokens
      );
      if (!init.sandbox || !init.results_dir) throw new Error("Protocol v2 requires sandbox and results_dir");
      sandbox = new DockerSandboxManager({ ...init.sandbox, env: { ...init.sandbox.env, DATAAGENT_PLAN_STATE: planApproved || !planMode ? "approved" : "pending" } });
      const baseTools = createSandboxTools(sandbox, { resultsDir: init.results_dir,
        timeoutMs: init.limits.bash_timeout_seconds * 1000,
        inlineBytes: init.governance_settings?.max_inline_result_bytes ?? DEFAULT_FOLD_THRESHOLD_BYTES,
        onUiResult: (id, result) => { const unwrapped = unwrapToolResult(result);
          uiResults.set(id, { output: unwrapped.output, meta: unwrapped.output_meta }); } });
      const reservedNames = new Set(["bash", "read", "write", "edit", "ls", "find", "grep", "AskUserQuestion", "ExitPlanMode"]);
      for (const tool of mcpBridge.tools as any[]) {
        if (reservedNames.has(tool.name)) throw new Error("MCP_TOOL_NAME_COLLISION");
        reservedNames.add(tool.name);
      }
      const interactionTools: ToolDefinition[] = [
        { name: "AskUserQuestion", label: "AskUserQuestion", description: "Ask the user a question and end this segment until they answer.",
          parameters: Type.Object({ questions: Type.Array(Type.Object({ question: Type.String(),
            header: Type.Optional(Type.String()), multiSelect: Type.Optional(Type.Boolean()),
            options: Type.Array(Type.Object({ label: Type.String(), description: Type.Optional(Type.String()), recommended: Type.Optional(Type.Boolean()) }), { minItems: 2, maxItems: 4 }) }), { minItems: 1, maxItems: 4 }) }),
          execute: async (id, args) => park("question", id, args as Record<string, unknown>) },
        ...(planMode ? [{ name: "ExitPlanMode", label: "ExitPlanMode", description: "Submit a plan for approval and end this segment.",
          parameters: Type.Object({ plan: Type.String() }),
          execute: async (id: string, args: unknown) => park("plan", id, args as Record<string, unknown>) }] : []),
      ];
      if (init.allow_questions === false) interactionTools.splice(0, 1);
      sdk = await createDataAgentSession(init, model, streamFn,
        [...baseTools, ...mcpBridge.tools, ...interactionTools] as ToolDefinition[]);
      const agent = sdk.session.agent;
      agent.afterToolCall = async (context) => {
          const details = context.result?.details as { segment_failed?: boolean; error?: string } | undefined;
          if (details?.segment_failed) segmentError = details.error || "Sandbox command failed";

          const foldThreshold = init.governance_settings?.max_inline_result_bytes ?? DEFAULT_FOLD_THRESHOLD_BYTES;
          const toolResult = context.result;
          const mcpWrite = mcpAnnotations.has(context.toolCall.name) &&
            (mcpAnnotations.get(context.toolCall.name)?.readOnlyHint !== true || mcpAnnotations.get(context.toolCall.name)?.destructiveHint === true);
          if (mcpWrite && toolResult?.isError) segmentError = "MCP_WRITE_FAILED: check the external operation state before retrying";
          if ((toolResult?.details as { storage_path?: string })?.storage_path) return undefined;
          if (!toolResult || !Array.isArray(toolResult.content)) {
            return undefined;
          }

          // Measure and persist every text block, not just the first. A small
          // leading block used to hide large ones from the threshold, and when
          // the first block did trigger folding the remaining blocks were
          // dropped with the replaced content array and never stored.
          const textBlocks = toolResult.content.filter(
            (block): block is { type: "text"; text: string } => block?.type === "text"
          );
          const nonTextBlockCount = toolResult.content.length - textBlocks.length;
          const rawText = textBlocks.map((block) => String(block.text ?? "")).join("\n");
          if (!shouldFold(rawText, foldThreshold)) {
            return undefined;
          }

          // Register the UI's copy before folding anything. The two used to be
          // the same object, so shrinking the model's context also erased the
          // chart from history. Now the transcript keeps the full result and
          // only the model sees the digest — which is also why structured
          // outputs no longer need a fold exemption paid for in tokens.
          const toolCallId = String(context.toolCall?.id ?? "");
          const uiBytes = Buffer.byteLength(rawText, "utf8");
          // Unwrap here rather than storing the raw wrapper, so a registered
          // copy and the normalizer's own fallback produce the same shape. It
          // also keeps the tool's own details: registering content alone
          // silently dropped them for exactly the large results this registry
          // exists to protect, and the fold provenance below then looked like
          // the whole story.
          const unwrapped = unwrapToolResult(toolResult);
          // Held locally so the fold below can attach provenance without a
          // take/set round-trip.
          const uiOutput =
            uiBytes <= STRUCTURED_OUTPUT_MAX_BYTES
              // Copy the array: sharing it would let the fold below mutate what
              // the UI is about to persist.
              ? toolResult.content.map((block) => ({ ...block }))
              : null;
          if (uiOutput) {
            uiResults.set(toolCallId, { output: uiOutput, meta: unwrapped.output_meta });
          } else {
            // Past the persistence ceiling the transcript keeps the digest too.
            // Saying so beats handing the renderer a digest silently, which
            // draws a broken chart with no sign anything was dropped.
            logDiagnostic(
              `STRUCTURED_OUTPUT_TOO_LARGE: ${uiBytes}B exceeds ${STRUCTURED_OUTPUT_MAX_BYTES}B; ` +
                `UI copy will hold the digest`
            );
          }

          try {
            const toolName = String(context.toolCall?.name ?? "tool");
            const saveOutcome = await saveResult(init.results_dir!, rawText);
            const digest = extractDigest(rawText, {
              resultRef: saveOutcome.result_ref,
              toolName,
            });
            let compactText = `Saved output: ${saveOutcome.relative_path} (${saveOutcome.format}, ${saveOutcome.byte_size} bytes).\n` + formatDigestText(digest, saveOutcome.relative_path);
            // Last-resort guard: folding must never make the context larger.
            // Cell capping handles the common case, but a very wide schema or
            // many columns can still out-grow the original.
            if (Buffer.byteLength(compactText, "utf8") >= Buffer.byteLength(rawText, "utf8")) {
              const minimal: Record<string, unknown> = { ...digest };
              delete minimal.preview_head;
              delete minimal.preview_tail;
              compactText = JSON.stringify(minimal, null, 2);
            }

            // Non-text blocks (images and the like) are not folded and not
            // stored, so keep them inline rather than losing them.
            const preservedBlocks = toolResult.content.filter(
              (block: { type?: string }) => block && block.type !== "text"
            );

            // One merge, one shape. The registry built provenance after
            // unwrapping and the returned details before it, so the same
            // source_result_ref sat at the top of output_meta for a small
            // result and inside engine_details for a large one. Where a field
            // lives must not depend on how big the result was.
            //
            // Merging rather than replacing also matters past the persistence
            // ceiling: nothing is registered there, so this is the only copy
            // the transcript gets.
            const foldedDetails = withFoldProvenance(toolResult.details, {
              result_ref: saveOutcome.result_ref,
              storage_path: saveOutcome.relative_path,
              // Both sizes describe the original raw JSON/text, never the preview.
              original_bytes: uiBytes,
              stored_bytes: saveOutcome.byte_size,
              folded_text_blocks: textBlocks.length,
              preserved_blocks: nonTextBlockCount,
            });

            // Record on the UI copy that the model saw a digest, and where the
            // full result lives. The transcript still holds the whole payload;
            // this is provenance, not a substitute for it.
            if (uiOutput) {
              uiResults.set(toolCallId, {
                output: uiOutput,
                meta: unwrapToolResult({ content: uiOutput, details: foldedDetails }).output_meta,
              });
            }

            return {
              content: [{ type: "text" as const, text: compactText }, ...preservedBlocks],
              details: foldedDetails,
              // Folding shrinks the text; it must not turn a failed call into a success.
              ...(toolResult.isError ? { isError: true } : {}),
            };
          } catch (err) {
            segmentError = `RESULT_STORAGE_FAILED: ${String(err)}`;
            logDiagnostic(segmentError);
            return { content: [{ type: "text" as const, text: segmentError }],
              details: { error: segmentError, segment_failed: true }, isError: true };
          }
        };
      agent.beforeToolCall = async (context) => {
          if (parked || segmentError) return { block: true, reason: parked ? "Not executed: waiting for user" : segmentError };
          toolCalls += 1;
          if (toolCalls > init.limits.max_tool_calls) {
            limitDenial = `已达到本次任务工具调用上限 ${init.limits.max_tool_calls}`;
            // Close the started tool block, as the plan-mode denial below does.
            emit(sm.createEvent("tool.denied", { turn_id: normalizer.turnId,
              tool_call_id: String(context.toolCall?.id ?? ""),
              tool_name: String(context.toolCall?.name ?? ""), reason: limitDenial }));
            return { block: true, reason: limitDenial, terminate: true };
          }
          // Second line of defence. The tools enforce the boundary themselves,
          // but a tool added later must not be able to skip it by forgetting.
          const toolName = String(context.toolCall?.name ?? "");
          const args = (context.toolCall?.arguments ?? {}) as Record<string, unknown>;
          const hints = mcpAnnotations.get(toolName);
          const isMcp = mcpAnnotations.has(toolName);
          const write = isMcp && (hints?.readOnlyHint !== true || hints?.destructiveHint === true);
          const reason =
            planMode && !planApproved && (write || ["write", "edit"].includes(toolName))
              ? `计划模式：批准计划前不能调用 ${toolName}。先调用 ExitPlanMode 提交计划，批准后再执行。`
              : null;
          if (reason) {
            emit(
              sm.createEvent("tool.denied", {
                turn_id: normalizer.turnId,
                // The reducer locates the tool block by this id; without it a
                // denied call renders as a call that never finished.
                tool_call_id: String(context.toolCall?.id ?? ""),
                tool_name: toolName,
                reason,
              })
            );
            return { block: true, reason };
          }
          const mode = planApproved ? "acceptEdits" : init.permission_mode ?? "default";
          if (write && mode !== "bypassPermissions" && (mode !== "acceptEdits" || hints?.destructiveHint === true)) {
            park("permission", String(context.toolCall?.id ?? ""), {
              tool_name: toolName, arguments: args, annotations: hints, plan_approved: planApproved,
              server_name: (mcpBridge!.tools.find((tool: any) => tool.name === toolName) as any)?.mcpServerName,
              server_identity: (mcpBridge!.tools.find((tool: any) => tool.name === toolName) as any)?.mcpServerIdentity,
            });
            return { block: true, reason: "Not executed: waiting for approval" };
          }
          return undefined;
        };
        // Error and aborted responses are hard exits already; only a normal
        // response may end the run early at the turn ceiling.
      agent.finishTurn = async (turn) => {
          if (parked || segmentError || limitDenial) return { action: "end" };
          if (turn.message.stopReason === "error" || turn.message.stopReason === "aborted") {
            return undefined;
          }
          if (init.limits.max_turns <= 0) {
            return undefined;
          }
          if (!(sm.lastSequence > 0 && turnCount >= init.limits.max_turns)) return undefined;
          // Cut off while it still wanted tools: the task is unfinished, so the
          // run fails like the tool-call ceiling instead of reading as done.
          if (turn.message.stopReason === "toolUse") {
            limitDenial = `已达到本次任务轮数上限 ${init.limits.max_turns}，任务未完成`;
          }
          return { action: "end" };
        };

      this.agent = agent;

      agent.subscribe((piEvent: PiAgentEvent) => {
        if (piEvent.type === "turn_start") {
          turnCount += 1;
        } else if (piEvent.type === "tool_execution_start") {
          if (activeToolTimer) clearInterval(activeToolTimer);
          const toolCallId = piEvent.toolCallId;
          const toolName = piEvent.toolName;
          activeToolTimer = setInterval(() => {
            // A protocol frame, not an event: this exists to keep the control
            // plane's idle timer alive during a slow tool, and nothing renders
            // it. Emitting an event instead filled the record table with rows
            // no consumer read.
            heartbeat({ tool_call_id: toolCallId, tool_name: toolName });
          }, 15_000);
        } else if (piEvent.type === "tool_execution_end") {
          if (activeToolTimer) {
            clearInterval(activeToolTimer);
            activeToolTimer = null;
          }
        }
        for (const event of normalizer.normalize(piEvent)) {
          emit(event);
        }
      });

      let prompt = init.prompt ?? init.messages.at(-1)?.content ?? "";
      if (init.resume_operation) {
        const operation = init.resume_operation;
        let result = operation.operation_result;
        if (operation.operation_status !== "done") {
          if (!onOperationResult) throw new Error("Operation result persistence unavailable");
          const tool: any = mcpBridge.tools.find((tool: any) => tool.name === operation.payload.tool_name &&
            tool.mcpServerName === operation.payload.server_name && tool.mcpServerIdentity === operation.payload.server_identity);
          if (!tool) throw new Error("Approved MCP tool is no longer available");
          // Already counted when the call parked for approval in the previous segment.
          const turn_id = normalizer.turnId;
          emit(sm.createEvent("tool.started", { turn_id, tool_call_id: operation.request_id,
            tool_name: tool.name, input: operation.payload.arguments }));
          try {
            result = await tool.execute(operation.request_id, operation.payload.arguments, this.cancellation.signal);
          } catch (error) {
            // A server error is a known outcome; a lost transport is not.
            if (!(error instanceof McpToolError)) throw error;
            result = { content: [{ type: "text", text: error.message }], isError: true };
          }
          const resultText = JSON.stringify(result);
          if (Buffer.byteLength(resultText) > STRUCTURED_OUTPUT_MAX_BYTES) {
            const stored = await saveResult(init.results_dir!, resultText);
            result = { content: [{ type: "text", text: `Approved operation result saved: ${stored.relative_path} (${stored.byte_size} bytes).` }],
              details: { storage_path: stored.relative_path, byte_count: stored.byte_size, preview_truncated: true },
              isError: Boolean((result as any)?.isError) };
          }
          await onOperationResult(operation.request_id, result);
          emit(sm.createEvent("tool.completed", { turn_id, tool_call_id: operation.request_id,
            tool_name: tool.name, output: (result as any)?.content, is_error: Boolean((result as any)?.isError) }));
        }
        const raw = JSON.stringify(result);
        if (shouldFold(raw, init.governance_settings?.max_inline_result_bytes ?? DEFAULT_FOLD_THRESHOLD_BYTES)) {
          const file = await saveResult(init.results_dir!, raw);
          prompt += `\nApproved operation result: ${file.relative_path} (${file.byte_size} bytes). Read the saved JSON as needed.`;
        } else prompt += "\nApproved operation actual result: " + raw;
      }
      if (this.cancelled) throw new Error("cancelled");
      await sdk.session.prompt(prompt, { expandPromptTemplates: false });
      const checkpoint = { header: sdk.manager.getHeader(), entries: sdk.manager.getEntries(), leaf_id: sdk.manager.getLeafId() };
      if (Buffer.byteLength(JSON.stringify(checkpoint)) > 8 * 1024 * 1024) throw new Error("SESSION_SNAPSHOT_TOO_LARGE");
      // Cancel wins over a card raised in the same segment: the user stopped the task.
      if (this.cancelled || agent.signal?.aborted) {
        emit(sm.createEvent("run.cancelled", { reason: "cancelled by control plane" }));
        return { terminal_status: "cancelled", last_sequence: sm.lastSequence };
      }
      if (parked) return { terminal_status: "parked", last_sequence: sm.lastSequence,
        checkpoint, interaction: parked, tool_calls: toolCalls, turns: turnCount };
      if (segmentError) throw new Error(segmentError);
      if (limitDenial) {
        emit(sm.createEvent("run.failed", { error_code: "PI_LIMIT_EXCEEDED", message: limitDenial }));
        return { terminal_status: "failed", last_sequence: sm.lastSequence, error: limitDenial };
      }

      // agent.prompt() resolves rather than rejects when the model stream
      // fails: the failure is recorded on the agent state as errorMessage.
      // Reporting success here would persist a successful-looking turn that
      // carries no answer, so the state has to be consulted explicitly.
      const modelError = agent.state.errorMessage;
      if (modelError) {
        emit(sm.createEvent("run.failed", { error_code: "PI_MODEL_ERROR", message: modelError }));
        return { terminal_status: "failed", last_sequence: sm.lastSequence, error: modelError };
      }

      // A reply cut off at the output ceiling ends the loop like a finished
      // one: no tool call follows, so a model that spent the budget thinking
      // looked like a successful run that produced nothing.
      const last = agent.state.messages[agent.state.messages.length - 1] as { role?: string; stopReason?: string } | undefined;
      if (last?.role === "assistant" && last.stopReason === "length") {
        const message = `模型单次回复达到输出上限 ${model.maxTokens} tokens，回复被截断；请在模型配置中调大“最大输出 tokens”`;
        emit(sm.createEvent("run.failed", { error_code: "PI_OUTPUT_TRUNCATED", message }));
        return { terminal_status: "failed", last_sequence: sm.lastSequence, error: message };
      }

      emit(sm.createEvent("run.completed", { terminal_status: "success" }));
      return { terminal_status: "success", last_sequence: sm.lastSequence, checkpoint, tool_calls: toolCalls, turns: turnCount };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      const stack = err instanceof Error ? err.stack : message;
      logDiagnostic(`run failed: ${stack}`);
      // Cancel surfaces as an abort rejection from the agent loop; it is a
      // cancellation, not a failure, and must not be reported as an error.
      if (this.cancelled) {
        emit(sm.createEvent("run.cancelled", { reason: "cancelled by control plane" }));
        return { terminal_status: "cancelled", last_sequence: sm.lastSequence };
      }
      emit(sm.createEvent("run.failed", { error_code: "PI_EXECUTION_ERROR", message }));
      return { terminal_status: "failed", last_sequence: sm.lastSequence, error: message };
    } finally {
      if (activeToolTimer) {
        clearInterval(activeToolTimer);
        activeToolTimer = null;
      }
      this.agent = null;
      sdk?.session.dispose();
      // A failed sandbox cleanup must still propagate (the backend then removes
      // the container itself), but never strand MCP transports or stdio children.
      try { await sandbox?.close(); }
      finally { await mcpBridge?.close(); mcpBridge = null; }
      // Whatever was never consumed goes now, on every exit path — a run that
      // failed or was cancelled mid-tool would otherwise leave results behind.
      if (uiResults.unconsumed > 0) {
        // Registered but never claimed: the afterToolCall/tool_execution_end
        // pairing is broken, which is the one asymmetry worth logging.
        logDiagnostic(`ui result registry: ${uiResults.unconsumed} unconsumed copy/copies`);
      }
      uiResults.clear();
    }
  }
}
