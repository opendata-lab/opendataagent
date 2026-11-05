import fs from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { Type } from "@earendil-works/pi-ai";
import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import { isRenderableStructuredOutput, STRUCTURED_OUTPUT_MAX_BYTES } from "../context/tabular-digest.js";
import { DockerSandboxManager } from "./docker-sandbox-manager.js";

// All model paths, file operations and pattern matching execute in the container.
// JSON stdin avoids shell quoting and the OS argument-size ceiling on file writes.
const FILE_TOOL = String.raw`
import sys,json,pathlib,subprocess,fnmatch
p=json.load(sys.stdin); name=sys.argv[1]; target=pathlib.Path(p.get('path','.') or '.')
if name=='read':
    cap=min(int(p.get('_inlineBytes',16384)),int(p.get('byteLimit',p.get('_inlineBytes',16384))))
    if 'byteOffset' in p:
        start=int(p['byteOffset'])
        with target.open('rb') as f:
            f.seek(start); raw=f.read(cap+1)
        text=raw[:cap].decode('utf-8',errors='ignore'); next_offset=start+len(text.encode('utf-8'))
        print(text,end='')
        if len(raw)>cap: print('\n[Next byteOffset: '+str(next_offset)+']')
    else:
        start=max(0,int(p.get('offset',1))-1); limit=max(1,int(p.get('limit',2000)))
        byte_start=0; raw=b''
        with target.open('rb') as f:
            for _ in range(start): byte_start+=len(f.readline())
            for _ in range(limit):
                line=f.readline(cap+1-len(raw)); raw+=line
                if len(raw)>cap or not line: break
            more=bool(f.read(1))
        text=raw[:cap].decode('utf-8',errors='ignore')
        print(text,end='')
        if len(raw)>cap: print('\n[Next byteOffset: '+str(byte_start+len(text.encode('utf-8')))+']')
        elif more: print('\n[More lines available; use offset/limit or byteOffset.]')
elif name=='write':
    target.parent.mkdir(parents=True,exist_ok=True); target.write_text(p['content'],encoding='utf-8'); print('File written')
elif name=='edit':
    original=target.read_text(encoding='utf-8'); changes=[]
    for edit in p['edits']:
        old=edit['oldText']
        if not old or original.count(old)!=1: raise ValueError('oldText must match exactly once')
        start=original.index(old); changes.append((start,start+len(old),edit['newText']))
    changes.sort()
    if any(a[1]>b[0] for a,b in zip(changes,changes[1:])): raise ValueError('Overlapping edits')
    text=original
    for start,end,new in reversed(changes): text=text[:start]+new+text[end:]
    target.write_text(text,encoding='utf-8'); print('File edited')
elif name=='ls':
    items=sorted(target.iterdir(),key=lambda x:x.name)
    limit=max(1,int(p.get('limit',500)))
    for item in items[:limit]: print(item.name+('/' if item.is_dir() else ''))
    if len(items)>limit: print('[Directory listing truncated]')
elif name=='find':
    args=['fd','--glob','--hidden','--max-results',str(max(1,int(p.get('limit',1000)))),'--',p['pattern'],str(target)]
    sys.exit(subprocess.run(args).returncode)
elif name=='grep':
    args=['rg','--line-number','--max-count',str(max(1,int(p.get('limit',100))))]
    if p.get('ignoreCase'): args+=['--ignore-case']
    if p.get('literal'): args+=['--fixed-strings']
    if p.get('glob'): args+=['--glob',p['glob']]
    if p.get('context'): args+=['--context',str(max(0,int(p['context'])))]
    result=subprocess.run(args+['--',p['pattern'],str(target)])
    sys.exit(0 if result.returncode==1 else result.returncode)
`;

