# AgentSafe: what it does and how it works

## The idea in one minute

AI agents can call tools: read a support ticket, search a help center, send an email, or change an account. If an agent follows a malicious instruction hidden in data, it may try to use a tool in a way its operator did not intend.

AgentSafe is a defender-side checkpoint. The agent proposes a tool action, a policy checks it before execution, and the decision is recorded for review. A salted hash of that record can be anchored to Avalanche Fuji so someone can later check that the evidence has not changed.

Think of it as a security checkpoint in front of an agent's tool use. The blockchain is the tamper-evident receipt; it does not make the policy decision or prove the AI was right.

## What happens when you run a scenario

The **Run attack simulation** and **Run safe scenario** buttons exercise the same LangGraph workflow with different synthetic cases:

1. **Read context:** LangGraph reads a ticket fixture and searches a local knowledge fixture.
2. **Propose an action:** the proposal node asks Mistral for one structured tool-call proposal, if `MISTRAL_API_KEY` is configured. The attack fixture asks it to propose sending to an unapproved address; the safe fixture asks it to prepare a reply draft.
3. **Check policy:** the server checks the proposed tool and recipient. An address outside `@example.org` is blocked. An address in that domain still goes to analyst review. Only the safe scenario's local draft action can continue.
4. **Stop or create a draft:** a blocked action stops before any sender is called. The safe case creates a draft object in the run; it does not send an email.
5. **Show the run:** the API streams completed graph-node updates to the dashboard. While a run is in progress, the Current run panel shows the active node and the trace as it arrives. The evidence panel says **IN PROGRESS** until the graph completes.
6. **Prepare evidence:** after completion, the browser creates a random salt and hashes the run's events and draft. The raw trace, draft, salt, and hash are stored in that browser's local storage.
7. **Optionally anchor the hash:** connect MetaMask on Fuji and confirm the transaction. The contract stores only the `bytes32` hash and its timestamp. The UI shows a transaction link after confirmation. Until that happens, evidence is **local**, not on-chain.

## What is real in this prototype

- **LangGraph is actually used.** `app/api/simulate/agent.ts` defines and compiles a `StateGraph`. Its nodes are `read_context`, `propose_action`, `policy_gate`, `create_local_draft`, and `stop_action`; a conditional edge chooses the draft or stop path.
- **Mistral can actually generate the tool proposal.** The server calls Mistral's chat completions API when a server-side `MISTRAL_API_KEY` is available. The key is never sent to the browser. The UI labels a completed run as Mistral or fixed replay.
- **The policy check is executable code.** It runs on the server between proposal and action. No real email sender is present in this demo, and a blocked proposal cannot send email.
- **The Fuji contract is real.** When a user connects a wallet and confirms, the browser sends a zero-value contract call on Fuji. The contract stores a hash and timestamp. Fuji is a test network; its AVAX has no real monetary value.
- **The dashboard is interactive.** Runs stream node updates, show decisions, and store completed evidence in the current browser.

## What is still synthetic or limited

- The ticket and knowledge article are hard-coded examples in `agent.ts`; there is no Gmail, help-desk, database, or customer-data connection.
- The attack is a controlled demonstration. It does not test all prompt injections or establish that the model is generally safe.
- If Mistral is missing or unavailable, the graph uses a deterministic replay. That replay makes the flow demonstrable, but it is not an AI-generated decision.
- This is **one support-agent workflow**, not a team of agents. LangGraph orchestrates its steps; it does not by itself connect AgentSafe to arbitrary agents.
- The local policy gate protects actions only inside this workflow. It is not yet a general interception layer for agents running elsewhere.
- Browser local storage is demo storage. It is not shared, backed up, access-controlled, or suitable as a production evidence archive.
- Anchoring the hash proves that the same hash was submitted by a wallet at a time. It does not prove the underlying trace is complete, true, or safe. The trace and salt are not placed on-chain.

## Where to see a run

On the dashboard, click **Run attack simulation** or **Run safe scenario**. Watch **Current run → LangGraph node trace** while it runs. Each row is a completed workflow event; the run header names the node currently executing. When complete, inspect the decision and (for the safe path) the **LOCAL DRAFT · NOT SENT** card. The evidence panel will say **READY** for a locally saved hash or **CONFIRMED** only after a Fuji transaction is confirmed.

In this workspace, open `http://localhost:3001` for the verified model-backed preview. The `localhost:3000` development server may use a different local environment. The deployed Vercel URL does not show these latest changes until they are published.

## What a real integration would require

For AgentSafe to protect an actual operational agent, the agent's real tool calls must pass through the policy gate before a tool executes. That means integrating at least one real agent runtime and its tools, defining production policies for those tools, and storing audit records in an access-controlled service. Merely logging a tool call after it happened would not stop it. This prototype demonstrates the checkpoint flow, but it is not yet connected to an operating support system.

## Main implementation files

- `app/api/simulate/agent.ts` — LangGraph state, nodes, policy decision, and streamed run updates.
- `app/api/simulate/route.ts` — validates the scenario request and streams graph updates to the browser.
- `app/page.tsx` — run controls, live trace, local evidence, and wallet interaction.
- `contracts/src/AuditAnchor.sol` — Fuji contract that stores a commitment root and timestamp.
- `README.md` — setup and deployment details.
