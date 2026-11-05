/**
 * Cell entrypoint.
 *
 * The kernel is constructed *with* a real model factory. A Cell wired without
 * one throws on the first run and passes every test, because tests inject their
 * own stream function — so the wiring, not the kernel, is what this file exists
 * to get right.
 */

import { CellChannel, logDiagnostic } from "./protocol/channel.js";
import { Cell } from "./kernel/cell.js";
import { resolveRuntimeModel } from "./providers/stream-fn-resolver.js";

import type { CellInitPayload, ProtocolFrame } from "./protocol/frames.js";

function bootstrap(): void {
  const channel = new CellChannel(process.stdin, process.stdout);
  const cell = new Cell(resolveRuntimeModel);

  let running = false;
  const operationAcks = new Map<string, () => void>();

  channel.onFrame(async (frame: ProtocolFrame) => {
    switch (frame.type) {
      case "cell.init": {
        if (running) {
          channel.send("protocol.error", { error: "cell already has an active run" });
          return;
        }
        running = true;
        const init = frame.payload as unknown as CellInitPayload;
        channel.send("cell.ready", { manifest: { runtime_kind: "pi_agent_core", protocol_version: 2 } });
        let terminalEvent: Record<string, unknown> | undefined;
        try {
          const result = await cell.run(
            init,
            (event) => {
              if (["run.completed", "run.failed", "run.cancelled", "run.suspended"].includes(event.type)) terminalEvent = event as unknown as Record<string, unknown>;
              else channel.send("run.event", event as unknown as Record<string, unknown>);
            },
            (detail) => {
              channel.send("run.heartbeat", detail);
            },
            async (requestId, result) => {
              const committed = new Promise<void>(resolve => operationAcks.set(requestId, resolve));
              channel.send("operation.result", { request_id: requestId, result });
              await committed;
            }
          );
          if (result.checkpoint) channel.send("session.entries", { ...result.checkpoint, tool_calls: result.tool_calls, turns: result.turns } as unknown as Record<string, unknown>);
          if (terminalEvent) channel.send("run.event", terminalEvent);
          channel.send(result.terminal_status === "parked" ? "run.parked" : "run.settled",
            { ...result, checkpoint: undefined } as unknown as Record<string, unknown>);
        } finally {
          running = false;
          // One run per Cell process: the control plane spawns a fresh child per
          // turn, so lingering here would only hold the workspace open.
          //
          // Flush before exiting. process.exit() discards whatever is still
          // queued on stdout, and the terminal event is the very last frame
          // written, so exiting eagerly turns a completed run into CELL_LOSS.
          channel.close();
          await channel.flush();
          process.exit(0);
        }
        return;
      }
      case "run.cancel": {
        logDiagnostic(`cancel requested: ${String((frame.payload as { reason?: string })?.reason ?? "")}`);
        cell.cancel();
        return;
      }
      case "cell.shutdown": {
        channel.close();
        await channel.flush();
        process.exit(0);
        return;
      }
      case "operation.saved": {
        const requestId = String((frame.payload as { request_id?: string }).request_id ?? "");
        operationAcks.get(requestId)?.();
        operationAcks.delete(requestId);
        return;
      }
      default: {
        channel.send("protocol.error", { error: `unknown frame type '${frame.type}'` });
      }
    }
  });

  channel.start();
  logDiagnostic("runtime cell listening on stdio");
}

bootstrap();
