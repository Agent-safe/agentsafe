# AgentSafe: what the project is and how to see it work

## The idea in plain language

An AI agent can have legitimate access to tools and still use them in a risky way: send confidential information to an outside address, spend more than its mission budget, or keep acting after its task was canceled. AgentSafe puts a controller between an agent and selected tools. The agent proposes an action; AgentSafe checks the authority and rules; the connector runs only after an allow decision has been written to the local journal.

Think of the controller as a security checkpoint with a signed work order. A blockchain is not what makes the checkpoint safe. This iteration uses local SQLite and Ed25519 signatures. Avalanche Fuji was used in an earlier demo to timestamp a hash; that chain action is separate from the new controller and does not enforce its rules.

## How the local demo works

The running prototype is under `controller/`, not the old Next.js demo. Agent A and Agent B are nodes in a compiled LangGraph `StateGraph`; a third node passes each proposed action through AgentSafe. This particular test graph uses a deterministic fixture, not Mistral, so its allowed and blocked outcomes are repeatable. Start it with:

```bash
cd controller
uv sync
uv run uvicorn app:app --host 127.0.0.1 --port 8765
```

Then open <http://127.0.0.1:8765> and click **Start policy demo**. The controller creates a mission for Agent A, signs its mandate, and accepts a delegation signed by A that gives Agent B a smaller budget and tool scope. Agent B signs each proposed action.

Before starting the controller for the first time, initialize its real delegation proof circuit from the repository root:

```bash
cd zk && npm ci && npm run setup
```

The setup creates local Groth16 keys for the compiled Circom circuit. A delegation is issued only after the proof is generated and independently checked by a separate snarkjs verifier process. The one-party development setup is not a production ceremony.

The first policy run shows:

1. A confidential record sent to an internal destination is allowed and saved as a local artifact.
2. The same confidential record sent to an outside destination is blocked.
3. A $70 mock purchase is blocked while its exact-operation approval is missing.

The run then pauses in the interface. Click **Approve exact $70 local demo order** to record a clear local demo approval. The controller checks the approval against the proposed parameters, consumes it once, and writes a local artifact. Agent B then tries another $20. The mission still has $30, but B’s delegated grant has only $10 left, so the request is blocked by the child grant’s remaining budget. Finally, the mission is revoked and a later action is blocked.

Every step creates signed controller events. The page shows the current mission and parent/child grant tree, not an invented prefilled activity feed. The API documentation is at <http://127.0.0.1:8765/docs>.

## What actually happens at an action

The controller verifies the caller’s Ed25519 signature against the public key in its grant; checks mission and grant status, expiry, agent identity, tool/resource scope, cumulative mission and grant budgets, destination/data label, and any exact-operation approval; then commits the decision inside a SQLite write transaction. A request ID can be used once. Only after that durable commit does a permitted connector run. A separate event records the result, and the controller returns a signed receipt.

If the journal write fails, the tool does not run. If execution outcome cannot be confirmed, the journal records `unknown`; the system does not quietly refund the budget. Revocation blocks future operations for all grants in the mission but does not undo earlier operations.

The current connectors are intentionally local: `records.share_local` and `procurement.create_order` write demo artifacts into SQLite. They do not send email, contact a supplier, or make a purchase. The approval button is an explicit local demo interaction, not an authenticated enterprise approver system.

## What the evidence means

Each event contains identifiers, policy outcome, keyed HMAC commitments for private resource and destination values, and a link to the preceding event; it does not copy the request payload or raw destination into the journal. Events are SHA-256 chained and Ed25519 signed. `GET /journal/export` downloads the records and public key. `controller/verify_export.py` validates the export independently of the running controller:

```bash
curl http://127.0.0.1:8765/journal/export > /tmp/agentsafe-journal.json
uv run python verify_export.py /tmp/agentsafe-journal.json
```

A valid signature means the event matches this controller’s signing key. It does not establish that an AI answer or external tool result is true. A database administrator could delete an uncheckpointed tail; the project does not claim this journal is inviolable.

## Current boundaries

- The local API and console are running, tested together, and backed by SQLite on this machine.
- The two demo agents and handoff are represented in LangGraph. The scenario is deliberately deterministic; it does not call an LLM.
- There is no real customer data, Gmail integration, external purchase tool, or business connector in the demo.
- Four SOC tabs show persisted grant inventory, group-principal policies and shared mission budgets, audit events/JSONL export, and active alerts with manual/automatic kill switches. Denials are classified as ASI02 tool misuse, ASI03 privilege/budget abuse, or crypto signature/replay alerts. Three denied calls by one grant within 60 seconds revoke that grant and descendants.
- A group policy has a signed group principal and shared budget, but listed member names are only a roster. Per-member cryptographic credentials and membership enforcement are not implemented.
- Data labels are supplied on signed requests. The prototype does not yet have trusted source connectors that assign labels or preserve them through real agent transformations.
- Delegation now generates a Circom/Poseidon/Groth16 zero-knowledge proof that the child mandate preserves the parent mission and does not expand its bounded scope, budget, expiry, or delegation/data flags. The API stores the proof and public signals and provides a separate verification endpoint. This proves only the encoded relation; it does not make the whole controller private or production-audited.
- Human approvals are local demo records. Enterprise identity, approver roles, MFA, and authenticated approval policy remain to be built.
- There is no independently held signed checkpoint for journal-tail deletion detection, no enterprise key manager, and no multi-tenant service.
- The deployed `agentsafe-nu.vercel.app` site is the earlier synthetic LangGraph dashboard. It is not connected to this SQLite controller. Do not use it as proof an operating agent is protected. The controller stays local because Vercel’s serverless disk is not the durable client-local database the design needs.

## Where to look in the code

- `controller/app.py` — API, mandate and agent-signature validation, delegation, policy checks, local connectors, receipts and event journal.
- `controller/ui/index.html` — interactive local console.
- `controller/verify_export.py` — independent signed-journal verifier.
- `controller/tests/test_controller.py` — end-to-end API checks, replay/tamper checks and cross-origin guard.
- `PRODUCT_SPEC.md` — product requirements and status.
- `app/api/simulate/agent.ts` — earlier LangGraph demo still used by the Vercel app; it is not the enforcement controller.
