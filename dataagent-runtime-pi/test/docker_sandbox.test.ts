import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { DockerSandboxManager } from "../src/sandbox/docker-sandbox-manager.js";
import { createSandboxTools } from "../src/sandbox/sandbox-tools.js";

const image = process.env.DATAAGENT_SANDBOX_TEST_IMAGE;
function fixture() {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "oda-sandbox-test-")));
  const dirs = ["workspace", "results", "skill"].map(name => {
    const dir = path.join(root, name); fs.mkdirSync(dir); return dir;
  });
  fs.writeFileSync(path.join(dirs[1], "result.json"), '{"rows":[1,2,3]}');
  const manager = new DockerSandboxManager({
    runId: root, image: image!, cli: "docker", uid: process.getuid!(), gid: process.getgid!(),
    lifetimeSeconds: 60, memory: "512m", cpus: "1", pidsLimit: 128, workspaceSource: dirs[0], resultsSource: dirs[1],
    skills: [{ source: dirs[2], folder: "test-skill" }],
  });
  return { root, dirs, manager };
}

test("sandbox rejects malformed resource limits before any docker call", () => {
  const base = { runId: "r", image: "img", cli: "docker" as const, uid: 1000, gid: 1000, lifetimeSeconds: 60,
    memory: "2g", cpus: "2", pidsLimit: 512, workspaceSource: "/w", resultsSource: "/r", skills: [] };
  assert.doesNotThrow(() => new DockerSandboxManager(base));
  for (const bad of [{ memory: "2g --privileged" }, { memory: "" }, { cpus: "two" }, { pidsLimit: 0 }, { pidsLimit: 1.5 }]) {
    assert.throws(() => new DockerSandboxManager({ ...base, ...bad }), /Invalid sandbox resource limits/);
  }
});

test("sandbox network is a plain docker network name", () => {
  const base = { runId: "r", image: "img", cli: "docker" as const, uid: 1000, gid: 1000, lifetimeSeconds: 60,
    memory: "2g", cpus: "2", pidsLimit: 512, workspaceSource: "/w", resultsSource: "/r", skills: [] };
  for (const network of [undefined, "none", "bridge", "oda_trusted-net.1"]) {
    assert.doesNotThrow(() => new DockerSandboxManager({ ...base, network }));
  }
  for (const network of ["", "container:abc", "bridge --privileged", "-x"]) {
    assert.throws(() => new DockerSandboxManager({ ...base, network }), /Invalid sandbox network/);
  }
});

test("real sandbox reuses one container and enforces readonly mounts and no network", { skip: !image }, async () => {
  const { root, dirs, manager } = fixture();
  const execute = async (args: string[]) => {
    const chunks: Buffer[] = [];
    const code = await manager.execute(args, { timeoutMs: 15_000, onData: b => { chunks.push(b); } });
    return { code, text: Buffer.concat(chunks).toString() };
  };
  try {
    const results = await Promise.all([
      execute(["python", "-c", "from pathlib import Path; Path('hello.txt').write_text('hello')"]),
      execute(["cat", "/mnt/results/result.json"]),
    ]);
    assert.equal(results[0].code, 0);
    assert.equal(results[1].text, '{"rows":[1,2,3]}');
    assert.equal(fs.readFileSync(path.join(dirs[0], "hello.txt"), "utf8"), "hello");
    for (const dest of ["/mnt/results/new.txt", "/opt/skills/test-skill/new.txt", "/etc/new.txt"]) {
      assert.notEqual((await execute(["python", "-c", "import sys;open(sys.argv[1],'w').write('x')", dest])).code, 0);
    }
    const [container] = JSON.parse(execFileSync("docker", ["inspect", manager.containerName], { encoding: "utf8" }));
    assert.equal(container.HostConfig.NetworkMode, "none");
    assert.equal(container.HostConfig.ReadonlyRootfs, true);
    assert.ok(!container.Mounts.some((m: { Destination: string }) => m.Destination.includes("docker.sock")));
  } finally {
    await manager.close(); fs.rmSync(root, { recursive: true, force: true });
  }
});

test("slow container startup does not spend the command timeout", { skip: !image }, async () => {
  const { root, manager } = fixture();
  // Simulate a slow daemon: startup takes longer than the whole command budget.
  const internals = manager as unknown as { start: () => Promise<void> };
  const realStart = internals.start.bind(manager);
  internals.start = async () => { await new Promise(resolve => setTimeout(resolve, 1500)); await realStart(); };
  try {
    const code = await manager.execute(["true"], { timeoutMs: 1000, onData: () => {} });
    assert.equal(code, 0);
  } finally {
    await manager.close(); fs.rmSync(root, { recursive: true, force: true });
  }
});

