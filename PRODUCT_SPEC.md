# AgentSafe Product Specification

## Product

AgentSafe is a control and audit layer for existing AI agents. It governs mission-scoped permissions, delegation, tool access, budgets, data movement, and required approvals. An agent proposes an action; the controller checks the signed mandate before a protected connector can run. Actions that bypass the controller are outside the protection boundary.

The console separates execution relationships (agents, tools, transfers) from authorization relationships (who delegated which rights). An operator can suspend a mission or revoke a mandate to stop future actions; revocation cannot undo completed actions.

## Demo company

The hosted demonstration represents a fictional company and uses synthetic agent identities, invoices, policy notes, and connector results. State is persisted in the configured database and is read back from the API; it is not a hard-coded activity feed. The mock connectors write local demo artifacts only. They do not send messages, contact suppliers, or move money.

## Product guarantees demonstrated

- Each protected operation is tied to a mission and signed mandate scoped by tools, resources, budgets, expiry, and delegation rights.
- A child mandate cannot expand the encoded parent scope, budget, expiry, or delegation/data flags.
- Cumulative budgets are checked atomically; request identifiers cannot be replayed.
- Confidential data transfers are checked against declared labels and destinations.
- A required exact-operation approval must exist and be unused before the protected operation is allowed.
- The allow/deny decision is durably journaled before connector execution. Journal-write failure blocks the action.
- Signed receipts describe what the controller checked or observed. They do not prove model reasoning or the truth of a tool response.

## Demo journey

1. Create a mission mandate with a tool scope, resources, limits, budget, expiry, and delegation setting.
2. Delegate a narrower child mandate. The prototype generates and independently verifies a Groth16 proof for the encoded parent-child relation.
3. Run a workflow. The AWS path uses three specialist agents to produce a proposal; the AgentSafe controller independently evaluates it. The local path runs deterministic policy cases without an LLM call.
4. Inspect an allowed internal action and blocked confidential external transfer, missing approval, budget overrun, replay, or post-revocation action.
5. Inspect the signed JSONL audit events, verify the journal, and use the kill switch to stop future actions.

## AI workflow and model routing

Amazon Bedrock AgentCore Runtime hosts three sequential Strands agents: Research Agent, Security Analyst Agent, and Action Planner Agent. Amazon Bedrock runs inference. Amazon Titan creates embeddings and OpenSearch Serverless retrieves synthetic policy guidance. The runtime can propose actions but has no business-tool credentials; FastAPI remains the authorization boundary.

Four Bedrock models are configured as a fallback chain, in provider order: Claude Sonnet 4.5, Claude Haiku 4.5, Claude Sonnet 4.6, then Mistral Devstral 2 123B. AWS Converse smoke checks in the workshop account returned `OK` for each. Role preferences specialize the primary model: Sonnet 4.5 for research, Sonnet 4.6 for security review, and Haiku 4.5 for structured planning. Each role then tries the remaining configured models in order. Empty output and invalid planner JSON trigger fallback. The interface records which model served each role and which earlier attempts failed.

Model access was verified in the AWS workshop session; that does not by itself mean the AgentCore runtime and OpenSearch collection are deployed or reachable from Vercel. The dashboard must continue to label the live AWS workflow unavailable until runtime, index, and Vercel-to-AWS identity are all configured and a real end-to-end call succeeds.

## Components

| Component | Prototype technology | Responsibility |
| --- | --- | --- |
| Controller and API | Python, FastAPI, Pydantic | Validate signed mandates and requests, enforce rules, call only local demo connectors |
| Durable state | Supabase PostgreSQL via the session pooler when hosted; SQLite for isolated local development | Store mandates, budgets, alerts, approvals, artifacts, and journal events |
| Authorization signatures | Ed25519 | Sign mandates, agent requests, and controller receipts |
| Event integrity | SHA-256 chain and Ed25519 signatures | Detect edits to exported event history |
| Delegation privacy | Circom 2, circomlib/Poseidon, Groth16, snarkjs | Prove the bounded parent-child relation at delegation time |
| Agent orchestration | AWS Bedrock AgentCore with Strands Agents | Run specialist proposal agents in the AWS-backed path |
| Policy retrieval | Bedrock Titan embeddings and OpenSearch Serverless | Retrieve synthetic rule guidance for the agents |
| Dashboard | HTML, CSS, JavaScript served by FastAPI | Separate mission, workflow, access, audit, and response views |
| Hosted demo | Vercel | Serve the controller and console; isolate Node proof/AWS bridge functions |

Private signing keys stay with the signing components, not in prompts. Raw document and prompt content is not copied into the audit journal. Low-entropy private values are represented using keyed commitments.

## Security and product limits

The journal is signed and hash-chained, but an administrator who controls the database can delete an uncheckpointed tail. A signature authenticates a data label supplied by a caller; it does not prove that the source classified the data correctly. The one-party Groth16 development setup is not an audited trusted setup. Human approvals are demo interactions, not enterprise identity management. Group member names are a roster; the prototype does not mint per-member cryptographic credentials. JSONL export is SIEM-friendly output, not a live Splunk, Sentinel, or Datadog integration. No real company connector is enabled.

The audit screen anchors a salted journal-head commitment to the deployed Fuji `AuditAnchor` contract through MetaMask. The controller confirms it only after checking the transaction receipt and emitted root event. This proves that the commitment was recorded at a chain height; it does not make the private journal contents public or prove that tool execution was truthful.

## Current deployment boundary

The `controller/` app is the intended hosted product. It includes the FastAPI controller, console, PostgreSQL persistence, separate Groth16 endpoint, and AWS bridge. Deployment state and end-to-end AWS readiness must be verified against the current Vercel production deployment before describing the AWS path as live. Keep documented status tied to a production health check and a successful workflow invocation.
