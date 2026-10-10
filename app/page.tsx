"use client";

import { useMemo, useState, useSyncExternalStore } from "react";
import {
  BrowserProvider,
  Contract,
  hexlify,
  keccak256,
  randomBytes,
  toUtf8Bytes,
} from "ethers";

type Decision = "allowed" | "review" | "blocked";
type ToolEvent = {
  time: string;
  tool: string;
  detail: string;
  decision: Decision;
  tag: string;
};
type DraftArtifact = { kind: "draft"; title: string; body: string };
type AuditRecord = {
  runId: string;
  root: string;
  salt: string;
  events: ToolEvent[];
  artifacts: DraftArtifact[];
  createdAt: string;
  engine: "mistral" | "replay";
  txHash?: string;
};

declare global {
  interface Window {
    ethereum?: {
      request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
    };
  }
}

const STORAGE_KEY = "agentsafe.audit.v1";
const CONTRACT_ADDRESS = process.env.NEXT_PUBLIC_AUDIT_ANCHOR_ADDRESS;
const FUJI_CHAIN_ID = "0xa869";
const FUJI_RPC = "https://api.avax-test.network/ext/bc/C/rpc";
const ABI = [
  "function anchorRoot(bytes32 root) external",
  "function anchoredAt(bytes32 root) external view returns (uint64)",
  "event RootAnchored(bytes32 indexed root, address indexed submittedBy, uint64 anchoredAt)",
];

const auditSubscribers = new Set<() => void>();

function getAuditSnapshot() {
  return window.localStorage.getItem(STORAGE_KEY);
}

function subscribeToAudit(listener: () => void) {
  auditSubscribers.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    auditSubscribers.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function saveAuditRecord(record: AuditRecord) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(record));
  auditSubscribers.forEach((listener) => listener());
}

function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  const paths: Record<string, React.ReactNode> = {
    shield: <><path d="M12 3 20 6v5c0 5-3.4 8.2-8 10-4.6-1.8-8-5-8-10V6l8-3Z"/><path d="m9 12 2 2 4-4"/></>,
    grid: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
    activity: <><path d="M3 12h4l3-8 4 16 3-8h4"/></>,
    list: <><path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 6h.01M4 12h.01M4 18h.01"/></>,
    settings: <><circle cx="12" cy="12" r="3"/><path d="m19.4 15 .1.1 1.4 1.1-1.4 2.4-1.7-.6a8 8 0 0 1-1.8 1l-.3 1.8h-2.8l-.3-1.8a8 8 0 0 1-1.8-1l-1.7.6-1.4-2.4 1.4-1.1a8 8 0 0 1 0-2l-1.4-1.1 1.4-2.4 1.7.6a8 8 0 0 1 1.8-1l.3-1.8h2.8l.3 1.8a8 8 0 0 1 1.8 1l1.7-.6 1.4 2.4-1.4 1.1a8 8 0 0 1 0 2Z" transform="translate(-1 -1) scale(1.08)"/></>,
    search: <><circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.5 4.5"/></>,
    arrow: <><path d="M5 12h14M13 6l6 6-6 6"/></>,
    external: <><path d="M14 4h6v6M20 4l-9 9"/><path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    lock: <><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
    dots: <><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></>,
    play: <path d="m8 5 12 7-12 7V5Z"/>,
    wallet: <><rect x="3" y="5" width="18" height="15" rx="2"/><path d="M3 9h18M16 14h2"/></>,
    chain: <><path d="m10 13.5 4-4"/><path d="M7.5 15.5 5 18a3.5 3.5 0 0 1-5-5l4-4a3.5 3.5 0 0 1 5 0" transform="translate(3 -1)"/><path d="m16.5 8.5 2.5-2.5a3.5 3.5 0 0 1 5 5l-4 4a3.5 3.5 0 0 1-5 0" transform="translate(-3 1)"/></>,
  };
  return <svg {...common} aria-hidden="true">{paths[name] ?? paths.grid}</svg>;
}

