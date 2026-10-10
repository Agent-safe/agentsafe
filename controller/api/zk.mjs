import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const toolPath = path.join(projectRoot, "zk", "tool.mjs");
const allowedModes = new Set(["self-check", "commitment", "prove", "verify"]);

export const config = { maxDuration: 300 };

export default async function handler(request, response) {
  if (request.method !== "POST") {
    response.setHeader("Allow", "POST");
    response.status(405).json({ error: "method not allowed" });
    return;
  }

  const { mode, payload = {} } = request.body ?? {};
  if (!allowedModes.has(mode) || typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    response.status(400).json({ error: "invalid ZK request" });
    return;
  }

  const child = spawn(process.execPath, [toolPath, mode], {
    cwd: projectRoot,
    stdio: ["pipe", "pipe", "pipe"],
  });
  const chunks = [];
  let stderr = "";
  child.stdout.on("data", (chunk) => chunks.push(chunk));
  child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
  const exitCode = await new Promise((resolve, reject) => {
    child.on("error", reject);
    child.on("close", resolve);
    child.stdin.end(JSON.stringify(payload));
  }).catch(() => null);

  let result;
  try {
    result = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
  } catch {
    response.status(502).json({ error: "ZK tool returned invalid JSON" });
    return;
  }

  if (exitCode !== 0 || result.error) {
    response.status(422).json({ error: result.error || "ZK operation failed", detail: stderr.slice(-2000) });
    return;
  }
  response.status(200).json(result);
}
