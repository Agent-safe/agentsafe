# AgentSafe

AgentSafe is a hackathon proof of concept for supervising AI-agent tool calls. It shows a synthetic safe run and a synthetic exfiltration attempt, demonstrates allow/block/review decisions, and commits a salted fingerprint of the evidence to Avalanche Fuji.

## What the demo proves

- A tool call to an unapproved destination can be blocked and escalated for analyst review.
- The browser can create a salted commitment over an event record and verify its timestamp on Fuji.
- Raw event details stay in the browser. The contract accepts only a `bytes32` root; it does not receive prompts, arguments, user data, or the salt.

This demo uses synthetic events, a Mistral-proposed tool call, a deterministic local policy gate, and local browser storage. Mistral only proposes a synthetic tool call; AgentSafe evaluates it and never executes the proposal. If the Mistral key is missing or the service is unavailable, a fixed replay runs instead. It is not a production agent gateway, does not intercept arbitrary frameworks, and does not claim to detect all prompt injection or agent abuse.

## Run the dashboard

```bash
npm install
npm run dev
```

Open the local URL shown by Next.js. Use **Run attack simulation** or **Run safe simulation** to create a local audit record. The interface remains useful without a deployed contract, but anchoring stays disabled until `NEXT_PUBLIC_AUDIT_ANCHOR_ADDRESS` points to a Fuji deployment.

To enable Mistral locally, put `MISTRAL_API_KEY` in `.env.local`. It is read only by `/api/simulate` on the server and must never use a `NEXT_PUBLIC_` prefix. Vercel production and preview deployments use the project-scoped sensitive environment variable.

## Avalanche Fuji

The contract is deliberately Fuji-only (chain ID `43113`). Build with Foundry using the configuration in `contracts/foundry.toml`:

```bash
cd contracts
forge build
forge create --rpc-url https://api.avax-test.network/ext/bc/C/rpc \
  --private-key "$FUJI_DEPLOYER_PRIVATE_KEY" \
  src/AuditAnchor.sol:AuditAnchor
```

Keep `FUJI_DEPLOYER_PRIVATE_KEY` in a local shell or secret manager. Never commit it or enter a seed phrase into the app. The returned contract address can be configured as `NEXT_PUBLIC_AUDIT_ANCHOR_ADDRESS` in local `.env.local` and in the AgentSafe Vercel project. The browser wallet used for anchoring must be on Fuji and hold test AVAX. A local proof does not become a chain proof until its transaction is confirmed and a transaction link is visible in the UI.

Network details: Fuji C-Chain RPC `https://api.avax-test.network/ext/bc/C/rpc`, chain ID `43113`, explorer [Avalanche Fuji C-Chain](https://build.avax.network/explorer/fuji/c-chain).

## Evidence model

The app generates a random 32-byte salt and computes `keccak256(UTF8(salt + ':' + JSON.stringify(events)))`. The salt and events are stored in browser `localStorage`; only the resulting root is submitted to `AuditAnchor`. Keep the salt with the local evidence record: without it, a third party cannot independently reproduce the commitment. Treat browser storage as demo storage, not durable or access-controlled evidence retention.

## Track framing

The demo is a defender-facing observability and response concept aligned with Detect/Respond: make a risky tool call visible, block or pause it, and preserve a tamper-evident reference for later review. The on-chain record proves that a particular hash was submitted at a particular time; it does **not** prove the source log was truthful, complete, or safe, and it does not provide confidentiality for data that is mistakenly published.
