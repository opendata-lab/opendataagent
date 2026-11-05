import fs from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";

/** Persist exactly the received text or JSON, without discarding metadata. */
export async function saveResult(resultsDir: string, raw: string) {
  let format = "text";
  try { JSON.parse(raw); format = "json"; } catch { /* UTF-8 text */ }
  await fs.mkdir(resultsDir, { recursive: true });
  const resultRef = `res_${randomUUID().replace(/-/g, "")}`;
  const name = `${resultRef}.${format === "json" ? "json" : "txt"}`;
  await fs.writeFile(path.join(resultsDir, name), raw, { encoding: "utf8", flag: "wx" });
  return { result_ref: resultRef, file_path: path.join(resultsDir, name), relative_path: `/mnt/results/${name}`,
    byte_size: Buffer.byteLength(raw), format };
}
