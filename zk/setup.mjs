import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const build = path.join(here, "build");
const bin = path.join(here, "node_modules", ".bin");
const circom = path.join(bin, "circom2");
const snark = path.join(bin, "snarkjs");
fs.mkdirSync(build, { recursive: true });

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd: here, stdio: ["ignore", "pipe", "pipe"] });
    let output = "";
    child.stdout.on("data", (part) => { output += part.toString(); });
    child.stderr.on("data", (part) => { output += part.toString(); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve(output);
      else reject(new Error(`${path.basename(command)} failed (${code}):\n${output.slice(-5000)}`));
    });
  });
}

const r1cs = path.join(build, "delegation.r1cs");
const pot0 = path.join(build, "pot14_0000.ptau");
const pot1 = path.join(build, "pot14_0001.ptau");
const potFinal = path.join(build, "pot14_final.ptau");
const zkey0 = path.join(build, "delegation_0000.zkey");
const zkeyFinal = path.join(build, "delegation_final.zkey");
const setupHashPath = path.join(build, "circuit.sha256");
const circuitHash = crypto.createHash("sha256").update(fs.readFileSync(path.join(here, "circuits", "delegation.circom"))).digest("hex");

if (fs.existsSync(zkeyFinal) && fs.existsSync(path.join(build, "verification_key.json")) && fs.existsSync(setupHashPath) && fs.readFileSync(setupHashPath, "utf8").trim() === circuitHash) {
  console.log("Groth16 development artifacts already exist.");
  process.exit(0);
}
for (const entry of fs.readdirSync(build)) fs.rmSync(path.join(build, entry), { recursive: true, force: true });

console.log("Compiling delegation subset circuit...");
console.log(await run(circom, ["circuits/delegation.circom", "--r1cs", "--wasm", "--sym", "-l", "node_modules", "--output", "build"]));
console.log("Creating a local-only Powers of Tau phase 1 contribution...");
console.log(await run(snark, ["powersoftau", "new", "bn128", "14", pot0]));
const entropy1 = crypto.randomBytes(64).toString("hex");
console.log(await run(snark, ["powersoftau", "contribute", pot0, pot1, "--name=AgentSafe local dev contribution", `-e=${entropy1}`]));
console.log("Preparing phase 2 and creating the circuit-specific Groth16 key...");
console.log(await run(snark, ["powersoftau", "prepare", "phase2", pot1, potFinal]));
console.log(await run(snark, ["groth16", "setup", r1cs, potFinal, zkey0]));
const entropy2 = crypto.randomBytes(64).toString("hex");
console.log(await run(snark, ["zkey", "contribute", zkey0, zkeyFinal, "--name=AgentSafe local dev contribution", `-e=${entropy2}`]));
console.log(await run(snark, ["zkey", "verify", r1cs, potFinal, zkeyFinal]));
console.log(await run(snark, ["zkey", "export", "verificationkey", zkeyFinal, path.join(build, "verification_key.json")]));

for (const file of [pot0, pot1, potFinal, zkey0]) fs.rmSync(file, { force: true });
fs.writeFileSync(setupHashPath, `${circuitHash}\n`);
console.log("Ready. This one-party local setup is for development only; it is not safe to deploy as production proving parameters.");
