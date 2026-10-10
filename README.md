# AgentSafe

AgentSafe is a local control layer for AI agents. An agent signs a proposed tool call; the controller checks its signed mandate, delegation scope, remaining budgets, data destination, approval requirements, expiry, and revocation state. Only an allowed request reaches a registered connector. The controller records the decision before execution and signs a receipt after it observes the result.

## Run the working local prototype

Requirements: Python 3.10+, Node.js 20+, npm, and [`uv`](https://docs.astral.sh/uv/).

```bash
cd zk
npm ci
npm run setup
cd ../controller
uv sync
uv run uvicorn app:app --host 127.0.0.1 --port 8765
```

`npm run setup` compiles the Circom delegation circuit and creates local Groth16 proving/verifying keys. This is a development-only one-party setup, not an audited production ceremony. The app fails closed if proof generation or independent verification fails.

Open <http://127.0.0.1:8765>. Click **Start policy demo**, inspect the real allow/block decisions, then explicitly click **Approve exact $70 local demo order**. The approved operation creates a local SQLite artifact; it does not contact a supplier or move money. Four SOC tabs expose persisted grants, group policies, signed JSONL audit events, and alerts/kill switches. `/docs` exposes the API.

Group policies create a signed group-principal mandate and shared mission budget. The MVP roster is descriptive only: member-specific credentials are not minted, so the product does not claim to enforce individual group membership.

The local controller is deliberately bound to loopback. State lives in `controller/.data/` by default; set `AGENTSAFE_DATA_DIR` to choose another local directory. The Ed25519 controller key is generated on first run and kept in that directory. Never commit or share it. Browser-driven state changes reject cross-origin requests.

## Hosted hackathon demo

The deployed controller and console are available at <https://agentsafe-nu.vercel.app>. Its FastAPI controller runs as a Python function and stores state in Neon PostgreSQL. Groth16 proof generation and verification run in a separate Node.js function at `/api/zk`, so the Python function does not bundle a second Node runtime. The hosted page is publicly reachable; its agents and connector effects remain synthetic demo data.

## Run the checks

```bash
cd controller
uv run pytest -q
```

Export and verify the journal independently:

```bash
curl http://127.0.0.1:8765/journal/export > /tmp/agentsafe-journal.json
uv run python verify_export.py /tmp/agentsafe-journal.json
```

The verifier checks every event’s SHA-256 link and Ed25519 signature using the public key included in the export. It does not trust the controller’s `verification` field. Since there is no external checkpoint witness yet, someone who controls the database can delete an uncheckpointed tail; this is not an immutable archive.

## Connect an agent

Create a mission through `POST /missions` with an Ed25519 public key belonging to the root agent. The controller returns a signed mandate. When delegating, the parent agent signs the delegation claim with its private key; the controller checks that the child’s tools, resources, budget, duration, and delegation capability remain within the parent grant. A child signs every `POST /actions` request with the private key bound to its grant. Private agent keys must stay in the agent process or its signing service.

The prototype connectors are `records.share_local` and `procurement.create_order`; they save demo artifacts locally. The `procurement.create_order` connector requires an exact-parameter, one-use approval. The approval endpoint is a **demo local operator action**, not authenticated enterprise identity management. Add real connectors only behind `protected_action`, and keep their credentials unavailable to the agent runtime.

## What is implemented and what is not

- **Working now:** local FastAPI controller, Pydantic validation, SQLite transactions, Ed25519-signed mandates and agent requests, parent-signed scoped delegation, parent budget reservation, cumulative spend checks, expiry/revocation, one-use exact-parameter approval, fail-closed pre-execution journal write, two local-only connectors, signed receipts, hash-chained events, export verifier, and an interactive local console. The same controller is deployed at `https://agentsafe-nu.vercel.app` with Neon PostgreSQL persistence; its Groth16 endpoint runs as a separate Node.js function to keep the Python function bundle within Vercel's limit. The demo’s two deterministic agents and handoff run through a compiled LangGraph `StateGraph`; this is a repeatable policy test harness, not an LLM-generated run.
- **Working now:** Circom 2/Poseidon/Groth16 delegation proofs, independent snarkjs verification, the four SOC dashboard tabs, local JSONL audit export, alert records, manual grant-tree kill switch, and automatic grant-tree revocation after three denials within 60 seconds. Group mandates share a mission budget, but the roster is descriptive and does not issue member credentials.
- **Not implemented:** a production trusted setup; authenticated human approvers; trusted data-label issuers and label propagation from real sources; real business-system connectors; a separately stored signed checkpoint; enterprise key management or tenancy; or on-chain journal anchoring to Avalanche Fuji.
- **Data-label limit:** the prototype applies its policy to the signed request’s declared label. It does not prove that a real source classified the data correctly or propagate labels through an external agent’s internal transformations. Private resource and destination values are represented in the journal by HMAC commitments made with the local controller key, so an exported journal alone cannot brute-force common low-entropy values.
- **Receipt limit:** a receipt proves what this controller checked or observed. It does not prove that a model reasoned correctly or that an external tool’s response is true.

## Architecture and deployment status

The controller is the enforcement point. The agent must not hold direct credentials for protected tools; if a tool call bypasses the controller, AgentSafe cannot protect it. The hosted Vercel application runs the FastAPI controller and stores missions, grants, alerts, artifacts, and journal events in Neon PostgreSQL. Groth16 proof operations run through a separate Node.js Vercel function. Local development defaults to SQLite. The Ed25519 signing key is a sensitive Vercel environment variable in hosted mode.

The hosted demo still uses synthetic agents and side-effect-free connectors. It does not connect to real email, procurement, or business systems. The controller currently does not submit journal commitments to Avalanche Fuji; the contract is not an active part of the hosted action flow. Delegation ZK proofs are generated and independently verified using a one-party development setup, not production ceremony parameters. Do not present the deployment as production-ready enforcement for a company’s agents.

See [`PROJECT_GUIDE.md`](PROJECT_GUIDE.md) for a plain-language walkthrough and [`PRODUCT_SPEC.md`](PRODUCT_SPEC.md) for the MVP requirements and implementation status.
