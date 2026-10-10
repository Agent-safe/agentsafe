# AgentSafe

AgentSafe is a local control layer for AI agents. An agent signs a proposed tool call; the controller checks its signed mandate, delegation scope, remaining budgets, data destination, approval requirements, expiry, and revocation state. Only an allowed request reaches a registered connector. The controller records the decision before execution and signs a receipt after it observes the result.

## Run the working local prototype

Requirements: Python 3.10+ and [`uv`](https://docs.astral.sh/uv/).

```bash
cd controller
uv sync
uv run uvicorn app:app --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765>. Click **Start policy demo**, inspect the real allow/block decisions, then explicitly click **Approve exact $70 local demo order**. The approved operation creates a local SQLite artifact; it does not contact a supplier or move money. The page shows the root-to-child grant graph, local mission spend, signed journal state, and individual policy outcomes. `/docs` exposes the API.

The local controller is deliberately bound to loopback. State lives in `controller/.data/` by default; set `AGENTSAFE_DATA_DIR` to choose another local directory. The Ed25519 controller key is generated on first run and kept in that directory. Never commit or share it. Browser-driven state changes reject cross-origin requests.

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

- **Working now:** local FastAPI controller, Pydantic validation, SQLite transactions, Ed25519-signed mandates and agent requests, parent-signed scoped delegation, parent budget reservation, cumulative spend checks, expiry/revocation, one-use exact-parameter approval, fail-closed pre-execution journal write, two local-only connectors, signed receipts, hash-chained events, export verifier, and an interactive local console. The demo’s two deterministic agents and handoff run through a compiled LangGraph `StateGraph`; this is a repeatable policy test harness, not an LLM-generated run.
- **Not implemented:** Circom/Groth16 delegation proofs; a production trusted setup; authenticated human approvers; trusted data-label issuers and label propagation from real sources; real business-system connectors; a separately stored signed checkpoint; enterprise key management, tenancy, or a production deployment.
- **Data-label limit:** the prototype applies its policy to the signed request’s declared label. It does not prove that a real source classified the data correctly or propagate labels through an external agent’s internal transformations. Private resource and destination values are represented in the journal by HMAC commitments made with the local controller key, so an exported journal alone cannot brute-force common low-entropy values.
- **Receipt limit:** a receipt proves what this controller checked or observed. It does not prove that a model reasoned correctly or that an external tool’s response is true.

## Architecture and deployment status

The local controller is the enforcement point. The agent must not hold direct credentials for protected tools; if a tool call bypasses the controller, AgentSafe cannot protect it. SQLite and the signing key are designed to stay with this local process. The production Vercel site is still the earlier synthetic LangGraph dashboard; it does not host this controller, and its serverless filesystem is not the durable local SQLite deployment described here. Don’t treat the Vercel dashboard as evidence that an agent is protected.

See [`PROJECT_GUIDE.md`](PROJECT_GUIDE.md) for a plain-language walkthrough and [`PRODUCT_SPEC.md`](PRODUCT_SPEC.md) for the MVP requirements and implementation status.
