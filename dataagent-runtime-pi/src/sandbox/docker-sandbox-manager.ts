import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { logDiagnostic } from "../protocol/channel.js";

export interface SandboxConfig {
  runId: string;
  image: string;
  cli: "docker";
  uid: number;
  gid: number;
  lifetimeSeconds: number;
  memory: string;
  cpus: string;
  pidsLimit: number;
  /** Enable Docker's default bridge network; false isolates the sandbox. */
  networkEnabled?: boolean;
  workspaceSource: string;
  resultsSource: string;
  skills: Array<{ source: string; folder: string }>;
  env?: Record<string, string>;
}

/** One container per execution segment. Model paths never reach host fs APIs. */
export class DockerSandboxManager {
  readonly containerName: string;
  private startPromise?: Promise<void>;
  private closed = false;
  private closePromise?: Promise<void>;

  constructor(private readonly config: SandboxConfig) {
    this.containerName = "oda-sandbox-" + createHash("sha256").update(config.runId).digest("hex").slice(0, 24);
    // Root in the sandbox would own files on the bind-mounted host workspace.
    if (!Number.isInteger(config.uid) || !Number.isInteger(config.gid) || config.uid <= 0 || config.gid <= 0) {
      throw new Error("Invalid sandbox UID/GID");
    }
    if (!Number.isFinite(config.lifetimeSeconds) || config.lifetimeSeconds <= 0) {
      throw new Error("Invalid sandbox lifetime");
    }
    if (!/^\d+[bkmg]?$/i.test(config.memory) || !/^\d+(\.\d+)?$/.test(config.cpus) ||
      !Number.isInteger(config.pidsLimit) || config.pidsLimit <= 0) {
      throw new Error("Invalid sandbox resource limits");
    }
    if (config.networkEnabled !== undefined && typeof config.networkEnabled !== "boolean") {
      throw new Error("Invalid sandbox networkEnabled: expected a boolean");
    }
    for (const skill of config.skills) {
      if (!/^[\w-]+$/.test(skill.folder)) throw new Error("Invalid skill folder");
    }
    // --mount uses commas as delimiters; never allow a source to inject options.
    for (const source of [config.workspaceSource, config.resultsSource, ...config.skills.map(s => s.source)]) {
      if (!source.startsWith("/") || /[,\n\r\0]/.test(source)) throw new Error("Invalid sandbox mount source");
    }
  }

  private command(args: string[], onData: (data: Buffer) => void | Promise<void> = () => {}, timeoutMs = 30_000, input?: string): Promise<number> {
    return new Promise((resolve, reject) => {
      const child = spawn(this.config.cli, args, { stdio: ["pipe", "pipe", "pipe"] });
      child.stdin.on("error", () => {}); // close/error is reported by the child.
      child.stdin.end(input);
      const timer = timeoutMs > 0 ? setTimeout(() => { child.kill("SIGKILL"); reject(new Error("Container CLI timed out")); }, timeoutMs) : undefined;
      const consume = (stream: typeof child.stdout) => {
        stream.on("data", (data: Buffer) => {
          stream.pause();
          Promise.resolve(onData(data)).then(() => stream.resume(), error => { child.kill("SIGKILL"); reject(error); });
        });
      };
      consume(child.stdout);
      consume(child.stderr);
      child.once("error", error => { clearTimeout(timer); reject(error); });
      child.once("close", code => { clearTimeout(timer); resolve(code ?? 1); });
    });
  }