test("timeout destroys all descendants and the segment cannot restart", { skip: !image }, async () => {
  const { root, manager } = fixture();
  try {
    await manager.execute(["true"], { timeoutMs: 15_000, onData: () => {} });
    await assert.rejects(manager.execute(["bash", "-c", "sleep 60 & wait"], {
      timeoutMs: 300, onData: () => {},
    }), /SANDBOX_COMMAND_TIMEOUT/);
    const remaining = execFileSync("docker", ["ps", "-aq", "--filter", `name=^/${manager.containerName}$`], { encoding: "utf8" });
    assert.equal(remaining.trim(), "");
    await assert.rejects(manager.execute(["true"], { timeoutMs: 1000, onData: () => {} }), /SANDBOX_CLOSED/);
  } finally {
    await manager.close(); fs.rmSync(root, { recursive: true, force: true });
  }
});

test("seven sandbox tools edit files and consume a complete saved result", { skip: !image || !image.startsWith("oda-sandbox") }, async () => {
  const { root, dirs, manager } = fixture();
  const tools = createSandboxTools(manager, { resultsDir: dirs[1], timeoutMs: 15_000, inlineBytes: 1024 });
  const call = async (name: string, args: Record<string, unknown>) => {
    const tool = tools.find(t => t.name === name)!;
    return tool.execute("test", args, new AbortController().signal, undefined, {} as never);
  };
  try {
    assert.deepEqual(tools.map(t => t.name).sort(), ["bash", "read", "write", "edit", "ls", "find", "grep"].sort());
    assert.equal((await call("write", { path: "folder/a.txt", content: "alpha\nbeta\n" })).isError, false);
    assert.equal((await call("edit", { path: "folder/a.txt", edits: [{ oldText: "beta", newText: "gamma" }] })).isError, false);
    const text = JSON.stringify(await call("read", { path: "folder/a.txt" }));
    assert.match(text, /gamma/);
    assert.match(JSON.stringify(await call("ls", { path: "folder" })), /a.txt/);
    assert.match(JSON.stringify(await call("find", { pattern: "*.txt" })), /a.txt/);
    assert.match(JSON.stringify(await call("grep", { pattern: "gamma" })), /gamma/);
    assert.equal((await call("write", { path: "/mnt/results/forbidden", content: "no" })).isError, true);
    fs.writeFileSync(path.join(root, "host-secret"), "host-only");
    assert.equal((await call("read", { path: path.join(root, "host-secret") })).isError, true);
    const large = await call("bash", { command: "python -c 'print(\"x\"*20000)'" });
    const stored = (large.details as { storage_path: string }).storage_path;
    assert.match(stored, /^\/mnt\/results\/res_.*\.txt$/);
    assert.equal(fs.readFileSync(path.join(dirs[1], path.basename(stored))).length, 20001);
    const consumed = await call("bash", { command: `python -c 'from pathlib import Path; print(len(Path("${stored}").read_text()))'` });
    assert.match(JSON.stringify(consumed), /20001/);
  } finally { await manager.close(); fs.rmSync(root, { recursive: true, force: true }); }
});

test("read keeps small files clean and references long JSON without copying it", {skip:!image}, async () => {
  const {root,dirs,manager}=fixture();
  const tools=createSandboxTools(manager,{resultsDir:dirs[1],timeoutMs:15000,inlineBytes:1024});
  const call=async (name:string,args:Record<string,unknown>)=>tools.find(t=>t.name===name)!.execute("read-test",args,new AbortController().signal,undefined,{} as never);
  const raw=JSON.stringify({rows:Array.from({length:1000},(_,id)=>({id,label:"数据".repeat(6)}))});
  fs.writeFileSync(path.join(dirs[1],"compact.json"),raw);
  const files=fs.readdirSync(dirs[1]).sort();
  try {
    const small=await call("read",{path:"/mnt/results/result.json"});
    assert.equal((small.content[0] as any).text,'{"rows":[1,2,3]}');
    assert.equal((small.details as any).storage_path,undefined);
    const large=await call("read",{path:"/mnt/results/compact.json",offset:1,limit:1});
    assert.equal((large.details as any).storage_path,"/mnt/results/compact.json");
    assert.match((large.content[0] as any).text,/Read preview truncated/);
    const tail=await call("bash",{command:`python -c 'import json; print(json.load(open("/mnt/results/compact.json"))["rows"][-1]["id"])'`});
    assert.equal((tail.content[0] as any).text,'999\n');
    assert.equal((await call("read",{path:"missing.txt"})).isError,true);
    assert.equal((await call("bash",{command:"echo small-error >&2; exit 2"})).isError,true);
    assert.deepEqual(fs.readdirSync(dirs[1]).sort(),files);
  } finally {await manager.close();fs.rmSync(root,{recursive:true,force:true});}
});
