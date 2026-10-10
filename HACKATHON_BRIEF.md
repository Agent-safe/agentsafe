# Hackathon brief and implementation instructions

## Source material

- Track 2: [AI for Security — Secure AI Defenders Augmented](https://app.notion.com/p/Track-2-AI-for-Security-Secure-AI-Defenders-Augmented-3f400920c77e81d7a718cbf19f4bbf80)
- Candidate guide: [Track Guidance EN](https://app.notion.com/p/Candidate-Guide-Track-Guidance-EN-3f400920c77e81beb3b4fad01fc5eeda)
- Product implementation: [`PRODUCT_SPEC.md`](PRODUCT_SPEC.md), [`PROJECT_GUIDE.md`](PROJECT_GUIDE.md)

Use the official track and candidate guide as the competition requirements. Preserve their constraints when changing scope or preparing the submission. The linked Notion pages could not be fetched by the available web reader in this session; this file records the references and the product direction supplied by the team, not a transcription of the private pages.

## Product direction

AgentSafe is a control and audit layer for existing AI agents. It governs permissions, delegation, tool access, and data movement. A controller checks each protected action against a signed, mission-scoped mandate, records an allow/deny decision, executes only allowed local connectors, and produces a signed receipt. The SOC console exposes agent grants, group missions, JSONL audit events, alerts, and revocation controls.

The threat story is concrete: a legitimately provisioned agent can still send confidential content to an unapproved destination, exceed a transaction or mission budget, delegate excessive permissions, replay a request, or act after revocation. AgentSafe should show who initiated an action, which mandate authorized it, the rule that decided it, and how the operator stops future actions.

## Demo requirements

- Use LangGraph for the explicit multi-agent demo graph. Show the agent proposal, controller policy gate, and local connector as separate steps.
- Keep scenarios deterministic and label synthetic inputs/results as synthetic. A simulated call must never be described as a real external tool action.
- Demonstrate both legitimate and malicious/unsafe paths: allowed internal work; denied external confidential transfer; exceeded scope or budget; missing exact approval; replay or invalid signature; and revocation/kill switch.
- Verify Groth16 delegation proofs independently. State that the current one-party setup is development-only; do not imply production trusted-setup security.
- Persist hosted state in Supabase PostgreSQL. The browser dashboard should read actual API state, not hard-coded demo counters or fictional tool-call history.
- Keep private payloads and credentials out of the audit journal. Export JSONL that can be ingested by standard log shippers, and show reasons for denials and alerts.
- The controller anchors salted journal commitments to the deployed Avalanche Fuji contract. Report an anchor as confirmed only after the API validates the Fuji receipt and commitment event; verify the transaction on the explorer.
- Avoid adding a workshop technology merely because it appeared in a warm-up. Use the chain and wallet workflow only when it serves the product and is actually integrated.

## Honest implementation boundary

The current hosted build is an interactive controller prototype with durable PostgreSQL state, Ed25519 mandates/receipts, policy enforcement, LangGraph synthetic scenarios, local-only connectors, a hash-chained signed journal, alerting, and Groth16 delegation proofs. It is not a production multi-tenant gateway, does not protect third-party tools, and does not have authenticated enterprise approvers.
