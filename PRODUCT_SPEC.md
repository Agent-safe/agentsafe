# AgentSafe product specification

## Product goal

Help a company see what its AI agents may do, stop unsafe actions before protected tools run, and retain evidence of the controller's decision.

AgentSafe is an authorization layer around existing agent workflows. Agents propose. AgentSafe checks the signature, mission, tool, resource, parameters, budget, expiry, revocation state, data destination, and required approval. A connector can run only after the controller durably records an allow decision.

## Demo tenant

The application uses **Acme Commerce**, a fictional online retailer. All agents, staff, supplier, invoice, policy notes, and business results in the tenant are synthetic. The controller database is shared and persistent. The available connectors do not call real business systems.

The named specialists are `ag-policy-researcher`, `ag-data-guardian`, and `ag-procurement-planner`. Their generated content remains untrusted input to the controller.

## Product workflow

```mermaid
sequenceDiagram
  participant A as Agent workflow
  participant C as AgentSafe controller
  participant G as Signed mission grant
  participant D as Demo connector
  participant J as PostgreSQL audit journal
  A->>C: Signed request with unique ID and exact parameters
  C->>G: Verify identity, signature, scope, and delegation
  C->>C: Check expiry, revocation, budget, data policy, approval, replay
  C->>J: Write decision before connector call
  alt Allowed and durable write succeeded
    C->>D: Execute the checked synthetic operation
    D-->>C: Return synthetic result
    C->>J: Record result and signed receipt
  else Denied or write failed
    C-->>A: Stop with a reason; no connector call
  end
```

## Requirements represented in the prototype

- A mission grant names an agent, tools, resources, limits, a cumulative budget, expiry, and delegation rights.
- A child grant cannot expand the constraints encoded by its parent.
- The delegation circuit creates a Groth16 proof and independently verifies it before issuing the delegated grant.
- Every protected request includes a unique ID and an Ed25519 signature.
- The controller checks replay, scope, expiration, revocation, budget, destination, and approval.
- Cumulative budgets are checked in a serialized database transaction.
- A decision is saved before connector execution. Persistence failure blocks the action.
- Results and receipts distinguish what the controller observed from what it cannot prove.
- Events are Ed25519-signed and linked by a SHA-256 hash chain; JSONL can be exported.
- A security operator can revoke future authority for a grant and its descendants.
- A salted journal fingerprint can be anchored to the real Avalanche Fuji testnet contract after explicit wallet confirmation.
- The live public AWS workflow is limited to ten runs per UTC day.

## AI workflow and current AWS runtime

The AWS Workshop role denies AgentCore runtime API access. The live path therefore uses AWS Lambda, LangGraph, and direct Amazon Bedrock Converse calls. No Vercel identity bridge is involved.

The graph runs these specialists in order:

1. `ag-policy-researcher` summarizes synthetic rules for the scenario.
2. `ag-data-guardian` reviews the requested transfer destination.
3. `ag-procurement-planner` returns an untrusted action proposal.

Each specialist uses this fallback order:

1. `us.anthropic.claude-sonnet-4-5-20250929-v1:0`
2. `us.mistral.pixtral-large-2502-v1:0`
3. `us.amazon.nova-lite-v1:0`
4. `us.anthropic.claude-haiku-4-5-20251001-v1:0`
5. `global.amazon.nova-2-lite-v1:0`

If a model fails or returns unusable content, the next model is tried. If all five fail, the workflow stops and no proposal is authorized. A valid proposal still has to pass the separate deterministic controller. OpenSearch is not provisioned; synthetic policy notes are bundled with the service. The product does not claim vector retrieval.

## Screens

| Screen | Purpose |
| --- | --- |
| Overview | Show the company context, recent actions, and control health |
| Run a workflow | Choose Bedrock-backed specialist calls or deterministic policy checks |
| Agent permissions | Inspect a grant, its limits, and revoke it |
| Groups & missions | Review mission budgets and synthetic group rosters |
| Audit evidence | Verify signatures and event hashes, export JSONL, and optionally anchor a checkpoint |
| Alerts & response | Explain blocked requests and stop future actions |

## Technical components

| Concern | Prototype implementation |
| --- | --- |
| UI | React, TypeScript, Material UI, Vite |
| API and policy controller | Python, FastAPI, Pydantic |
| Hosted persistence | Supabase PostgreSQL session pooler |
| Local isolated storage | SQLite |
| Hosted runtime | AWS Lambda container |
| Agent graph | LangGraph |
| Inference | Amazon Bedrock Converse with five model fallbacks |
| Mandate and receipt authentication | Ed25519 |
| Journal linkage | SHA-256 hash chain |
| Delegation proof | Circom 2, Poseidon, Groth16, snarkjs |
| Optional external checkpoint | Avalanche Fuji `AuditAnchor` |

The Lambda function reads only the database URL and signing key from a Secrets Manager secret. Its role is scoped to that secret, CloudWatch log writes, and the five Bedrock model families. The browser does not receive secret material.

## Out of scope and limitations

- No actual email, purchase, finance, supplier, identity-provider, SIEM, or SOAR integration is enabled.
- The fictional company profile is not connected to an external company's private network or systems.
- Human approval is a synthetic demonstration and does not authenticate a real employee.
- Group rosters do not issue individual cryptographic credentials or prove a shared group budget across real agents.
- Data labels are supplied by trusted integration code; the signature cannot prove that the initial label is correct.
- A journal chain detects modification of retained history but cannot stop an administrator deleting an unanchored tail.
- A Fuji checkpoint proves a commitment was recorded on a test network, not that an event was true or a policy was correct.
- The one-party Groth16 setup is for development, not an audited production trusted setup.
- Availability, key rotation/recovery, tenant isolation, authenticated administration, and independent security review are not production complete.

## Success criteria

The demo is complete when the AWS URL serves the Material UI dashboard, reads the persisted Acme company state, reports the live Bedrock/LangGraph route truthfully, completes a real model-backed workflow, receives a blocked decision from the independent AgentSafe controller, persists and verifies the resulting signed journal events, and runs the deterministic policy checks against the same company database.
