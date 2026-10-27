/**
 * The shell environment handed to a model-driven command.
 *
 * The Cell's own environment carries provider API keys and database
 * credentials. Passing a copy of it to `bash -c` would make every one of them
 * readable with `env`, by a command the model chose. These tests pin the
 * allowlist so that stays impossible.
 */

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { buildShellEnv, runShell, createTools } from "../src/tools/tool-registry.js";
import { WorkspaceBoundaryEnforcer, type BoundaryPolicy } from "../src/policy/workspace-boundary-enforcer.js";

const SECRET_ENV = {
  PATH: "/usr/bin:/bin",
  HOME: "/home/agent",
  DATAAGENT_PYTHON_BIN: "/usr/bin/python3",
  DATAAGENT_SKILL_ROOT: "/skills/nl2sql",
  DATAAGENT_PLATFORM_SKILL_ROOT: "/skills/platform-tools",
  SKILLS_ROOT_DIR: "/skills",
  ANTHROPIC_API_KEY: "sk-secret-anthropic",
  OPENAI_API_KEY: "sk-secret-openai",
  MYSQL_PASSWORD: "dataagent123",
  DATAAGENT_RUNTIME_SECRET: "shared-hmac-secret",
  AWS_SECRET_ACCESS_KEY: "aws-secret",
} as NodeJS.ProcessEnv;

test("shell env carries only allowlisted variables", () => {
  const env = buildShellEnv(SECRET_ENV, {});

  assert.equal(env.PATH, "/usr/bin:/bin");
  assert.equal(env.HOME, "/home/agent");
  assert.equal(env.DATAAGENT_PYTHON_BIN, "/usr/bin/python3");
  assert.equal(env.DATAAGENT_SKILL_ROOT, "/skills/nl2sql");
  // 平台专属锚点已随调用契约统一移除，即使上游还塞进来也不该透传
  assert.equal(env.DATAAGENT_PLATFORM_SKILL_ROOT, undefined);
  assert.equal(env.SKILLS_ROOT_DIR, "/skills");
});

test("provider and database credentials never reach the shell", () => {
  const env = buildShellEnv(SECRET_ENV, {});

  for (const leaked of [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "MYSQL_PASSWORD",
    "DATAAGENT_RUNTIME_SECRET",
    "AWS_SECRET_ACCESS_KEY",
  ]) {
    assert.equal(env[leaked], undefined, `${leaked} must not be visible to a model-driven shell`);
  }
  const serialized = JSON.stringify(env);
  assert.ok(!serialized.includes("sk-secret"), "no provider key may appear in the shell env");
  assert.ok(!serialized.includes("dataagent123"), "no database password may appear in the shell env");
});

test("runtime_env cannot smuggle a non-allowlisted variable through", () => {
  // The control plane's runtime_env is additive but still filtered: a bug or a
  // compromised profile upstream must not be able to widen the shell's view.
  const env = buildShellEnv(SECRET_ENV, {
    DATAAGENT_PYTHON_BIN: "/opt/py/bin/python3",
    ANTHROPIC_API_KEY: "sk-injected",
    EVIL_VAR: "x",
  });

  assert.equal(env.DATAAGENT_PYTHON_BIN, "/opt/py/bin/python3", "allowlisted value may be overridden");
  assert.equal(env.ANTHROPIC_API_KEY, undefined);
  assert.equal(env.EVIL_VAR, undefined);
});

test("a command really cannot read a secret from its environment", async () => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "dataagent-shell-"));
  const result = await runShell("echo \"key=${ANTHROPIC_API_KEY:-ABSENT} pw=${MYSQL_PASSWORD:-ABSENT}\"", {
    cwd,
    env: buildShellEnv(SECRET_ENV, {}),
    timeoutMs: 10_000,
  });

  assert.equal(result.exitCode, 0);
  assert.match(result.stdout, /key=ABSENT/);
  assert.match(result.stdout, /pw=ABSENT/);
});

test("a shell command exceeding its timeout is killed", async () => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "dataagent-shell-"));
  const result = await runShell("sleep 30", {
    cwd,
    env: buildShellEnv(SECRET_ENV, {}),
    timeoutMs: 300,
  });

  assert.equal(result.timedOut, true);
});

