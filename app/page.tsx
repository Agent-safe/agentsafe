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
type AuditRecord = {
  root: string;
  salt: string;
  events: ToolEvent[];
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

const initialEvents: ToolEvent[] = [
  { time: "16:42:08.103", tool: "ticket.lookup", detail: "Read synthetic ticket #1842", decision: "allowed", tag: "Read" },
  { time: "16:42:08.421", tool: "kb.search", detail: "Search internal help articles", decision: "allowed", tag: "Read" },
  { time: "16:42:09.017", tool: "mail.send", detail: "Send ticket context to an unapproved domain", decision: "blocked", tag: "Exfiltration" },
  { time: "16:42:09.022", tool: "agent.pause", detail: "Run paused; analyst review required", decision: "review", tag: "Human review" },
];

const safeEvents: ToolEvent[] = [
  { time: "16:45:12.054", tool: "ticket.lookup", detail: "Read synthetic ticket #1842", decision: "allowed", tag: "Read" },
  { time: "16:45:12.287", tool: "kb.search", detail: "Search internal help articles", decision: "allowed", tag: "Read" },
  { time: "16:45:12.901", tool: "draft.create", detail: "Prepare a reply for analyst review", decision: "allowed", tag: "Draft" },
  { time: "16:45:13.116", tool: "agent.complete", detail: "Run ended without external delivery", decision: "allowed", tag: "Complete" },
];

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

function createAuditRecord(events: ToolEvent[], engine: AuditRecord["engine"] = "replay"): AuditRecord {
  const salt = hexlify(randomBytes(32));
  const root = keccak256(toUtf8Bytes(`${salt}:${JSON.stringify(events)}`));
  return { root, salt, events, engine, createdAt: new Date().toISOString() };
}

export default function Home() {
  const savedAudit = useSyncExternalStore(subscribeToAudit, getAuditSnapshot, () => null);
  const record = useMemo(() => {
    if (!savedAudit) return null;
    try { return JSON.parse(savedAudit) as AuditRecord; } catch { return null; }
  }, [savedAudit]);
  const events = record?.events ?? initialEvents;
  const [wallet, setWallet] = useState("");
  const [isAnchoring, setIsAnchoring] = useState(false);
  const [isSimulating, setIsSimulating] = useState(false);
  const [notice, setNotice] = useState("");
  const [activeNav, setActiveNav] = useState("Overview");

  const blockedCount = useMemo(() => events.filter((event) => event.decision === "blocked").length, [events]);

  async function runScenario(nextScenario: "attack" | "safe") {
    setIsSimulating(true);
    setNotice("Running the synthetic scenario through the policy gate…");
    try {
      const response = await fetch("/api/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario: nextScenario }),
      });
      if (!response.ok) throw new Error("simulation-failed");
      const result = await response.json() as { events?: ToolEvent[]; engine?: "mistral" | "replay" };
      if (!Array.isArray(result.events) || (result.engine !== "mistral" && result.engine !== "replay")) throw new Error("invalid-simulation");
      const nextRecord = createAuditRecord(result.events, result.engine);
      saveAuditRecord(nextRecord);
      setNotice(`${nextScenario === "attack" ? "Attack" : "Safe"} simulation completed via ${result.engine === "mistral" ? "Mistral AI" : "fixed replay"}. Local evidence commitment is ready.`);
    } catch {
      const nextRecord = createAuditRecord(nextScenario === "attack" ? initialEvents : safeEvents, "replay");
      saveAuditRecord(nextRecord);
      setNotice("The simulation API was unavailable; a fixed synthetic replay was used. Local evidence commitment is ready.");
    } finally {
      setIsSimulating(false);
    }
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
          <span className="workspace-avatar">A</span>
          <span className="workspace-copy"><b>Acme Security</b><small>Workspace</small></span>
          <span className="switch-chevron">⌄</span>
        </div>
        <div className="side-label">WORKSPACE</div>
        <nav className="side-nav" aria-label="Main navigation">
          {[["Overview", "grid"], ["Agent runs", "activity"], ["Audit evidence", "list"], ["Policies", "shield"]].map(([label, icon]) => (
            <button className={`nav-item ${activeNav === label ? "active" : ""}`} key={label} onClick={() => setActiveNav(label)}>
              <Icon name={icon} size={17} /><span>{label}</span>{label === "Agent runs" && <span className="nav-count">04</span>}
            </button>
          ))}
        </nav>
        <div className="side-label side-label-spaced">MONITORING</div>
        <nav className="side-nav" aria-label="Monitoring navigation">
          <button className="nav-item" onClick={() => setActiveNav("Tool calls")}><Icon name="list" size={17} /><span>Tool calls</span><span className="live-dot" /></button>
          <button className="nav-item" onClick={() => setActiveNav("Integrations")}><Icon name="chain" size={17} /><span>Integrations</span></button>
        </nav>
        <div className="sidebar-spacer" />
        <div className="chain-card">
          <div className="chain-card-head"><span className="chain-icon">A</span><div><b>Avalanche Fuji</b><small>TEST NETWORK</small></div><span className="status-dot" /></div>
          <p>Only salted evidence hashes are written on-chain.</p>
          <a href="https://build.avax.network/explorer/fuji/c-chain" target="_blank" rel="noreferrer">Explore network <Icon name="external" size={12} /></a>
        </div>
        <button className="profile-row" onClick={() => setNotice("Demo workspace. No user accounts or real customer data are connected.")}>
          <span className="profile-avatar">AM</span><span><b>Alex Morgan</b><small>Security analyst</small></span><Icon name="dots" size={18} />
        </button>
      </aside>

      <section className="main-area" id="overview">
        <header className="topbar">
          <div className="breadcrumbs"><span>Workspace</span><b>/</b><strong>{activeNav}</strong></div>
          <div className="top-actions"><div className="search-box"><Icon name="search" size={16} /><span>Search anything...</span><kbd>⌘ K</kbd></div><button className="icon-button" aria-label="Notifications" onClick={() => setNotice("You’re all caught up.")}>♧<i /></button><button className="help-button" onClick={() => setNotice("AgentSafe demo: simulate an unsafe run, inspect the block, then anchor its evidence hash to Fuji.")}>Help center</button></div>
        </header>

        <div className="page-content">
          <div className="page-heading">
            <div><div className="eyebrow"><span className="pulse-dot" /> THREAT MONITORING <span className="eyebrow-sep">/</span> SYNTHETIC DEMO</div><h1>Security overview</h1><p>Keep AI agents accountable, one tool call at a time.</p></div>
            <div className="heading-actions"><button className="button button-secondary" onClick={connectWallet}><Icon name="wallet" size={16} />{wallet ? `${wallet.slice(0, 6)}…${wallet.slice(-4)}` : "Connect wallet"}</button><button className="button button-primary" onClick={() => runScenario("attack")} disabled={isSimulating}><Icon name="play" size={15} />{isSimulating ? "Simulating…" : "Run attack simulation"}</button></div>
          </div>

          <div className="metrics-grid">
            <article className="metric-card"><div className="metric-top"><span>Tool calls in run</span><span className="metric-icon blue"><Icon name="activity" size={17} /></span></div><div className="metric-value">{events.length}</div><div className="metric-foot"><span className="status-label">Synthetic run</span><span>current trace</span><div className="sparkline blue-line"><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /></div></div></article>
            <article className="metric-card"><div className="metric-top"><span>Blocked in run</span><span className="metric-icon red"><Icon name="shield" size={17} /></span></div><div className="metric-value">{blockedCount}</div><div className="metric-foot"><span className="status-label red-label"><span />Policy decision</span><span>current trace</span><div className="sparkline red-line"><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /></div></div></article>
            <article className="metric-card"><div className="metric-top"><span>Pending review</span><span className="metric-icon amber"><Icon name="clock" size={17} /></span></div><div className="metric-value">{events.filter((event) => event.decision === "review").length}</div><div className="metric-foot"><span className="status-label amber-label"><span />Review decisions</span><span>current trace</span><div className="sparkline amber-line"><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /></div></div></article>
            <article className="metric-card"><div className="metric-top"><span>Evidence status</span><span className="metric-icon green"><Icon name="lock" size={16} /></span></div><div className="metric-value">{record?.txHash ? "Anchored" : record ? "Local" : "None"}</div><div className="metric-foot"><span className="status-label green-label"><span />{record?.txHash ? "Confirmed on Fuji" : "Not on-chain yet"}</span><span>hash commitment</span><div className="sparkline green-line"><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /></div></div></article>
          </div>

          <div className="content-grid">
            <section className="panel run-panel">
              <div className="panel-header"><div><div className="section-kicker">SYNTHETIC AGENT SESSION <span className="live-pill"><i />DEMO</span></div><h2>Latest monitored run</h2><small className="section-kicker">Engine: {record?.engine === "mistral" ? "Mistral AI proposal + local policy gate" : "Fixed local replay"}</small></div><button className="more-button" aria-label="More run options" onClick={() => setNotice("Showing the most recent synthetic demo run.")}><Icon name="dots" /></button></div>
              <div className="run-meta"><div className="agent-avatar">✳</div><div className="agent-details"><b>support-assistant <span className="version">v2.4.1</span></b><small>Run ID <code>run_8f2a91c</code><span className="meta-divider">·</span>Started just now</small></div><span className="contained-chip"><span />{blockedCount ? "Threat contained" : "Run completed"}</span></div>
              <div className="timeline-heading"><span>TOOL CALL TIMELINE</span><span>{events.length} events</span></div>
              <div className="timeline">
                {events.map((event, index) => <div className={`timeline-row ${event.decision}`} key={`${event.time}-${index}`}>
                  <div className="timeline-rail"><span className="timeline-marker">{event.decision === "allowed" ? <Icon name="check" size={12} /> : event.decision === "blocked" ? "!" : <Icon name="clock" size={12} />}</span>{index < events.length - 1 && <i />}</div>
                  <div className="timeline-event"><div className="event-top"><b>{event.tool}</b><time>{event.time}</time></div><p>{event.detail}</p></div>
                  <span className={`event-tag ${event.decision}`}>{event.tag}</span>
                </div>)}
              </div>
              <div className="run-footer"><span><span className="mini-lock"><Icon name="lock" size={12} /></span> Raw events stay in this browser</span><button onClick={() => setActiveNav("Agent runs")}>View run details <Icon name="arrow" size={14} /></button></div>
            </section>

            <aside className="right-column">
              <section className="panel evidence-panel">
                <div className="panel-header"><div><div className="section-kicker">AUDIT EVIDENCE</div><h2>Proof of integrity</h2></div><span className="evidence-emblem"><Icon name="lock" size={17} /></span></div>
                <p className="evidence-copy">Anchor a salted fingerprint of this run. Event details never leave your device.</p>
                <div className="proof-box"><div className="proof-label"><span>RUN COMMITMENT</span><span className="proof-ready"><i />{record?.txHash ? "CONFIRMED" : record ? "READY" : "NOT CREATED"}</span></div><code>{record?.root ?? "Run a simulation to create a proof"}</code><button className="copy-button" onClick={async () => { if (record) { await navigator.clipboard.writeText(record.root); setNotice("Commitment copied."); } }} aria-label="Copy commitment" disabled={!record}>⧉</button></div>
                {record?.txHash ? <a className="tx-link" href={`https://build.avax.network/explorer/fuji/c-chain/tx/${record.txHash}`} target="_blank" rel="noreferrer"><span className="tx-check"><Icon name="check" size={12} /></span>View confirmed transaction <Icon name="external" size={12} /></a> : <div className="network-note"><span className="network-symbol">A</span><span><b>Avalanche Fuji</b><small>Testnet · Chain ID 43113</small></span><span className="network-online"><i />TESTNET</span></div>}
                <button className="button button-anchor" onClick={anchorEvidence} disabled={isAnchoring || Boolean(record?.txHash) || !CONTRACT_ADDRESS}><Icon name={isAnchoring ? "clock" : "chain"} size={16} />{isAnchoring ? "Waiting for confirmation…" : record?.txHash ? "Evidence anchored" : CONTRACT_ADDRESS ? "Anchor proof to Fuji" : "Fuji contract not configured"}</button>
                {!CONTRACT_ADDRESS && <p className="config-hint">The evidence hash is local only until the Fuji contract is deployed and configured.</p>}
              </section>

              <section className="panel policy-panel">
                <div className="panel-header"><div><div className="section-kicker">POLICY ENGINE</div><h2>Active guardrails</h2></div><button className="view-all" onClick={() => setActiveNav("Policies")}>View all</button></div>
                <div className="policy-row"><span className="policy-symbol purple"><Icon name="shield" size={15} /></span><div><b>External data transfer</b><small>Block unapproved destinations</small></div><span className="policy-state">ACTIVE</span></div>
                <div className="policy-row"><span className="policy-symbol orange"><Icon name="lock" size={15} /></span><div><b>Sensitive context guard</b><small>Pause before sharing records</small></div><span className="policy-state">ACTIVE</span></div>
                <div className="policy-row"><span className="policy-symbol cyan"><Icon name="activity" size={15} /></span><div><b>Tool sequence anomaly</b><small>Flag unusual call patterns</small></div><span className="policy-state">ACTIVE</span></div>
              </section>
            </aside>
          </div>

          <section className="panel activity-panel">
            <div className="panel-header"><div><div className="section-kicker">RECENT ACTIVITY</div><h2>Events across your workspace</h2></div><button className="button button-secondary small-button" onClick={() => runScenario("safe")} disabled={isSimulating}>Run safe simulation <Icon name="play" size={13} /></button></div>
            <div className="activity-table-wrap"><table className="activity-table"><thead><tr><th>EVENT</th><th>AGENT</th><th>RISK</th><th>DECISION</th><th>TIME</th><th /></tr></thead><tbody>
              <tr><td><span className="table-icon danger">↗</span><div><b>Unapproved data destination</b><small>mail.send · external domain</small></div></td><td><span className="agent-mini">✳</span>support-assistant</td><td><span className="risk-high">High</span></td><td><span className="decision-blocked"><i />Blocked</span></td><td>Just now</td><td><button className="row-open" onClick={() => setActiveNav("Agent runs")} aria-label="Open event">↗</button></td></tr>
              <tr><td><span className="table-icon warning">⌁</span><div><b>Repeated lookup pattern</b><small>kb.search · 18 calls / 2s</small></div></td><td><span className="agent-mini lavender">◈</span>research-agent</td><td><span className="risk-medium">Medium</span></td><td><span className="decision-review"><i />Review</span></td><td>4 min ago</td><td><button className="row-open" onClick={() => setActiveNav("Agent runs")} aria-label="Open event">↗</button></td></tr>
              <tr><td><span className="table-icon success">✓</span><div><b>Draft prepared for review</b><small>draft.create · internal only</small></div></td><td><span className="agent-mini mint">✳</span>support-assistant</td><td><span className="risk-low">Low</span></td><td><span className="decision-allowed"><i />Allowed</span></td><td>12 min ago</td><td><button className="row-open" onClick={() => setActiveNav("Agent runs")} aria-label="Open event">↗</button></td></tr>
            </tbody></table></div>
            <div className="table-footer"><span>Showing synthetic demo activity</span><button onClick={() => setActiveNav("Audit evidence")}>Open audit trail <Icon name="arrow" size={13} /></button></div>
          </section>

          <footer className="page-footer"><span>AGENTSAFE <span>·</span> DEFENDERS IN CONTROL</span><span>Demo data only <i /> Fuji testnet</span></footer>
        </div>
      </section>

      {notice && <div className="toast" role="status"><span className="toast-icon"><Icon name="check" size={14} /></span><span>{notice}</span><button aria-label="Dismiss" onClick={() => setNotice("")}>×</button></div>}
    </main>
  );
}
