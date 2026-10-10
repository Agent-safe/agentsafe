import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildPoseidon } from "circomlibjs";
import * as snarkjs from "snarkjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const buildDir = path.join(here, "build");
const wasm = path.join(buildDir, "delegation_js", "delegation.wasm");
const zkey = path.join(buildDir, "delegation_final.zkey");
const vkeyPath = path.join(buildDir, "verification_key.json");
const FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617n;
const MAX_SCOPE = 8;
let poseidon;

function hashText(domain, text) {
  const digest = crypto.createHash("sha256").update(`${domain}\0${text}`).digest("hex");
  const value = BigInt(`0x${digest}`) % FIELD;
  return value === 0n ? 1n : value;
}

function numberField(value) {
  const n = BigInt(value);
  if (n < 0n || n >= FIELD) throw new Error("integer does not fit the circuit field");
  return n;
}

function nonceField(value) {
  const raw = BigInt(value.startsWith("0x") ? value : `0x${value}`);
  return raw % FIELD;
}

async function fieldHash(values) {
  poseidon ??= await buildPoseidon();
  return poseidon.F.toString(poseidon(values.map((value) => BigInt(value))));
}

function paddedHashes(values, domain) {
  if (!Array.isArray(values) || values.length > MAX_SCOPE) {
    throw new Error(`scope must contain at most ${MAX_SCOPE} entries`);
  }
  const hashes = values.map((value) => hashText(domain, String(value)).toString());
  return hashes.concat(Array(MAX_SCOPE - hashes.length).fill("0"));
}

async function mandateCommitment(mandate) {
  const actions = paddedHashes(mandate.actions, "agentsafe:action:v1");
  const resources = paddedHashes(mandate.resources, "agentsafe:resource:v1");
  const conditionValues = Object.entries(mandate.parameterConstraints ?? {}).map(([key, value]) => `${key}=${JSON.stringify(value)}`).sort();
  const conditions = paddedHashes(conditionValues, "agentsafe:condition:v1");
  const scope = await fieldHash([...actions, ...resources]);
  const conditionHash = await fieldHash(conditions);
  const policyScope = await fieldHash([scope, conditionHash]);
  const missionId = hashText("agentsafe:mission:v1", mandate.missionId).toString();
  const agentId = hashText("agentsafe:agent-key:v1", mandate.agentPublicKey).toString();
  const budget = numberField(mandate.budgetCents).toString();
  const expiry = numberField(mandate.expiresAt).toString();
  const canDelegate = mandate.canDelegate ? "1" : "0";
  const allowExternal = mandate.allowExternal ? "1" : "0";
  const nonce = nonceField(mandate.nonce).toString();
  const commitment = await fieldHash([missionId, agentId, policyScope, budget, expiry, canDelegate, allowExternal, nonce]);
  return { commitment, missionId, agentId, actions, resources, conditions, scope, budget, expiry, canDelegate, allowExternal, nonce };
}

async function prove(input) {
  if (![wasm, zkey, vkeyPath].every((file) => fs.existsSync(file))) {
    throw new Error("Groth16 setup missing. Run `cd zk && npm run setup` first.");
  }
  const parent = await mandateCommitment(input.parent);
  const child = await mandateCommitment(input.child);
  const witness = {
    missionId: parent.missionId,
    parentAgentId: parent.agentId,
    childAgentId: child.agentId,
    parentActions: parent.actions,
    childActions: child.actions,
    parentResources: parent.resources,
    childResources: child.resources,
    parentConditions: parent.conditions,
    childConditions: child.conditions,
    parentBudgetCents: parent.budget,
    childBudgetCents: child.budget,
    parentExpiresAt: parent.expiry,
    childExpiresAt: child.expiry,
    parentCanDelegate: parent.canDelegate,
    childCanDelegate: child.canDelegate,
    parentAllowExternal: parent.allowExternal,
    childAllowExternal: child.allowExternal,
    parentNonce: parent.nonce,
    childNonce: child.nonce,
  };
  const originalLog = console.log;
  console.log = () => {};
  let generated;
  try {
    console.error("Generating Groth16 proof in single-thread mode...");
    generated = await snarkjs.groth16.fullProve(witness, wasm, zkey, undefined, { singleThread: true, memorySize: 0 }, { singleThread: true });
    console.error("Proof generated; verifying it...");
  } finally {
    console.log = originalLog;
  }
  const publicSignals = generated.publicSignals.map(String);
  const expected = [parent.commitment, child.commitment, parent.missionId, parent.agentId, child.agentId];
  if (JSON.stringify(publicSignals) !== JSON.stringify(expected)) {
    throw new Error("proof public signals do not match the signed mission and grant commitments");
  }
  const vkey = JSON.parse(fs.readFileSync(vkeyPath, "utf8"));
  if (!(await snarkjs.groth16.verify(vkey, publicSignals, generated.proof))) {
    throw new Error("generated Groth16 proof failed local verification");
  }
  const proofBody = JSON.stringify(generated.proof);
  return {
    protocol: "Groth16/bn128",
    circuit: "delegation-subset-v1",
    verified: true,
    parentCommitment: parent.commitment,
    childCommitment: child.commitment,
    publicSignals,
    proof: generated.proof,
    proofSha256: crypto.createHash("sha256").update(proofBody).digest("hex"),
    verificationKeySha256: crypto.createHash("sha256").update(fs.readFileSync(vkeyPath)).digest("hex"),
    setup: "local development setup; not a production ceremony",
  };
}

async function verify(proof) {
  if (!fs.existsSync(vkeyPath)) throw new Error("verification key missing; run `cd zk && npm run setup`");
  const vkey = JSON.parse(fs.readFileSync(vkeyPath, "utf8"));
  const valid = await snarkjs.groth16.verify(vkey, proof.publicSignals, proof.proof);
  return { valid, protocol: "Groth16/bn128", circuit: "delegation-subset-v1" };
}

async function main() {
  const mode = process.argv[2];
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const input = chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : {};
  if (mode === "commitment") {
    const value = await mandateCommitment(input);
    return { commitment: value.commitment };
  } else if (mode === "prove") {
    return await prove(input);
  } else if (mode === "verify") {
    return await verify(input);
  } else if (mode === "self-check") {
    return { setupReady: [wasm, zkey, vkeyPath].every((file) => fs.existsSync(file)), protocol: "Groth16/bn128", circuit: "delegation-subset-v1" };
  } else {
    throw new Error("mode must be commitment, prove, verify, or self-check");
  }
}

main().then((result) => {
  process.stdout.write(JSON.stringify(result), () => process.exit(0));
}).catch((error) => {
  process.stdout.write(JSON.stringify({ error: error instanceof Error ? error.message : "ZK operation failed" }), () => process.exit(1));
});
