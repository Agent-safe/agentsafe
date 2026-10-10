import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as snarkjs from "snarkjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const vkeyPath = path.join(here, "build", "verification_key.json");
const proofPath = process.argv[2];
if (!proofPath || !fs.existsSync(vkeyPath)) {
  console.error("usage: node verify.mjs <proof.json>; run npm run setup first");
  process.exit(2);
}
const payload = JSON.parse(fs.readFileSync(proofPath, "utf8"));
const verificationKey = JSON.parse(fs.readFileSync(vkeyPath, "utf8"));
const valid = await snarkjs.groth16.verify(verificationKey, payload.publicSignals, payload.proof);
process.stdout.write(JSON.stringify({ valid, protocol: "Groth16/bn128", circuit: "delegation-subset-v1" }), () => process.exit(valid ? 0 : 1));