for (const mode of ["timeout", "cancel"] as const) {
  test(`${mode} stops background descendants and closes their output pipes`, async (t) => {
    const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "dataagent-shell-group-"));
    t.after(() => fs.rmSync(cwd, { recursive: true, force: true }));
    const controller = new AbortController();
    const timer = mode === "cancel" ? setTimeout(() => controller.abort(), 50) : undefined;
    t.after(() => clearTimeout(timer));
    const started = performance.now();
    const result = await runShell("(sleep 0.4; echo survived > child-survived) & wait", {
      cwd, env: buildShellEnv(SECRET_ENV, {}),
      timeoutMs: mode === "timeout" ? 50 : 3000,
      signal: controller.signal,
    });
    assert.ok(performance.now() - started < 2000, "must not wait out the full command budget");
    assert.equal(result.timedOut, mode === "timeout");
    // Returning early alone is insufficient: no descendant may write later.
    await new Promise((resolve) => setTimeout(resolve, 500));
    assert.equal(fs.existsSync(path.join(cwd, "child-survived")), false);
  });
}

test("an already cancelled command is never spawned", async (t) => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "dataagent-shell-cancelled-"));
  t.after(() => fs.rmSync(cwd, { recursive: true, force: true }));
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(runShell("echo ran > should-not-exist", {
    cwd, env: buildShellEnv(SECRET_ENV, {}), timeoutMs: 3000, signal: controller.signal,
  }), /aborted before start/);
  assert.equal(fs.existsSync(path.join(cwd, "should-not-exist")), false);
});

test("tools refuse a path outside the workspace before touching the filesystem", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dataagent-tools-"));
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "dataagent-outside-"));
  fs.writeFileSync(path.join(outside, "secret.txt"), "top secret", "utf-8");

  const policy: BoundaryPolicy = {
    policy_version: 1,
    profile: "pi_agent_core",
    workspace_root: root,
    allowed_roots: [root],
    allowed_executables: [],
    discard_sinks: ["/dev/null"],
    tool_path_keys: { Read: ["file_path"], LS: ["path"] },
    operator_chars: "();<>|&",
    tool_result_root: null,
    readonly_commands: [],
  };

  const tools = createTools({
    toolTimeoutMs: 30_000,
    boundary: new WorkspaceBoundaryEnforcer(policy),
    workspaceRoot: root,
    runtimeEnv: {},
  });

  // A denial must *throw*. AgentToolResult has no isError field: the agent loop
  // sets isError only when execute throws, so returning { isError: true } was
  // silently discarded and a denied call reached the model and the chat UI
  // marked as a success.
  const read = tools.find((t) => t.name === "Read")!;
  await assert.rejects(
    () => read.execute("call-1", { file_path: path.join(outside, "secret.txt") }),
    /PATH_OUT_OF_SCOPE/
  );

  const bash = tools.find((t) => t.name === "Bash")!;
  await assert.rejects(
    () => bash.execute("call-2", { command: `cat ${outside}/secret.txt` }),
    /PATH_OUT_OF_SCOPE/
  );
});

test("a failing shell command throws so the model and the UI both see an error", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dataagent-tools-"));
  const policy: BoundaryPolicy = {
    policy_version: 1,
    profile: "pi_agent_core",
    workspace_root: root,
    allowed_roots: [root],
    allowed_executables: [],
    discard_sinks: ["/dev/null"],
    tool_path_keys: {},
    operator_chars: "();<>|&",
    tool_result_root: null,
    readonly_commands: [],
  };
  const tools = createTools({
    toolTimeoutMs: 30_000,
    boundary: new WorkspaceBoundaryEnforcer(policy),
    workspaceRoot: root,
    runtimeEnv: {},
  });
  const bash = tools.find((t) => t.name === "Bash")!;

  // The output has to survive: createErrorToolResult keeps only the message.
  await assert.rejects(
    () => bash.execute("call-3", { command: "echo before-failure; exit 7" }),
    (err: Error) => /退出码 7/.test(err.message) && /before-failure/.test(err.message)
  );

  const ok = await bash.execute("call-4", { command: "echo fine" });
  assert.match(ok.content[0].type === "text" ? ok.content[0].text : "", /fine/);
});

test("a failed command inside a pipeline is not masked by a successful final stage", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dataagent-tools-"));
  const policy: BoundaryPolicy = {
    policy_version: 1,
    profile: "pi_agent_core",
    workspace_root: root,
    allowed_roots: [root],
    allowed_executables: [],
    discard_sinks: ["/dev/null"],
    tool_path_keys: {},
    operator_chars: "();<>|&",
    tool_result_root: null,
    readonly_commands: [],
  };
  const tools = createTools({
    toolTimeoutMs: 30_000,
    boundary: new WorkspaceBoundaryEnforcer(policy),
    workspaceRoot: root,
    runtimeEnv: {},
  });
  const bash = tools.find((t) => t.name === "Bash")!;

  await assert.rejects(
    () => bash.execute("call-pipefail", { command: "(echo upstream-failure >&2; exit 9) | head -n 1" }),
    (err: Error) => /退出码 9/.test(err.message) && /upstream-failure/.test(err.message)
  );
});