  private async start(): Promise<void> {
    if (this.closed) throw new Error("SANDBOX_CLOSED");
    this.startPromise ??= (async () => {
      const c = this.config;
      const network = c.networkEnabled === true ? "bridge" : "none";
      // Lands in the backend log as pi_cell.stderr, so the effective network is never a guess.
      logDiagnostic(`sandbox start name=${this.containerName} network=${network} networkEnabled=${String(c.networkEnabled)}`);
      const args = ["run", "-d", "--rm", "--pull=never", "--name", this.containerName,
        `--network=${network}`, "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        `--memory=${c.memory}`, `--memory-swap=${c.memory}`, `--cpus=${c.cpus}`, `--pids-limit=${c.pidsLimit}`,
        "--user", `${c.uid}:${c.gid}`,
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,mode=1777", "--workdir", "/mnt/workspace",
        "--mount", `type=bind,src=${c.workspaceSource},dst=/mnt/workspace`,
        "--mount", `type=bind,src=${c.resultsSource},dst=/mnt/results,readonly`];
      for (const skill of c.skills) args.push("--mount", `type=bind,src=${skill.source},dst=/opt/skills/${skill.folder},readonly`);
      for (const [key, value] of Object.entries(c.env ?? {})) {
        if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) throw new Error("Invalid sandbox environment key");
        args.push("--env", `${key}=${value}`);
      }
      args.push(c.image, "sleep", String(Math.ceil(c.lifetimeSeconds)));
      let error = "";
      try {
        const code = await this.command(args, data => { error = (error + data.toString()).slice(-4096); });
        if (code !== 0) throw new Error(error);
      } catch (cause) {
        throw new Error("SANDBOX_UNAVAILABLE: " + String(cause));
      }
    })();
    await this.startPromise;
    if (this.closed) throw new Error("SANDBOX_CLOSED");
  }

  async execute(argv: string[], options: {
    signal?: AbortSignal;
    timeoutMs: number;
    onData: (data: Buffer) => void | Promise<void>;
    input?: string;
  }): Promise<number> {
    if (!Number.isFinite(options.timeoutMs) || options.timeoutMs <= 0) throw new Error("Invalid command timeout");
    if (options.signal?.aborted) throw new Error("SANDBOX_CANCELLED");
    // Cancellation covers container startup as well as exec. close() waits for
    // an in-flight start, then removes it, so a late run cannot leak a container.
    // The command timeout starts only once the container is up: startup has its
    // own CLI timeout, and a slow daemon must not spend the command's budget.
    let timer: NodeJS.Timeout | undefined;
    let reason: string | undefined;
    let rejectStopped: (error: Error) => void = () => {};
    const stopped = new Promise<never>((_, reject) => { rejectStopped = reject; });
    const stop = (message: string) => {
      if (reason) return;
      reason = message;
      void this.close().then(() => rejectStopped(new Error(message)), rejectStopped);
    };
    const cancel = () => stop("SANDBOX_CANCELLED");
    options.signal?.addEventListener("abort", cancel, { once: true });
    const run = async () => {
      try { await this.start(); } catch (error) { if (reason) return stopped; throw error; }
      if (reason) return stopped;
      timer = setTimeout(() => stop("SANDBOX_COMMAND_TIMEOUT"), options.timeoutMs);
      const code = await this.command(["exec", ...(options.input === undefined ? [] : ["-i"]), "--workdir", "/mnt/workspace", this.containerName, ...argv], options.onData, 0, options.input);
      if (reason) return stopped;
      return code;
    };
    try { return await Promise.race([run(), stopped]); }
    finally {
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", cancel);
    }
  }

  async close(): Promise<void> {
    this.closePromise ??= this.remove();
    return this.closePromise;
  }

  private async remove(): Promise<void> {
    this.closed = true;
    if (!this.startPromise) return;
    try { await this.startPromise; } catch { /* A failed CLI may still have created the container. */ }
    const code = await this.command(["rm", "-f", this.containerName]);
    if (code !== 0) {
      // --rm may already have removed an expired container. Verify absence;
      // permission or daemon errors must not masquerade as successful cleanup.
      let output = "";
      const status = await this.command(["ps", "-aq", "--filter", `name=^/${this.containerName}$`], b => { output += b; });
      if (status !== 0 || output.trim()) throw new Error("SANDBOX_CLEANUP_FAILED");
    }
  }
}
