import test from "node:test";
import assert from "node:assert/strict";
import { logDiagnostic } from "../src/protocol/channel.js";

function captureStderr(run: () => void): string {
  const original = process.stderr.write.bind(process.stderr);
  let written = "";
  (process.stderr as any).write = (chunk: string | Uint8Array) => { written += String(chunk); return true; };
  try { run(); } finally { (process.stderr as any).write = original; }
  return written;
}

test("a diagnostic is one JSON line with level and source for runtime.log", () => {
  const out = captureStderr(() => {
    logDiagnostic("runtime cell listening on stdio");
    logDiagnostic("sandbox start name=x\nsecond line", { level: "WARN", source: "sandbox" });
  });
  const lines = out.trimEnd().split("\n");
  assert.equal(lines.length, 2);
  assert.deepEqual(lines.map(line => JSON.parse(line)), [
    { level: "INFO", source: "runtime", message: "runtime cell listening on stdio" },
    { level: "WARN", source: "sandbox", message: "sandbox start name=x\nsecond line" },
  ]);
});