function createAuditRecord(events: ToolEvent[], engine: AuditRecord["engine"] = "replay", artifacts: DraftArtifact[] = []): AuditRecord {
  const salt = hexlify(randomBytes(32));
  const root = keccak256(toUtf8Bytes(`${salt}:${JSON.stringify({ events, artifacts })}`));
  return { runId: `run_${hexlify(randomBytes(4)).slice(2)}`, root, salt, events, artifacts, engine, createdAt: new Date().toISOString() };
}

export default function Home() {
  const savedAudit = useSyncExternalStore(subscribeToAudit, getAuditSnapshot, () => null);
  const record = useMemo(() => {
    if (!savedAudit) return null;
    try { return JSON.parse(savedAudit) as AuditRecord; } catch { return null; }
  }, [savedAudit]);
  const events = useMemo(() => record?.events ?? [], [record]);
  const [wallet, setWallet] = useState("");
  const [isAnchoring, setIsAnchoring] = useState(false);
  const [isSimulating, setIsSimulating] = useState(false);
  const [notice, setNotice] = useState("");
  const [activeNav, setActiveNav] = useState("Overview");

  const [liveEvents, setLiveEvents] = useState<ToolEvent[]>([]);
  const [activeNode, setActiveNode] = useState("");
  const displayEvents = isSimulating ? liveEvents : events;
  const displayRecord = isSimulating ? null : record;
  const blockedCount = useMemo(() => displayEvents.filter((event) => event.decision === "blocked").length, [displayEvents]);

  async function runScenario(nextScenario: "attack" | "safe") {
    setIsSimulating(true);
    setLiveEvents([]);
    setActiveNode("read_context");
    setNotice("LangGraph started · reading synthetic context…");
    try {
      const response = await fetch("/api/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario: nextScenario }),
      });
      if (!response.ok || !response.body) throw new Error("simulation-failed");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let completed: { events: ToolEvent[]; artifacts: DraftArtifact[]; engine: "mistral" | "replay" } | null = null;
      const consume = (line: string) => {
        if (!line.trim()) return;
        const update = JSON.parse(line) as { type: "step" | "complete" | "error"; node?: string; nextNode?: string | null; events?: ToolEvent[]; artifacts?: DraftArtifact[]; engine?: "mistral" | "replay"; message?: string };
        if (update.type === "error") throw new Error(update.message ?? "simulation-failed");
        if (update.type === "step") {
          setActiveNode(update.nextNode ?? "");
          setLiveEvents((current) => current.concat(update.events ?? []));
          setNotice(`LangGraph running · ${nodeLabel(update.nextNode ?? update.node ?? "")}`);
        }
        if (update.type === "complete" && Array.isArray(update.events) && (update.engine === "mistral" || update.engine === "replay")) {
          completed = { events: update.events, artifacts: update.artifacts ?? [], engine: update.engine };
        }
      };
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        lines.forEach(consume);
        if (done) break;
      }
      if (buffer.trim()) consume(buffer);
      const result = completed as { events: ToolEvent[]; artifacts: DraftArtifact[]; engine: "mistral" | "replay" } | null;
      if (!result) throw new Error("invalid-simulation");
      const nextRecord = createAuditRecord(result.events, result.engine, result.artifacts);
      saveAuditRecord(nextRecord);
      setNotice(`${nextScenario === "attack" ? "Attack" : "Safe"} LangGraph run completed via ${result.engine === "mistral" ? "Mistral AI" : "fixed replay"}. Local evidence commitment is ready.`);
    } catch {
      setNotice("The simulation service could not be reached; no run was recorded. Check your connection and retry.");
    } finally {
      setIsSimulating(false);
      setActiveNode("");
    }
  }

  function nodeLabel(node: string) {
    const labels: Record<string, string> = {
      read_context: "reading synthetic context",
      propose_action: "getting an action proposal (Mistral or local replay)",
      policy_gate: "checking the proposal against policy",
      create_local_draft: "creating a local draft",
      stop_action: "stopping the action / requesting review",
    };
    return labels[node] ?? node;
  }

  async function connectWallet() {
    setNotice("");
    if (!window.ethereum) {
      setNotice("Install or open a browser wallet, then connect a Fuji testnet account.");
      return;
    }
    try {
      await window.ethereum.request({ method: "eth_requestAccounts" });
      await window.ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId: FUJI_CHAIN_ID }] });
      const provider = new BrowserProvider(window.ethereum as never);
      const signer = await provider.getSigner();
      setWallet(await signer.getAddress());
      setNotice("Wallet connected on Avalanche Fuji testnet.");
    } catch (error) {
      const code = (error as { code?: number }).code;
      if (code === 4902 && window.ethereum) {
        try {
          await window.ethereum.request({ method: "wallet_addEthereumChain", params: [{ chainId: FUJI_CHAIN_ID, chainName: "Avalanche Fuji C-Chain", nativeCurrency: { name: "AVAX", symbol: "AVAX", decimals: 18 }, rpcUrls: [FUJI_RPC], blockExplorerUrls: ["https://build.avax.network/explorer/fuji/c-chain"] }] });
          setNotice("Fuji was added to your wallet. Connect again to continue.");
        } catch {
          setNotice("Fuji was not added. You can add it manually from Settings.");
        }
      } else {
        setNotice("Wallet connection was cancelled or the wallet is not set to Fuji.");
      }
    }
  }

  async function anchorEvidence() {
    if (!record) {
      setNotice("Run a simulation first so AgentSafe has an evidence record to anchor.");
      return;
    }
    if (!CONTRACT_ADDRESS) {
      setNotice("The Fuji contract address is not configured yet. The local evidence record is ready.");
      return;
    }
    if (!window.ethereum) {
      setNotice("Connect a browser wallet with Fuji test AVAX to anchor this proof.");
      return;
    }
    setIsAnchoring(true);
    setNotice("Check your wallet and approve the Fuji testnet transaction.");
    try {
      const provider = new BrowserProvider(window.ethereum as never);
      const network = await provider.getNetwork();
      if (Number(network.chainId) !== 43113) throw new Error("wrong-chain");
      const signer = await provider.getSigner();
      const contract = new Contract(CONTRACT_ADDRESS, ABI, signer);
      const tx = await contract.anchorRoot(record.root);
      setNotice("Transaction sent. Waiting for Fuji to confirm the evidence anchor…");
      const receipt = await tx.wait();
      const updated = { ...record, txHash: receipt.hash };
      saveAuditRecord(updated);
      setNotice("Evidence commitment confirmed on Fuji. The log contents stayed in this browser.");
    } catch (error) {
      const reason = error instanceof Error ? error.message : "";
      setNotice(reason === "wrong-chain" ? "Switch your wallet to Avalanche Fuji before anchoring." : "The transaction was cancelled or could not be confirmed. Your local record is unchanged.");
    } finally {
      setIsAnchoring(false);
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#overview" aria-label="AgentSafe home">
          <span className="brand-mark"><Icon name="shield" size={21} /></span>
          <span>agent<span className="brand-light">safe</span></span>
          <span className="brand-beta">BETA</span>
        </a>
        <div className="workspace-switcher">
          <span className="workspace-avatar"><Icon name="activity" size={16} /></span>
          <span className="workspace-copy"><b>Local demo</b><small>Synthetic data · this browser</small></span>
        </div>
        <div className="side-label">DEMO</div>
        <nav className="side-nav" aria-label="Demo sections">
          {[["Overview", "grid", "overview"], ["Current run", "activity", "agent-run"], ["Audit evidence", "list", "evidence"], ["Policy", "shield", "policies"], ["Run events", "list", "events"]].map(([label, icon, target]) => (
            <a className={`nav-item ${activeNav === label ? "active" : ""}`} key={label} href={`#${target}`} onClick={() => setActiveNav(label)}>
              <Icon name={icon} size={17} /><span>{label}</span>
            </a>
          ))}
        </nav>
        <div className="sidebar-spacer" />
        <div className="chain-card">
          <div className="chain-card-head"><span className="chain-icon">A</span><div><b>Avalanche Fuji</b><small>TEST NETWORK</small></div></div>
          <p>{CONTRACT_ADDRESS ? "Only salted evidence hashes are written on-chain." : "Contract is not deployed yet; evidence remains local."}</p>
          <a href="https://build.avax.network/explorer/fuji/c-chain" target="_blank" rel="noreferrer">Explore network <Icon name="external" size={12} /></a>
        </div>
        <div className="profile-row"><span className="profile-avatar"><Icon name="lock" size={14} /></span><span><b>{wallet ? "Fuji wallet connected" : "No wallet connected"}</b><small>{wallet ? `${wallet.slice(0, 6)}…${wallet.slice(-4)}` : "Audit data stays in this browser"}</small></span></div>
      </aside>

      <section className="main-area" id="overview">
        <header className="topbar">
          <div className="breadcrumbs"><span>AgentSafe</span><b>/</b><strong>{activeNav}</strong></div>
          <div className="top-actions"><span className="demo-chip">LANGGRAPH · SYNTHETIC AGENT</span><button className="help-button" onClick={() => setNotice("LangGraph runs context lookup → Mistral action proposal → local policy gate → local draft or stop. All scenario data is synthetic; no email or external tool is connected.")}>How it works</button></div>
        </header>

        <div className="page-content">
          <div className="page-heading">
            <div><div className="eyebrow"><span className="pulse-dot" /> SYNTHETIC DEMO <span className="eyebrow-sep">/</span> TEST EACH STEP</div><h1>Put a safety check<br className="desktop-break" /> before an AI action</h1><p>The AI proposes an action. AgentSafe checks it. Risky requests are stopped before they run.</p></div>
            <div className="heading-actions"><button className="button button-secondary" onClick={connectWallet}><Icon name="wallet" size={16} />{wallet ? `${wallet.slice(0, 6)}…${wallet.slice(-4)}` : "Connect wallet"}</button><button className="button button-primary" onClick={() => runScenario("attack")} disabled={isSimulating}><Icon name="play" size={15} />{isSimulating ? "Simulating…" : "Run attack simulation"}</button></div>
          </div>

          <section className="idea-flow" aria-label="How AgentSafe works">
            <div><span>1</span><b>AI proposes</b><small>For example: send a support reply</small></div>
            <div><span>2</span><b>Policy checks</b><small>Is this destination allowed?</small></div>
            <div><span>3</span><b>Stop or allow</b><small>Keep a private proof of the decision</small></div>
          </section>

          <div className="metrics-grid">
            <article className="metric-card"><div className="metric-top"><span>Tool calls in this run</span><span className="metric-icon blue"><Icon name="activity" size={17} /></span></div><div className="metric-value">{displayEvents.length}</div><div className="metric-foot"><span>{isSimulating ? "Live LangGraph trace" : record ? "Synthetic trace" : "No run yet"}</span><span>{isSimulating ? "updating now" : record ? "current run" : "run a scenario to begin"}</span></div></article>
            <article className="metric-card"><div className="metric-top"><span>Blocked</span><span className="metric-icon red"><Icon name="shield" size={17} /></span></div><div className="metric-value">{blockedCount}</div><div className="metric-foot"><span>Policy result</span><span>current run</span></div></article>
            <article className="metric-card"><div className="metric-top"><span>Needs review</span><span className="metric-icon amber"><Icon name="clock" size={17} /></span></div><div className="metric-value">{displayEvents.filter((event) => event.decision === "review").length}</div><div className="metric-foot"><span>Human review decisions</span><span>current run</span></div></article>
            <article className="metric-card"><div className="metric-top"><span>Evidence</span><span className="metric-icon green"><Icon name="lock" size={16} /></span></div><div className="metric-value">{isSimulating ? "Running" : displayRecord?.txHash ? "On-chain" : displayRecord ? "Local" : "None"}</div><div className="metric-foot"><span>{isSimulating ? "Not committed yet" : displayRecord?.txHash ? "Confirmed on Fuji" : "Not on-chain"}</span><span>salted hash</span></div></article>
          </div>

          <div className="content-grid">
            <section className="panel run-panel" id="agent-run">
              <div className="panel-header"><div><div className="section-kicker">LANGGRAPH · SYNTHETIC SCENARIO</div><h2>Current run</h2><small className="section-kicker">{record ? record.engine === "mistral" ? "LangGraph · Mistral proposal · local policy gate" : "LangGraph · fixed replay · local policy gate" : "One demo agent · no external tools connected"}</small></div></div>
              {isSimulating ? <div className="run-meta"><div className="agent-avatar running-avatar"><Icon name="activity" size={17} /></div><div className="agent-details"><b>LangGraph is running</b><small>Active node: <code>{nodeLabel(activeNode)}</code></small></div><span className="contained-chip running-chip"><span />Running</span></div> : record ? <div className="run-meta"><div className="agent-avatar"><Icon name="activity" size={17} /></div><div className="agent-details"><b>Synthetic support flow</b><small>Run ID <code>{record.runId ?? "saved-local-run"}</code><span className="meta-divider">·</span>{new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(new Date(record.createdAt))}</small></div><span className="contained-chip"><span />{blockedCount ? "Blocked by policy" : "Completed"}</span></div> : <div className="empty-state"><b>No run yet</b><span>Choose a synthetic scenario above. It uses no real agent, account or customer data.</span></div>}
              <div className="timeline-heading"><span>LANGGRAPH NODE TRACE</span><span>{displayEvents.length} events{isSimulating ? " · live" : ""}</span></div>
              <div className="timeline">
                {displayEvents.length === 0 ? <div className="timeline-empty">{isSimulating ? `Running node: ${nodeLabel(activeNode)}…` : "Run an attack or safe scenario to create a trace."}</div> : displayEvents.map((event, index) => <div className={`timeline-row ${event.decision}`} key={`${event.time}-${index}`}>
                  <div className="timeline-rail"><span className="timeline-marker">{event.decision === "allowed" ? <Icon name="check" size={12} /> : event.decision === "blocked" ? "!" : <Icon name="clock" size={12} />}</span>{index < events.length - 1 && <i />}</div>
                  <div className="timeline-event"><div className="event-top"><b>{event.tool}</b><time>{event.time}</time></div><p>{event.detail}</p></div>
                  <span className={`event-tag ${event.decision}`}>{event.tag}</span>
                </div>)}
              </div>
              {!isSimulating && record?.artifacts?.map((artifact, index) => <div className="draft-artifact" key={`${artifact.kind}-${index}`}><div className="draft-label">LOCAL DRAFT · NOT SENT</div><b>{artifact.title}</b><p>{artifact.body}</p></div>)}
              <div className="run-footer"><span><span className="mini-lock"><Icon name="lock" size={12} /></span> Raw events stay in this browser</span>{record && <a href="#events" onClick={() => setActiveNav("Run events")}>View run events <Icon name="arrow" size={14} /></a>}</div>
            </section>

            <aside className="right-column">
              <section className="panel evidence-panel" id="evidence">
                <div className="panel-header"><div><div className="section-kicker">AUDIT EVIDENCE</div><h2>Proof of integrity</h2></div><span className="evidence-emblem"><Icon name="lock" size={17} /></span></div>
                <p className="evidence-copy">Anchor a salted fingerprint of this run. Event details never leave your device.</p>
                <div className="proof-box"><div className="proof-label"><span>RUN COMMITMENT</span><span className="proof-ready"><i />{isSimulating ? "IN PROGRESS" : displayRecord?.txHash ? "CONFIRMED" : displayRecord ? "READY" : "NOT CREATED"}</span></div><code>{isSimulating ? `Waiting for ${nodeLabel(activeNode)} to finish…` : displayRecord?.root ?? "Run a simulation to create a proof"}</code><button className="copy-button" onClick={async () => { if (displayRecord) { await navigator.clipboard.writeText(displayRecord.root); setNotice("Commitment copied."); } }} aria-label="Copy commitment" disabled={!displayRecord}>⧉</button></div>
                {displayRecord?.txHash ? <a className="tx-link" href={`https://build.avax.network/explorer/fuji/c-chain/tx/${displayRecord.txHash}`} target="_blank" rel="noreferrer"><span className="tx-check"><Icon name="check" size={12} /></span>View confirmed transaction <Icon name="external" size={12} /></a> : <div className="network-note"><span className="network-symbol">A</span><span><b>Avalanche Fuji</b><small>Testnet · Chain ID 43113</small></span><span className="network-online"><i />TESTNET</span></div>}
                <button className="button button-anchor" onClick={anchorEvidence} disabled={isSimulating || isAnchoring || Boolean(displayRecord?.txHash) || !CONTRACT_ADDRESS}><Icon name={isAnchoring ? "clock" : "chain"} size={16} />{isSimulating ? "Waiting for run to finish…" : isAnchoring ? "Waiting for confirmation…" : displayRecord?.txHash ? "Evidence anchored" : CONTRACT_ADDRESS ? "Anchor proof to Fuji" : "Fuji contract not configured"}</button>
                {!CONTRACT_ADDRESS && <p className="config-hint">The evidence hash is local only until the Fuji contract is deployed and configured.</p>}
              </section>

              <section className="panel policy-panel" id="policies">
                <div className="panel-header"><div><div className="section-kicker">LOCAL POLICY GATE</div><h2>Rules used in this demo</h2></div></div>
                <div className="policy-row"><span className="policy-symbol purple"><Icon name="shield" size={15} /></span><div><b>Outbound email recipient</b><small>Block destinations outside @example.org; route allowed-domain calls to review.</small></div><span className="policy-state">ENFORCED</span></div>
                <div className="policy-row"><span className="policy-symbol orange"><Icon name="lock" size={15} /></span><div><b>No external action execution</b><small>Mistral tool calls are proposals; the demo never sends email.</small></div><span className="policy-state">ENFORCED</span></div>
              </section>
            </aside>
          </div>

          <section className="panel activity-panel" id="events">
            <div className="panel-header"><div><div className="section-kicker">LOCAL TRACE</div><h2>Events in this run</h2></div><button className="button button-secondary small-button" onClick={() => runScenario("safe")} disabled={isSimulating}>Run safe scenario <Icon name="play" size={13} /></button></div>
            <div className="activity-table-wrap"><table className="activity-table"><thead><tr><th>EVENT</th><th>AGENT</th><th>RISK</th><th>DECISION</th><th>TIME</th><th /></tr></thead><tbody>
              {events.map((event, index) => <tr key={`${event.time}-${event.tool}-${index}`}><td><span className={`table-icon ${event.decision === "blocked" ? "danger" : event.decision === "review" ? "warning" : "success"}`}>{event.decision === "blocked" ? "!" : event.decision === "review" ? "⌁" : "✓"}</span><div><b>{event.detail}</b><small>{event.tool}</small></div></td><td>Synthetic flow</td><td><span className={event.decision === "blocked" ? "risk-high" : event.decision === "review" ? "risk-medium" : "risk-low"}>{event.decision === "blocked" ? "High" : event.decision === "review" ? "Review" : "Low"}</span></td><td><span className={event.decision === "blocked" ? "decision-blocked" : event.decision === "review" ? "decision-review" : "decision-allowed"}><i />{event.decision}</span></td><td>{event.time}</td><td /></tr>)}
            </tbody></table></div>
            <div className="table-footer"><span>{events.length ? "Synthetic data · stored in this browser" : "No events yet. Run a scenario to create a local trace."}</span>{record && <a href="#evidence" onClick={() => setActiveNav("Audit evidence")}>Open audit evidence <Icon name="arrow" size={13} /></a>}</div>
          </section>

          <footer className="page-footer"><span>AGENTSAFE <span>·</span> DEFENDERS IN CONTROL</span><span>Demo data only <i /> Fuji testnet</span></footer>
        </div>
      </section>

      {notice && <div className="toast" role="status"><span className="toast-icon"><Icon name="check" size={14} /></span><span>{notice}</span><button aria-label="Dismiss" onClick={() => setNotice("")}>×</button></div>}
    </main>
  );
}
