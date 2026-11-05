import fs from "node:fs";
import path from "node:path";

export function sandboxFixture(root: string) {
  const results = `${root}-results`;
  fs.mkdirSync(results, { recursive: true });
  return { results_dir: results, sandbox: {
    runId: path.basename(root), image: process.env.DATAAGENT_SANDBOX_TEST_IMAGE || "oda-sandbox:test",
    cli: "docker" as const, uid: process.getuid!(), gid: process.getgid!(), lifetimeSeconds: 60, memory: "512m", cpus: "1", pidsLimit: 128,
    workspaceSource: fs.realpathSync(root), resultsSource: fs.realpathSync(results), skills: [],
  } };
}