export function createSandboxTools(manager: DockerSandboxManager, options: {
  resultsDir: string; timeoutMs: number; inlineBytes: number;
  onUiResult?: (id: string, result: { content: Array<{ type: "text"; text: string }>; details: Record<string, unknown> }) => void;
}): ToolDefinition[] {
  const location = { path: Type.Optional(Type.String()) };
  const schemas = {
    bash: Type.Object({ command: Type.String() }),
    read: Type.Object({ path: Type.String(), offset: Type.Optional(Type.Integer({ minimum: 1 })), limit: Type.Optional(Type.Integer({ minimum: 1 })), byteOffset: Type.Optional(Type.Integer({ minimum: 0 })), byteLimit: Type.Optional(Type.Integer({ minimum: 4 })) }),
    write: Type.Object({ path: Type.String(), content: Type.String() }),
    edit: Type.Object({ path: Type.String(), edits: Type.Array(Type.Object({ oldText: Type.String(), newText: Type.String() }), { minItems: 1 }) }),
    ls: Type.Object({ ...location, limit: Type.Optional(Type.Integer({ minimum: 1 })) }),
    find: Type.Object({ ...location, pattern: Type.String(), limit: Type.Optional(Type.Integer({ minimum: 1 })) }),
    grep: Type.Object({ ...location, pattern: Type.String(), glob: Type.Optional(Type.String()), ignoreCase: Type.Optional(Type.Boolean()), literal: Type.Optional(Type.Boolean()), context: Type.Optional(Type.Integer({ minimum: 0 })), limit: Type.Optional(Type.Integer({ minimum: 1 })) }),
  };
  return Object.entries(schemas).map(([name, parameters]) => ({
    name, label: name, description: `${name} in the isolated sandbox. Paths are relative to /mnt/workspace; results are at /mnt/results.` +
      (name === "read" ? " For long single-line files use byteOffset/byteLimit, continuing at the returned Next byteOffset." : ""), parameters,
    execute: async (id, raw, signal) => {
      const params = raw as Record<string, unknown>;
      const filename = `res_${randomUUID().replace(/-/g, "")}.txt`;
      let output: fs.FileHandle | undefined;
      try {
        if (name !== "read") {
          await fs.mkdir(options.resultsDir, { recursive: true });
          output = await fs.open(path.join(options.resultsDir, filename), "wx");
        }
      } catch (cause) {
        await manager.close();
        const error = `RESULT_STORAGE_FAILED: ${String(cause)}`;
        return { content: [{ type: "text", text: error }], details: { error, segment_failed: true }, isError: true };
      }
      let bytes = 0;
      const preview: Buffer[] = [];
      let previewBytes = 0;
      let writeChain = Promise.resolve();
      let writeError: unknown;
      let storedBytes = 0;
      let exitCode = 1;
      let error: string | undefined;
      try {
        exitCode = await manager.execute(name === "bash"
          ? ["bash", "-o", "pipefail", "-c", String(params.command)]
          : ["python", "-c", FILE_TOOL, name], {
          input: name === "bash" ? undefined : JSON.stringify({ ...params, _inlineBytes: options.inlineBytes }),
          timeoutMs: options.timeoutMs, signal,
          onData: chunk => {
            bytes += chunk.length;
            // read already bounds file bytes and adds a small continuation marker.
            const remaining = Math.max(0, options.inlineBytes + (name === "read" ? 256 : 0) - previewBytes);
            if (remaining) { const part = chunk.subarray(0, remaining); preview.push(part); previewBytes += part.length; }
            if (!output) return;
            const destination = output;
            writeChain = writeChain.then(async () => { if (!writeError) { await destination.writeFile(chunk); storedBytes += chunk.length; } }).catch(e => { writeError = e; });
            return writeChain;
          },
        });
      } catch (cause) { error = String(cause); }
      finally { await writeChain; try { await output?.close(); } catch (cause) { writeError = cause; } }
      if (writeError) {
        await manager.close();
        error = `RESULT_STORAGE_FAILED: saved ${storedBytes} of ${bytes} received bytes. ${String(writeError)}`;
      }
      const file = name === "read" ? path.posix.resolve("/mnt/workspace", String(params.path)) : `/mnt/results/${filename}`;
      const text = Buffer.concat(preview).toString("utf8");
      const existingFile = name === "read" && !error && exitCode === 0;
      const saved = name !== "read" && bytes > options.inlineBytes;
      if (!saved && output) await fs.unlink(path.join(options.resultsDir, filename));
      const details = { exit_code: exitCode, byte_count: bytes, ...(existingFile ? { storage_path: file, format: "text" } : {}), ...(saved ? { storage_path: file, format: "text",
          dataagent_fold: { storage_path: file, original_bytes: bytes, stored_bytes: storedBytes,
            result_ref: filename.replace(/\.txt$/, "") } } : {}), ...(error ? { error, segment_failed: true } : {}) };
      // Preserve bounded structured UI payloads without sending their full data
      // back into model context. The filename is generated here, never model-supplied.
      if (saved && !error && bytes <= STRUCTURED_OUTPUT_MAX_BYTES && options.onUiResult) {
        const complete = await fs.readFile(path.join(options.resultsDir, filename), "utf8");
        if (isRenderableStructuredOutput(complete)) options.onUiResult(id, { content: [{ type: "text", text: complete }], details });
      }
      return {
        content: [{ type: "text", text: text + (saved ? `\nOutput saved: ${file} (${storedBytes} bytes, UTF-8 text).` : "") + (existingFile ? `\nSource file: ${file}. Continue with byteOffset/byteLimit or process it with bash.` : "") + (error ? `\n${error}` : "") }],
        details, isError: Boolean(error) || exitCode !== 0,
      };
    },
  }));
}
