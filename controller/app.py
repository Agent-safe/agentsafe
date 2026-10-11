"""Local-first AgentSafe policy controller prototype.

The only connectors shipped here are local, side-effect-free demo connectors.
Real external tools must be registered behind the same authorize/execute path.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import subprocess
import shutil
import urllib.error
import urllib.request
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from contextlib import contextmanager
from pathlib import Path
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, TypedDict

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # Local SQLite mode does not require the Postgres driver.
    psycopg = None
    dict_row = None


ROOT = Path(__file__).resolve().parent
DATABASE_URL = os.environ.get("DATABASE_URL")
AUDIT_ANCHOR_ADDRESS = os.environ.get("AUDIT_ANCHOR_ADDRESS", os.environ.get("NEXT_PUBLIC_AUDIT_ANCHOR_ADDRESS", "0xde5648cded04c9eca1124234867780604f89ff7e"))
FUJI_RPC_URL = os.environ.get("FUJI_RPC_URL", "https://api.avax-test.network/ext/bc/C/rpc")
FUJI_CHAIN_ID = 43113
ANCHOR_EVENT_TOPIC = "0xdb96f57960f1a8d2f2350bb63e1e1664fc0c2d57304af37731bb95cf619e26cd"
DATA_DIR = Path(os.environ.get("AGENTSAFE_DATA_DIR", Path("/tmp/agentsafe") if DATABASE_URL else ROOT / ".data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.environ.get("AGENTSAFE_DB", DATA_DIR / "agentsafe.sqlite3"))
KEY_PATH = Path(os.environ.get("AGENTSAFE_SIGNING_KEY", DATA_DIR / "ed25519.key"))
AUDIT_LOG_PATH = Path(os.environ.get("AGENTSAFE_AUDIT_LOG", DATA_DIR / "audit.log"))
ZK_DIR = Path(os.environ.get("AGENTSAFE_ZK_DIR", ROOT / "zk"))
NODE_BIN = shutil.which("node") or str(ROOT / "node_modules" / "node" / "bin" / "node")
@asynccontextmanager
async def lifespan(_app):
    init_db()
    signer()
    yield


app = FastAPI(title="AgentSafe Local Controller", version="0.1.0", description="Local authorization gate, demo connectors, signed receipts and verifiable journal.", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=ROOT / "ui" / "assets", check_dir=False), name="dashboard-assets")
DEMO_AGENT_KEYS: dict[str, Ed25519PrivateKey] = {}
_DB_INITIALIZED = False
_DB_INIT_LOCK = threading.Lock()


@app.middleware("http")
async def local_browser_guard(request: Request, call_next):
    # This prototype is a local controller. Prevent remote websites and DNS-rebinding
    # pages from driving its state-changing endpoints through the user's browser.
    host = request.headers.get("host", "").split(":", 1)[0].strip("[]").lower()
    allowed_hosts = {"127.0.0.1", "localhost", "::1"}
    allowed_hosts.update(value.strip().lower() for value in os.environ.get("AGENTSAFE_ALLOWED_HOSTS", "").split(",") if value.strip())
    if os.environ.get("VERCEL_URL"):
        allowed_hosts.add(os.environ["VERCEL_URL"].split(":", 1)[0].lower())
    if host not in allowed_hosts:
        return JSONResponse({"detail": "bind this prototype to loopback only"}, status_code=421)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",", 1)[0].strip()
        if origin and origin.rstrip("/").lower() != f"{scheme}://{request.headers.get('host', '').lower()}".rstrip("/"):
            return JSONResponse({"detail": "cross-origin state changes are not allowed"}, status_code=403)
    return await call_next(request)


@app.get("/", include_in_schema=False)
def console():
    return FileResponse(ROOT / "ui" / "index.html")


class PostgresAdapter:
    """Small compatibility layer for the controller's parameterized SQL."""
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, params=()):
        return self.connection.execute(query.replace("?", "%s"), params)

    def executescript(self, script):
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)

    def close(self):
        self.connection.close()


def connect():
    if DATABASE_URL:
        if psycopg is None:
            raise RuntimeError("Install the psycopg[binary] dependency for DATABASE_URL mode")
        return PostgresAdapter(psycopg.connect(DATABASE_URL, row_factory=dict_row, prepare_threshold=None))
    db = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    return db


@contextmanager
def transaction(immediate: bool = False):
    db = connect()
    if DATABASE_URL:
        try:
            with db.connection.transaction():
                # Serialize writes only. Read-only dashboard requests must not
                # queue behind each other on Supabase's transaction pooler.
                if immediate:
                    db.execute("SELECT pg_advisory_xact_lock(73104620261010)")
                yield db
        finally:
            db.close()
        return
    db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def private_commitment(payload: bytes) -> str:
    secret = signer().private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    key = hashlib.sha256(secret + b"AgentSafe local HMAC commitment v1").digest()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def private_fingerprint(value: str | None) -> str | None:
    return private_commitment(value.encode()) if value is not None else None


def zk_call(mode: str, payload: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    if os.environ.get("VERCEL"):
        deployment = os.environ.get("VERCEL_URL")
        if not deployment:
            raise RuntimeError("ZK service URL unavailable")
        headers = {"Content-Type": "application/json"}
        bypass = os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET")
        if bypass:
            headers["x-vercel-protection-bypass"] = bypass
        request = urllib.request.Request(
            f"https://{deployment}/api/zk",
            data=canonical({"mode": mode, "payload": payload}),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get("error", "request rejected")
            except (json.JSONDecodeError, AttributeError):
                detail = "request rejected"
            raise RuntimeError(f"ZK service rejected request ({exc.code}): {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"ZK service request failed: {getattr(exc, 'reason', type(exc).__name__)}") from exc
        if result.get("error"):
            raise RuntimeError(result["error"])
        return result
    try:
        result = subprocess.run([NODE_BIN, str(ZK_DIR / "tool.mjs"), mode], input=canonical(payload), capture_output=True, timeout=timeout, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("ZK tool unavailable; install zk dependencies and generate the local proving key") from exc
    try:
        response = json.loads(result.stdout or b"{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("ZK tool returned an invalid response") from exc
    if result.returncode or response.get("error"):
        raise RuntimeError(response.get("error", "ZK operation failed"))
    return response


def verify_zk_proof_independently(proof: dict[str, Any]) -> bool:
    if os.environ.get("VERCEL"):
        try:
            return zk_call("verify", proof).get("valid") is True
        except RuntimeError:
            return False
    verifier = ZK_DIR / "verify.mjs"
    with tempfile.NamedTemporaryFile("w", suffix=".json", dir=ZK_DIR, delete=False) as handle:
        json.dump(proof, handle, separators=(",", ":"))
        proof_path = Path(handle.name)
    try:
        result = subprocess.run([NODE_BIN, str(verifier), str(proof_path)], capture_output=True, text=True, timeout=60, check=False)
        return result.returncode == 0 and json.loads(result.stdout).get("valid") is True
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return False
    finally:
        proof_path.unlink(missing_ok=True)


def budget_cents(value: float) -> int:
    try:
        cents = int(Decimal(str(value)) * 100)
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(422, "budget must be a decimal amount") from exc
    if cents <= 0:
        raise HTTPException(422, "budget must be at least one cent")
    return cents


def zk_mandate(mission_id: str, agent_public_key: str, actions: list[str], resources: list[str], constraints: dict[str, Any], budget: float, expires_at: int, can_delegate: bool, allow_external: bool, nonce: str) -> dict[str, Any]:
    return {"missionId": mission_id, "agentPublicKey": agent_public_key, "actions": sorted(set(actions)), "resources": sorted(set(resources)), "parameterConstraints": constraints, "budgetCents": budget_cents(budget), "expiresAt": expires_at, "canDelegate": can_delegate, "allowExternal": allow_external, "nonce": nonce}


def signer() -> Ed25519PrivateKey:
    key_b64 = os.environ.get("AGENTSAFE_SIGNING_KEY_B64")
    if key_b64:
        return serialization.load_pem_private_key(base64.b64decode(key_b64), password=None)
    if DATABASE_URL and not KEY_PATH.exists():
        raise RuntimeError("AGENTSAFE_SIGNING_KEY_B64 is required for persistent online database mode")
    if KEY_PATH.exists():
        return serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
    key = Ed25519PrivateKey.generate()
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    try:
        KEY_PATH.chmod(0o600)
    except OSError:
        pass
    return key


def sign(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(signer().sign(canonical(payload))).decode().rstrip("=")


def decode64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_signature(public: str, signature: str, payload: dict[str, Any]) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(decode64(public)).verify(decode64(signature), canonical(payload))
        return True
    except Exception:
        return False


def fresh_agent_key() -> tuple[str, Ed25519PrivateKey]:
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode().rstrip("="), private


def public_key() -> str:
    raw = signer().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def init_db() -> None:
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return
    with _DB_INIT_LOCK:
        if _DB_INITIALIZED:
            return
        _init_db_once()
        _DB_INITIALIZED = True


def _init_db_once() -> None:
    with transaction(immediate=True) as db:
        event_sequence = "BIGSERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
        db.executescript(f"""
        CREATE TABLE IF NOT EXISTS missions(id TEXT PRIMARY KEY, name TEXT NOT NULL, budget_limit REAL NOT NULL, budget_used REAL NOT NULL DEFAULT 0, expires_at INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS grants(id TEXT PRIMARY KEY, mission_id TEXT NOT NULL REFERENCES missions(id), agent_id TEXT NOT NULL, agent_public_key TEXT NOT NULL, parent_id TEXT REFERENCES grants(id), actions TEXT NOT NULL, resources TEXT NOT NULL, parameter_constraints TEXT NOT NULL DEFAULT '{{}}', budget_limit REAL NOT NULL, budget_used REAL NOT NULL DEFAULT 0, budget_reserved REAL NOT NULL DEFAULT 0, expires_at INTEGER NOT NULL, can_delegate INTEGER NOT NULL, allow_external INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0, mandate TEXT NOT NULL, signature TEXT NOT NULL, zk_nonce TEXT, zk_commitment TEXT, zk_proof TEXT, zk_public_signals TEXT, zk_proof_sha256 TEXT, zk_verified INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS events(seq {event_sequence}, event_id TEXT UNIQUE NOT NULL, created_at INTEGER NOT NULL, body TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL, signature TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, request_id TEXT NOT NULL, tool TEXT NOT NULL, summary TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, request_hash TEXT NOT NULL, expires_at INTEGER NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY, code TEXT NOT NULL, severity TEXT NOT NULL, mission_id TEXT, grant_id TEXT, request_id TEXT, reason TEXT NOT NULL, created_at INTEGER NOT NULL, resolved_at INTEGER);
        CREATE TABLE IF NOT EXISTS agent_groups(id TEXT PRIMARY KEY, name TEXT NOT NULL, department TEXT NOT NULL, budget_limit REAL NOT NULL, mission_id TEXT NOT NULL, grant_id TEXT NOT NULL, member_names TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS company_profile(slug TEXT PRIMARY KEY, name TEXT NOT NULL, industry TEXT NOT NULL, size TEXT NOT NULL, description TEXT NOT NULL, demo_context TEXT NOT NULL, use_cases TEXT NOT NULL, updated_at BIGINT NOT NULL);
        CREATE TABLE IF NOT EXISTS journal_anchors(id TEXT PRIMARY KEY, journal_head TEXT NOT NULL, commitment TEXT NOT NULL UNIQUE, salt TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'prepared', tx_hash TEXT, created_at BIGINT NOT NULL, confirmed_at BIGINT);
        """)
        if DATABASE_URL:
            # The controller connects as the database owner; browser-facing Supabase roles
            # must not be able to read or mutate the tenant journal directly.
            for table in ("missions", "grants", "events", "artifacts", "approvals", "alerts", "agent_groups", "company_profile", "journal_anchors"):
                db.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
                db.execute(f"REVOKE ALL ON TABLE {table} FROM anon, authenticated")
        db.execute(
            "INSERT INTO company_profile(slug,name,industry,size,description,demo_context,use_cases,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(slug) DO NOTHING",
            (
                "acme-commerce",
                "Acme Commerce",
                "E-commerce and retail operations",
                "Synthetic mid-market company · 500 employees",
                "A fictional online retailer using AI agents to support procurement and finance operations.",
                canonical({
                    "notice": "All people, suppliers, invoices, amounts, and records are synthetic demo data.",
                    "agents": [
                        {"id": "ag-context-scout", "name": "ag-context-scout", "role": "Finds supplier and policy context; read-only."},
                        {"id": "ag-risk-sentinel", "name": "ag-risk-sentinel", "role": "Checks data classification and destination risk."},
                        {"id": "ag-procurement-planner", "name": "ag-procurement-planner", "role": "Proposes a purchase or data transfer; never executes tools directly."},
                    ],
                    "data": [
                        {"id": "invoice-1842", "label": "Confidential supplier invoice", "classification": "confidential"},
                        {"id": "supplier-x", "label": "Approved supplier #42", "classification": "internal"},
                    ],
                    "guardrails": {
                        "mission_budget_eur": 100,
                        "child_agent_budget_eur": 80,
                        "approved_supplier_id": 42,
                        "external_email_allowed_for_confidential_data": False,
                        "purchase_requires_human_approval": True,
                    },
                }).decode(),
                canonical([
                    {"id": "prevent-data-leak", "title": "Stop confidential invoice exfiltration", "trigger": "An agent proposes sending invoice-1842 to an external personal address.", "expected": "Block before the connector runs; record a signed decision."},
                    {"id": "enforce-procurement-limits", "title": "Keep purchase actions inside the mission mandate", "trigger": "An agent proposes a purchase for supplier #42.", "expected": "Enforce the €80 child cap and €100 shared mission budget; require exact-request human approval."},
                    {"id": "investigate-and-revoke", "title": "Let the SOC trace and stop a risky mission", "trigger": "Repeated denials, expired authority, or a replay attempt appears.", "expected": "Show the actor, delegated mandate, reason, signed receipt, and allow a kill switch."},
                ]).decode(),
                int(time.time()),
            ),
        )
        # Upgrade the fictional Acme demo roster in-place while leaving all live tenant
        # policy fields and historical signed grant subjects untouched.
        company_row = db.execute("SELECT demo_context FROM company_profile WHERE slug=?", ("acme-commerce",)).fetchone()
        if company_row:
            demo_context = json.loads(company_row["demo_context"])
            demo_agents = demo_context.get("agents", [])
            defaults = [
                ("ag-context-scout", "Finds supplier and policy context; read-only."),
                ("ag-risk-sentinel", "Checks data classification and destination risk."),
                ("ag-procurement-planner", "Proposes a purchase or data transfer; never executes tools directly."),
            ]
            changed = False
            for agent, (agent_id, role) in zip(demo_agents, defaults):
                if agent.get("id") != agent_id or agent.get("name") != agent_id:
                    agent["id"] = agent_id
                    agent["name"] = agent_id
                    agent["role"] = role
                    changed = True
            if changed:
                db.execute("UPDATE company_profile SET demo_context=?,updated_at=? WHERE slug=?", (canonical(demo_context).decode(), int(time.time()), "acme-commerce"))
        if DATABASE_URL:
            # Idempotent upgrades for online instances whose schema predates the controller.
            for column, definition in {
                "budget_reserved": "DOUBLE PRECISION NOT NULL DEFAULT 0",
                "agent_public_key": "TEXT NOT NULL DEFAULT ''",
                "parameter_constraints": "TEXT NOT NULL DEFAULT '{}'",
                "zk_nonce": "TEXT", "zk_commitment": "TEXT", "zk_proof": "TEXT",
                "zk_public_signals": "TEXT", "zk_proof_sha256": "TEXT",
                "zk_verified": "INTEGER NOT NULL DEFAULT 0",
            }.items():
                db.execute(f"ALTER TABLE grants ADD COLUMN IF NOT EXISTS {column} {definition}")
            db.execute("ALTER TABLE alerts ALTER COLUMN created_at TYPE BIGINT")
            return
        # Upgrade databases created by earlier prototype iterations.
        columns = {row["name"] for row in db.execute("PRAGMA table_info(grants)")}
        if "budget_reserved" not in columns:
            db.execute("ALTER TABLE grants ADD COLUMN budget_reserved REAL NOT NULL DEFAULT 0")
        if "agent_public_key" not in columns:
            db.execute("ALTER TABLE grants ADD COLUMN agent_public_key TEXT NOT NULL DEFAULT ''")
        migrations = {
            "parameter_constraints": "TEXT NOT NULL DEFAULT '{}'",
            "zk_nonce": "TEXT",
            "zk_commitment": "TEXT",
            "zk_proof": "TEXT",
            "zk_public_signals": "TEXT",
            "zk_proof_sha256": "TEXT",
            "zk_verified": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in migrations.items():
            if column not in columns:
                db.execute(f"ALTER TABLE grants ADD COLUMN {column} {definition}")


def append_event(db: sqlite3.Connection, body: dict[str, Any]) -> dict[str, Any]:
    previous = db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
    prev_hash = previous["hash"] if previous else "0" * 64
    event_id = str(uuid.uuid4())
    created_at = int(time.time())
    content = {"event_id": event_id, "created_at": created_at, **body}
    digest = sha(bytes.fromhex(prev_hash) + canonical(content))
    signature = sign({"hash": digest, "event_id": event_id})
    db.execute("INSERT INTO events(event_id,created_at,body,prev_hash,hash,signature) VALUES(?,?,?,?,?,?)", (event_id, created_at, canonical(content).decode(), prev_hash, digest, signature))
    record = {**content, "prev_hash": prev_hash, "hash": digest, "signature": signature}
    if not DATABASE_URL:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG_PATH.open("ab") as log:
            log.write(canonical(record) + b"\n")
            log.flush()
            os.fsync(log.fileno())
    return record


class MissionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    root_agent: str = Field(min_length=1, max_length=120)
    root_public_key: str
    actions: list[str] = Field(min_length=1)
    resources: list[str] = Field(min_length=1)
    parameter_constraints: dict[str, Any] = Field(default_factory=dict)
    budget_limit: float = Field(gt=0)
    ttl_seconds: int = Field(default=1800, gt=0, le=86400)
    allow_external: bool = False
    can_delegate: bool = True


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    department: str = Field(min_length=1, max_length=120)
    actions: list[str] = Field(min_length=1)
    resources: list[str] = Field(min_length=1)
    parameter_constraints: dict[str, Any] = Field(default_factory=dict)
    budget_limit: float = Field(gt=0)
    ttl_seconds: int = Field(default=3600, gt=0, le=86400)
    members: list[str] = Field(default_factory=list)


class DelegateRequest(BaseModel):
    parent_grant_id: str
    child_agent: str
    child_public_key: str
    parent_signature: str
    actions: list[str]
    resources: list[str]
    parameter_constraints: dict[str, Any] = Field(default_factory=dict)
    budget_limit: float = Field(gt=0)
    ttl_seconds: int = Field(gt=0, le=86400)
    can_delegate: bool = False


class ActionRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=120)
    mission_id: str
    grant_id: str
    agent_id: str
    tool: str
    resource: str
    amount: float = Field(default=0, ge=0)
    destination: str | None = None
    data_label: Literal["public", "internal", "confidential"] = "internal"
    payload: dict[str, Any] = Field(default_factory=dict)
    approval_id: str | None = None
    agent_signature: str


class DemoFinish(BaseModel):
    pending_order: ActionRequest
    approval_id: str


class BedrockDemoRequest(BaseModel):
    scenario: Literal["confidential_external_share"] = "confidential_external_share"


class DemoAgentState(TypedDict, total=False):
    mission: dict[str, Any]
    child: dict[str, Any]
    child_private: Ed25519PrivateKey
    proposals: list[ActionRequest]
    results: dict[str, str]
    details: dict[str, str]
    pending_order: dict[str, Any]
    mission_id: str
    approved: dict[str, Any]
    engine: str


def mandate_payload(mission_id: str, agent: str, agent_public: str, parent_id: str | None, actions: list[str], resources: list[str], constraints: dict[str, Any], budget: float, expires: int, delegate: bool, external: bool, zk_commitment: str) -> dict[str, Any]:
    return {"mission_id": mission_id, "agent_id": agent, "agent_public_key": agent_public, "parent_grant_id": parent_id, "actions": sorted(set(actions)), "resources": sorted(set(resources)), "parameter_constraints": constraints, "budget_limit": budget, "expires_at": expires, "can_delegate": delegate, "allow_external": external, "zk_commitment": zk_commitment}


def delegation_signing_payload(req: DelegateRequest, mission_id: str) -> dict[str, Any]:
    return {"mission_id": mission_id, "parent_grant_id": req.parent_grant_id, "child_agent": req.child_agent, "child_public_key": req.child_public_key, "actions": sorted(set(req.actions)), "resources": sorted(set(req.resources)), "parameter_constraints": req.parameter_constraints, "budget_limit": float(req.budget_limit), "ttl_seconds": req.ttl_seconds, "can_delegate": req.can_delegate}


@app.get("/health")
def health():
    init_db()
    try:
        zk_ready = zk_call("self-check", {}).get("setupReady") is True
    except RuntimeError:
        zk_ready = False
    return {"ok": True, "mode": "serverless" if os.environ.get("VERCEL") else "local", "database": "postgresql" if DATABASE_URL else "sqlite", "signing": "ed25519", "delegation_proofs": "Groth16 ready" if zk_ready else "setup required", "audit_log": "JSONL", "connectors": ["procurement.create_order", "records.share_local"], "external_connectors": False}


@app.get("/company")
def company_context():
    with transaction() as db:
        row = db.execute("SELECT * FROM company_profile WHERE slug=?", ("acme-commerce",)).fetchone()
    if row is None:
        raise HTTPException(503, "The demo company profile has not been initialized")
    return {
        "slug": row["slug"],
        "name": row["name"],
        "industry": row["industry"],
        "size": row["size"],
        "description": row["description"],
        "context": json.loads(row["demo_context"]),
        "use_cases": json.loads(row["use_cases"]),
        "updated_at": row["updated_at"],
    }


@app.get("/zk/status")
def zk_status():
    error = None
    try:
        ready = zk_call("self-check", {}).get("setupReady") is True
    except RuntimeError as exc:
        ready = False
        error = str(exc)
    runtime = {"mode": "remote Node.js function", "endpoint": "/api/zk"} if os.environ.get("VERCEL") else {"node": NODE_BIN if Path(NODE_BIN).is_file() else None, "tool_present": (ZK_DIR / "tool.mjs").is_file(), "wasm_present": (ZK_DIR / "build/delegation_js/delegation.wasm").is_file(), "proving_key_present": (ZK_DIR / "build/delegation_final.zkey").is_file()}
    return {"ready": ready, "protocol": "Groth16/bn128", "circuit": "delegation-subset-v1", "setup": "local development only; not an audited production ceremony", "max_scope_entries": 8, "runtime": runtime, "error": error}


@app.get("/keys/public")
def get_public_key():
    return {"algorithm": "Ed25519", "public_key": public_key()}


@app.post("/missions")
def create_mission(req: MissionCreate):
    init_db()
    try:
        if len(decode64(req.root_public_key)) != 32:
            raise ValueError()
    except Exception:
        raise HTTPException(422, "root_public_key must be a base64url Ed25519 public key")
    if len(set(req.actions)) > 8 or len(set(req.resources)) > 8 or len(req.parameter_constraints) > 8:
        raise HTTPException(422, "ZK delegation supports up to 8 tools, resources, and exact parameter constraints per grant")
    now = int(time.time())
    mission_id, grant_id = str(uuid.uuid4()), str(uuid.uuid4())
    exp = now + req.ttl_seconds
    nonce = secrets.token_hex(32)
    try:
        commitment = zk_call("commitment", zk_mandate(mission_id, req.root_public_key, req.actions, req.resources, req.parameter_constraints, req.budget_limit, exp, req.can_delegate, req.allow_external, nonce))["commitment"]
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    mandate = mandate_payload(mission_id, req.root_agent, req.root_public_key, None, req.actions, req.resources, req.parameter_constraints, req.budget_limit, exp, req.can_delegate, req.allow_external, commitment)
    signature = sign(mandate)
    with transaction(immediate=True) as db:
        db.execute("INSERT INTO missions(id,name,budget_limit,expires_at,created_at) VALUES(?,?,?,?,?)", (mission_id, req.name, req.budget_limit, exp, now))
        db.execute("INSERT INTO grants(id,mission_id,agent_id,agent_public_key,actions,resources,parameter_constraints,budget_limit,expires_at,can_delegate,allow_external,mandate,signature,zk_nonce,zk_commitment) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (grant_id, mission_id, req.root_agent, req.root_public_key, json.dumps(sorted(set(req.actions))), json.dumps(sorted(set(req.resources))), canonical(req.parameter_constraints).decode(), req.budget_limit, exp, int(req.can_delegate), int(req.allow_external), canonical(mandate).decode(), signature, nonce, commitment))
        append_event(db, {"type": "mission.created", "mission_id": mission_id, "grant_id": grant_id, "agent_id": req.root_agent, "mandate_hash": sha(canonical(mandate))})
    return {"mission_id": mission_id, "grant_id": grant_id, "mandate": mandate, "mandate_signature": signature, "signing_public_key": public_key()}


@app.post("/groups")
def create_group(req: GroupCreate):
    init_db()
    if len(set(req.actions)) > 8 or len(set(req.resources)) > 8 or len(req.parameter_constraints) > 8:
        raise HTTPException(422, "ZK group policies support at most 8 tools, resources, and parameter restrictions")
    group_id = str(uuid.uuid4())
    group_public_key, _group_private_key = fresh_agent_key()
    mission = create_mission(MissionCreate(name=req.name, root_agent=f"group:{group_id}", root_public_key=group_public_key, actions=req.actions, resources=req.resources, parameter_constraints=req.parameter_constraints, budget_limit=req.budget_limit, ttl_seconds=req.ttl_seconds, can_delegate=True))
    with transaction(immediate=True) as db:
        db.execute("INSERT INTO agent_groups(id,name,department,budget_limit,mission_id,grant_id,member_names,created_at) VALUES(?,?,?,?,?,?,?,?)", (group_id, req.name, req.department, req.budget_limit, mission["mission_id"], mission["grant_id"], canonical(req.members).decode(), int(time.time())))
        append_event(db, {"type": "group.policy.created", "group_id": group_id, "mission_id": mission["mission_id"], "grant_id": mission["grant_id"], "department": req.department, "member_count": len(req.members), "member_credentials": "not_issued_in_mvp"})
    return {"group_id": group_id, "mission_id": mission["mission_id"], "grant_id": mission["grant_id"], "mandate": mission["mandate"], "policy_status": "signed group-principal policy created", "member_credentials": "roster only; individual derived credentials are not implemented"}


@app.get("/groups")
def list_groups():
    init_db()
    with transaction() as db:
        rows = [dict(row) for row in db.execute("SELECT g.id,g.name,g.department,g.budget_limit,g.mission_id,g.grant_id,g.member_names,g.created_at,m.budget_used,m.expires_at,m.revoked,gr.actions,gr.resources,gr.parameter_constraints,gr.can_delegate,gr.revoked AS grant_revoked,gr.zk_commitment FROM agent_groups g JOIN missions m ON m.id=g.mission_id JOIN grants gr ON gr.id=g.grant_id ORDER BY g.created_at DESC")]
    for row in rows:
        row["members"] = json.loads(row.pop("member_names"))
        row["actions"] = json.loads(row["actions"])
        row["resources"] = json.loads(row["resources"])
        row["parameter_constraints"] = json.loads(row["parameter_constraints"])
    return {"groups": rows, "credential_limit": "Roster only. Each member does not yet receive a separate group-derived cryptographic credential."}


@app.post("/delegations")
def delegate(req: DelegateRequest):
    init_db()
    now = int(time.time())
    if len(set(req.actions)) > 8 or len(set(req.resources)) > 8 or len(req.parameter_constraints) > 8:
        raise HTTPException(422, "ZK delegation supports up to 8 tools, resources, and exact parameter constraints per grant")
    with transaction(immediate=True) as db:
        parent = db.execute("SELECT * FROM grants WHERE id=?", (req.parent_grant_id,)).fetchone()
        if not parent:
            raise HTTPException(404, "parent grant not found")
        actions, resources = set(json.loads(parent["actions"])), set(json.loads(parent["resources"]))
        parent_constraints = json.loads(parent["parameter_constraints"] or "{}")
        if parent["revoked"] or not parent["can_delegate"] or now >= parent["expires_at"]:
            raise HTTPException(403, "parent mandate is revoked, expired, or non-delegable")
        if not set(req.actions) <= actions or not set(req.resources) <= resources:
            raise HTTPException(403, "delegation expands parent scope")
        if any(req.parameter_constraints.get(key) != value for key, value in parent_constraints.items()):
            raise HTTPException(403, "delegation removes or changes a parent parameter restriction")
        if req.budget_limit > parent["budget_limit"] - parent["budget_used"] - parent["budget_reserved"]:
            raise HTTPException(403, "delegation exceeds remaining parent budget")
        claim = delegation_signing_payload(req, parent["mission_id"])
        if not verify_signature(parent["agent_public_key"], req.parent_signature, claim):
            raise HTTPException(403, "delegation request signature does not match parent agent identity")
        try:
            if len(decode64(req.child_public_key)) != 32:
                raise ValueError()
        except Exception:
            raise HTTPException(422, "child_public_key must be a base64url Ed25519 public key")
        exp = min(now + req.ttl_seconds, parent["expires_at"])
        mission = db.execute("SELECT * FROM missions WHERE id=?", (parent["mission_id"],)).fetchone()
        if not mission or mission["revoked"] or now >= mission["expires_at"]:
            raise HTTPException(403, "mission is revoked or expired")
        child_id = str(uuid.uuid4())
        child_can_delegate = bool(req.can_delegate and parent["can_delegate"])
        child_allow_external = bool(parent["allow_external"])
        parent_nonce = parent["zk_nonce"] or secrets.token_hex(32)
        parent_commitment = parent["zk_commitment"]
        if not parent_commitment:
            try:
                parent_commitment = zk_call("commitment", zk_mandate(parent["mission_id"], parent["agent_public_key"], actions, resources, parent_constraints, parent["budget_limit"], parent["expires_at"], bool(parent["can_delegate"]), bool(parent["allow_external"]), parent_nonce))["commitment"]
            except RuntimeError as exc:
                raise HTTPException(503, str(exc)) from exc
        child_nonce = secrets.token_hex(32)
        try:
            proof = zk_call("prove", {
                "parent": zk_mandate(parent["mission_id"], parent["agent_public_key"], list(actions), list(resources), parent_constraints, parent["budget_limit"], parent["expires_at"], bool(parent["can_delegate"]), bool(parent["allow_external"]), parent_nonce),
                "child": zk_mandate(parent["mission_id"], req.child_public_key, req.actions, req.resources, req.parameter_constraints, req.budget_limit, exp, child_can_delegate, child_allow_external, child_nonce),
            }, timeout=300)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        if not proof.get("verified") or proof.get("parentCommitment") != parent_commitment:
            raise HTTPException(503, "delegation proof failed circuit verification; grant was not issued")
        if not verify_zk_proof_independently(proof):
            raise HTTPException(503, "independent delegation proof verification failed; grant was not issued")
        mandate = mandate_payload(parent["mission_id"], req.child_agent, req.child_public_key, parent["id"], req.actions, req.resources, req.parameter_constraints, req.budget_limit, exp, child_can_delegate, child_allow_external, proof["childCommitment"])
        signature = sign(mandate)
        if parent["zk_commitment"] is None:
            parent_mandate = json.loads(parent["mandate"])
            parent_mandate.update({"parameter_constraints": parent_constraints, "zk_commitment": parent_commitment})
            db.execute("UPDATE grants SET mandate=?,signature=?,zk_nonce=?,zk_commitment=? WHERE id=?", (canonical(parent_mandate).decode(), sign(parent_mandate), parent_nonce, parent_commitment, parent["id"]))
        db.execute("INSERT INTO grants(id,mission_id,agent_id,agent_public_key,parent_id,actions,resources,parameter_constraints,budget_limit,expires_at,can_delegate,allow_external,mandate,signature,zk_nonce,zk_commitment,zk_proof,zk_public_signals,zk_proof_sha256,zk_verified) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (child_id, parent["mission_id"], req.child_agent, req.child_public_key, parent["id"], json.dumps(sorted(set(req.actions))), json.dumps(sorted(set(req.resources))), canonical(req.parameter_constraints).decode(), req.budget_limit, exp, int(mandate["can_delegate"]), parent["allow_external"], canonical(mandate).decode(), signature, child_nonce, proof["childCommitment"], canonical(proof["proof"]).decode(), canonical(proof["publicSignals"]).decode(), proof["proofSha256"], 1))
        db.execute("UPDATE grants SET budget_reserved=budget_reserved+? WHERE id=?", (req.budget_limit, parent["id"]))
        append_event(db, {"type": "delegation.issued", "mission_id": parent["mission_id"], "parent_grant_id": parent["id"], "child_grant_id": child_id, "agent_id": req.child_agent, "mandate_hash": sha(canonical(mandate)), "zk_protocol": proof["protocol"], "zk_proof_sha256": proof["proofSha256"]})
    return {"grant_id": child_id, "mandate": mandate, "mandate_signature": signature, "signing_public_key": public_key(), "zk_status": "verified", "zk_proof": proof, "zk_verifier": "separate snarkjs verification process", "setup": proof["setup"]}


@app.post("/approvals")
def issue_approval(mission_id: str, request_hash: str, ttl_seconds: int = 300):
    """Local demo approval issuer. Production must authenticate a human approver."""
    if len(request_hash) != 64 or ttl_seconds < 1 or ttl_seconds > 3600:
        raise HTTPException(422, "request_hash must be SHA-256 hex and ttl 1..3600 seconds")
    approval_id = str(uuid.uuid4())
    with transaction(immediate=True) as db:
        mission = db.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
        if not mission or mission["revoked"]:
            raise HTTPException(404, "active mission not found")
        db.execute("INSERT INTO approvals(id,mission_id,request_hash,expires_at) VALUES(?,?,?,?)", (approval_id, mission_id, request_hash, int(time.time()) + ttl_seconds))
        append_event(db, {"type": "approval.issued", "mission_id": mission_id, "approval_id": approval_id, "request_hash": request_hash})
    return {"approval_id": approval_id, "request_hash": request_hash, "expires_in_seconds": ttl_seconds, "note": "demo issuer; production requires authenticated human approval"}


def action_digest(req: ActionRequest) -> str:
    parameters = {"mission_id": req.mission_id, "grant_id": req.grant_id, "agent_id": req.agent_id, "tool": req.tool, "resource": req.resource, "amount": float(req.amount), "destination": req.destination, "data_label": req.data_label, "payload": req.payload}
    return private_commitment(canonical(parameters))


def action_signing_payload(req: ActionRequest) -> dict[str, Any]:
    payload = req.model_dump(exclude={"agent_signature", "approval_id"})
    payload["amount"] = float(req.amount)
    return payload


def signed_action(private: Ed25519PrivateKey, **fields: Any) -> ActionRequest:
    unsigned = ActionRequest(agent_signature="pending", **fields)
    signature = base64.urlsafe_b64encode(private.sign(canonical(action_signing_payload(unsigned)))).decode().rstrip("=")
    return unsigned.model_copy(update={"agent_signature": signature})


def add_alert(db: sqlite3.Connection, code: str, severity: str, mission_id: str | None, grant_id: str | None, request_id: str | None, reason: str) -> dict[str, Any]:
    # Millisecond timestamps preserve alert ordering when several denials arrive
    # during one second, including across serverless instances.
    alert_id, created_at = str(uuid.uuid4()), int(time.time() * 1000)
    db.execute("INSERT INTO alerts(id,code,severity,mission_id,grant_id,request_id,reason,created_at) VALUES(?,?,?,?,?,?,?,?)", (alert_id, code, severity, mission_id, grant_id, request_id, reason, created_at))
    append_event(db, {"type": "alert.raised", "alert_id": alert_id, "code": code, "severity": severity, "mission_id": mission_id, "grant_id": grant_id, "request_id": request_id, "reason": reason})
    return {"id": alert_id, "code": code, "severity": severity, "mission_id": mission_id, "grant_id": grant_id, "request_id": request_id, "reason": reason, "created_at": created_at, "resolved_at": None}


def alert_code(reason: str) -> tuple[str, str]:
    lowered = reason.lower()
    if "signature is invalid" in lowered:
        return "CRYPTO-SIGNATURE", "critical"
    if "already used" in lowered or "replay" in lowered:
        return "CRYPTO-REPLAY", "critical"
    if "outside grant scope" in lowered or "no connector registered" in lowered:
        return "ASI02-TOOL-MISUSE", "high"
    if "budget" in lowered:
        return "ASI03-PRIVILEGE-ABUSE", "high"
    if "quantitative limit" in lowered or "max_amount" in lowered or "max_transaction_eur" in lowered:
        return "ASI03-PRIVILEGE-ABUSE", "high"
    return "POLICY-DENY", "medium"


def record_action_denial(db: sqlite3.Connection, req: "ActionRequest", reason: str, now: int) -> None:
    code, severity = alert_code(reason)
    add_alert(db, code, severity, req.mission_id, req.grant_id, req.request_id, reason)
    grant_expr = "body::jsonb ->> 'grant_id'" if DATABASE_URL else "json_extract(body,'$.grant_id')"
    decision_expr = "body::jsonb ->> 'decision'" if DATABASE_URL else "json_extract(body,'$.decision')"
    type_expr = "body::jsonb ->> 'type'" if DATABASE_URL else "json_extract(body,'$.type')"
    recent = db.execute(f"SELECT COUNT(*) AS n FROM events WHERE created_at>=? AND {grant_expr}=? AND {decision_expr}='block' AND {type_expr}='action.decision'", (now - 60, req.grant_id)).fetchone()["n"]
    if recent == 3:
        db.execute("WITH RECURSIVE grant_tree(id) AS (SELECT id FROM grants WHERE id=? UNION ALL SELECT g.id FROM grants g JOIN grant_tree p ON g.parent_id=p.id) UPDATE grants SET revoked=1 WHERE id IN (SELECT id FROM grant_tree)", (req.grant_id,))
        append_event(db, {"type": "grant.auto_revoked", "grant_id": req.grant_id, "mission_id": req.mission_id, "trigger": "3 denied actions in 60 seconds", "affected_descendants": True})
        add_alert(db, "AUTO-KILL-SWITCH", "critical", req.mission_id, req.grant_id, req.request_id, "Grant tree revoked after 3 denied actions in 60 seconds")


@app.post("/actions/hash")
def preview_action_hash(req: ActionRequest):
    return {"parameter_hash": action_digest(req), "note": "This binds an approval to the exact declared parameters; the payload itself is not stored in the journal."}


@app.post("/actions")
def protected_action(req: ActionRequest):
    init_db()
    now = int(time.time())
    digest = action_digest(req)
    decision = "allow"
    reason = "mandate and policies permit this local connector"
    with transaction(immediate=True) as db:
        request_expr = "body::jsonb ->> 'request_id'" if DATABASE_URL else "json_extract(body,'$.request_id')"
        if db.execute(f"SELECT 1 FROM events WHERE {request_expr}=?", (req.request_id,)).fetchone():
            append_event(db, {"type": "action.decision", "request_id": req.request_id, "mission_id": req.mission_id, "grant_id": req.grant_id, "agent_id": req.agent_id, "tool": req.tool, "decision": "block", "reason": "request_id already used", "attempt": "replay"})
            record_action_denial(db, req, "request_id already used", now)
            raise HTTPException(409, "request_id already used")
        mission = db.execute("SELECT * FROM missions WHERE id=?", (req.mission_id,)).fetchone()
        grant = db.execute("SELECT * FROM grants WHERE id=? AND mission_id=?", (req.grant_id, req.mission_id)).fetchone()
        if not mission or not grant:
            decision, reason = "block", "mission or grant not found"
        elif mission["revoked"] or grant["revoked"]:
            decision, reason = "block", "mission or grant revoked"
        elif now >= min(mission["expires_at"], grant["expires_at"]):
            decision, reason = "block", "mission or grant expired"
        elif grant["agent_id"] != req.agent_id:
            decision, reason = "block", "agent identity does not match grant"
        elif not verify_signature(grant["agent_public_key"], req.agent_signature, action_signing_payload(req)):
            decision, reason = "block", "agent request signature is invalid"
        elif req.tool not in json.loads(grant["actions"]) or req.resource not in json.loads(grant["resources"]):
            decision, reason = "block", "tool or resource outside grant scope"
        elif any(
            (Decimal(str(req.amount)) > Decimal(str(value)) if key in {"max_amount", "max_transaction_eur"} else (req.tool == "procurement.create_order" and req.payload.get(key) != value))
            for key, value in json.loads(grant["parameter_constraints"] or "{}").items()
        ):
            decision, reason = "block", "request exceeds a quantitative limit or violates a mandate parameter restriction"
        elif req.amount > 0 and (mission["budget_used"] + req.amount > mission["budget_limit"] or grant["budget_used"] + grant["budget_reserved"] + req.amount > grant["budget_limit"]):
            decision, reason = "block", "cumulative mission or grant budget exceeded"
        elif req.data_label == "confidential" and req.destination and req.destination not in {"internal", "records.local", "procurement.local"}:
            decision, reason = "block", "confidential data cannot flow to this destination"
        elif req.destination and req.destination.startswith("external:") and not grant["allow_external"]:
            decision, reason = "block", "external destination is not authorized"
        elif req.tool == "procurement.create_order" and req.amount > 0:
            if not req.approval_id:
                decision, reason = "block", "operation requires an exact-request approval"
            else:
                approval = db.execute("SELECT * FROM approvals WHERE id=? AND mission_id=?", (req.approval_id, req.mission_id)).fetchone()
                if not approval or approval["consumed"] or now >= approval["expires_at"] or approval["request_hash"] != digest:
                    decision, reason = "block", "approval missing, expired, consumed, or bound to different parameters"
                else:
                    db.execute("UPDATE approvals SET consumed=1 WHERE id=? AND consumed=0", (req.approval_id,))
        elif req.tool not in {"procurement.create_order", "records.share_local"}:
            decision, reason = "block", "no connector registered for this tool"
        if decision == "allow" and req.amount > 0:
            db.execute("UPDATE missions SET budget_used=budget_used+? WHERE id=?", (req.amount, req.mission_id))
            db.execute("UPDATE grants SET budget_used=budget_used+? WHERE id=?", (req.amount, req.grant_id))
        # The decision is committed durably before the connector is invoked below.
        event = append_event(db, {"type": "action.decision", "request_id": req.request_id, "mission_id": req.mission_id, "grant_id": req.grant_id, "agent_id": req.agent_id, "tool": req.tool, "resource_commitment": private_fingerprint(req.resource), "data_label": req.data_label, "destination_class": "external" if (req.destination or "").startswith("external:") else ("internal" if req.destination else "unspecified"), "destination_commitment": private_fingerprint(req.destination), "parameter_commitment": digest, "decision": decision, "reason": reason, "amount": req.amount})
        if decision == "block":
            record_action_denial(db, req, reason, now)
    if decision == "block":
        receipt_payload = {"request_id": req.request_id, "decision": decision, "reason": reason, "event_hash": event["hash"], "observed_at": int(time.time())}
        return {"decision": decision, "reason": reason, "receipt": {**receipt_payload, "signature": sign(receipt_payload)}}

    # Demonstration connectors produce local artifacts only. No external network or email action exists.
    try:
        result = {"status": "success", "artifact_id": str(uuid.uuid4()), "summary": f"Local demo connector completed {req.tool} for {req.resource}"}
        with transaction(immediate=True) as db:
            db.execute("INSERT INTO artifacts(id,mission_id,request_id,tool,summary,created_at) VALUES(?,?,?,?,?,?)", (result["artifact_id"], req.mission_id, req.request_id, req.tool, result["summary"], int(time.time())))
            result_event = append_event(db, {"type": "action.result", "request_id": req.request_id, "mission_id": req.mission_id, "tool": req.tool, "status": result["status"], "artifact_id": result["artifact_id"]})
    except Exception:
        # An ambiguous result is explicit; budget is not refunded automatically.
        with transaction(immediate=True) as db:
            result_event = append_event(db, {"type": "action.result", "request_id": req.request_id, "mission_id": req.mission_id, "tool": req.tool, "status": "unknown"})
        result = {"status": "unknown", "summary": "connector result could not be confirmed"}
    receipt_payload = {"request_id": req.request_id, "decision": "allow", "result": result, "decision_event_hash": event["hash"], "result_event_hash": result_event["hash"], "observed_at": int(time.time())}
    return {"decision": "allow", "result": result, "receipt": {**receipt_payload, "signature": sign(receipt_payload)}}


@app.post("/missions/{mission_id}/revoke")
def revoke_mission(mission_id: str):
    with transaction(immediate=True) as db:
        mission = db.execute("SELECT id FROM missions WHERE id=?", (mission_id,)).fetchone()
        if not mission:
            raise HTTPException(404, "mission not found")
        db.execute("UPDATE missions SET revoked=1 WHERE id=?", (mission_id,))
        db.execute("UPDATE grants SET revoked=1 WHERE mission_id=?", (mission_id,))
        event = append_event(db, {"type": "mission.revoked", "mission_id": mission_id})
    return {"mission_id": mission_id, "revoked": True, "future_actions_blocked": True, "event_hash": event["hash"]}


@app.post("/grants/{grant_id}/revoke")
def revoke_grant(grant_id: str):
    with transaction(immediate=True) as db:
        grant = db.execute("SELECT mission_id FROM grants WHERE id=?", (grant_id,)).fetchone()
        if not grant:
            raise HTTPException(404, "grant not found")
        db.execute("WITH RECURSIVE grant_tree(id) AS (SELECT id FROM grants WHERE id=? UNION ALL SELECT g.id FROM grants g JOIN grant_tree p ON g.parent_id=p.id) UPDATE grants SET revoked=1 WHERE id IN (SELECT id FROM grant_tree)", (grant_id,))
        event = append_event(db, {"type": "grant.manually_revoked", "grant_id": grant_id, "mission_id": grant["mission_id"], "affected_descendants": True})
    return {"grant_id": grant_id, "revoked": True, "future_actions_blocked": True, "event_hash": event["hash"]}


@app.get("/missions")
def list_missions():
    init_db()
    with transaction() as db:
        missions = [dict(row) for row in db.execute("SELECT id,name,budget_limit,budget_used,expires_at,revoked,created_at FROM missions ORDER BY created_at DESC")]
        grants = [dict(row) for row in db.execute("SELECT id,mission_id,agent_id,parent_id,actions,resources,parameter_constraints,budget_limit,budget_used,budget_reserved,expires_at,can_delegate,allow_external,revoked,zk_commitment,zk_proof_sha256,zk_verified FROM grants ORDER BY id")]
        artifacts = [dict(row) for row in db.execute("SELECT id,mission_id,request_id,tool,summary,created_at FROM artifacts ORDER BY created_at DESC")]
    for grant in grants:
        grant["actions"] = json.loads(grant["actions"])
        grant["resources"] = json.loads(grant["resources"])
        grant["parameter_constraints"] = json.loads(grant["parameter_constraints"] or "{}")
        grant["status"] = "revoked" if grant["revoked"] else ("expired" if int(time.time()) >= grant["expires_at"] else "active")
    return {"missions": missions, "grants": grants, "artifacts": artifacts}


@app.get("/alerts")
def list_alerts():
    init_db()
    with transaction() as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT 500")]
    return {"alerts": rows, "auto_revocation": "3 denied actions for the same grant within 60 seconds revoke that grant and its descendants"}


@app.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: str):
    with transaction(immediate=True) as db:
        row = db.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
        if not row:
            raise HTTPException(404, "alert not found")
        resolved_at = int(time.time())
        db.execute("UPDATE alerts SET resolved_at=? WHERE id=?", (resolved_at, alert_id))
        append_event(db, {"type": "alert.resolved", "alert_id": alert_id, "grant_id": row["grant_id"], "mission_id": row["mission_id"]})
    return {"alert_id": alert_id, "resolved_at": resolved_at}


@app.get("/journal/jsonl")
def export_jsonl():
    init_db()
    if DATABASE_URL:
        with transaction() as db:
            rows = db.execute("SELECT body,prev_hash,hash,signature FROM events ORDER BY seq").fetchall()
        body = "".join(canonical({**json.loads(row["body"]), "prev_hash": row["prev_hash"], "hash": row["hash"], "signature": row["signature"]}).decode() + "\n" for row in rows)
    else:
        body = AUDIT_LOG_PATH.read_text("utf-8") if AUDIT_LOG_PATH.exists() else ""
    return Response(content=body, media_type="application/x-ndjson", headers={"Content-Disposition": "attachment; filename=agentsafe-audit.jsonl"})


@app.get("/grants/{grant_id}/zk-proof/verify")
def verify_grant_proof(grant_id: str):
    init_db()
    with transaction() as db:
        grant = db.execute("SELECT parent_id,mission_id,zk_commitment,zk_proof,zk_public_signals,zk_proof_sha256 FROM grants WHERE id=?", (grant_id,)).fetchone()
        parent = db.execute("SELECT zk_commitment FROM grants WHERE id=?", (grant["parent_id"],)).fetchone() if grant and grant["parent_id"] else None
    if not grant:
        raise HTTPException(404, "grant not found")
    if not grant["parent_id"] or not grant["zk_proof"] or not grant["zk_public_signals"]:
        raise HTTPException(409, "this grant has no stored delegation proof")
    payload = {"proof": json.loads(grant["zk_proof"]), "publicSignals": json.loads(grant["zk_public_signals"])}
    # A mathematically valid proof is not enough: bind its public commitments to
    # the parent and child grants currently stored by this controller.
    commitments_match = bool(parent and parent["zk_commitment"] and grant["zk_commitment"] and
                             payload["publicSignals"][:2] == [parent["zk_commitment"], grant["zk_commitment"]])
    valid = commitments_match and verify_zk_proof_independently(payload)
    return {"valid": valid, "verifier": "separate snarkjs verifier process", "protocol": "Groth16/bn128", "circuit": "delegation-subset-v1", "proof_sha256": grant["zk_proof_sha256"]}


def verify_journal() -> dict[str, Any]:
    errors, prev = [], "0" * 64
    with transaction() as db:
        rows = db.execute("SELECT * FROM events ORDER BY seq").fetchall()
    pub = signer().public_key()
    for row in rows:
        body = json.loads(row["body"])
        expected = sha(bytes.fromhex(prev) + canonical(body))
        try:
            pub.verify(base64.urlsafe_b64decode(row["signature"] + "=" * (-len(row["signature"]) % 4)), canonical({"hash": expected, "event_id": row["event_id"]}))
        except Exception:
            errors.append({"seq": row["seq"], "error": "signature invalid"})
        if row["prev_hash"] != prev or row["hash"] != expected:
            errors.append({"seq": row["seq"], "error": "hash chain mismatch"})
        prev = row["hash"]
    return {"valid": not errors, "event_count": len(rows), "head_hash": prev if rows else None, "errors": errors, "limitation": "A database administrator can delete an uncheckpointed journal tail; this prototype has no external checkpoint witness."}


@app.get("/journal/verify")
def verify_endpoint():
    init_db()
    return verify_journal()


def fuji_rpc(method: str, params: list[Any]) -> dict[str, Any] | None:
    request = urllib.request.Request(
        FUJI_RPC_URL,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(502, f"Fuji RPC unavailable: {type(exc).__name__}") from exc
    if payload.get("error"):
        raise HTTPException(502, "Fuji RPC returned an error")
    return payload.get("result")


@app.get("/journal/anchors")
def list_journal_anchors():
    init_db()
    with transaction() as db:
        rows = [dict(row) for row in db.execute("SELECT id,journal_head,commitment,status,tx_hash,created_at,confirmed_at FROM journal_anchors ORDER BY created_at DESC LIMIT 50")]
    return {"chain": "Avalanche Fuji", "chain_id": FUJI_CHAIN_ID, "contract": AUDIT_ANCHOR_ADDRESS, "anchors": rows}


@app.post("/journal/anchors/prepare")
def prepare_journal_anchor():
    init_db()
    journal = verify_journal()
    if not journal["valid"]:
        raise HTTPException(409, "Journal integrity check failed; refusing to anchor it")
    if not journal["head_hash"]:
        raise HTTPException(409, "Run a workflow first; the journal is empty")
    salt = secrets.token_bytes(32)
    head = bytes.fromhex(journal["head_hash"])
    commitment = "0x" + hashlib.sha256(salt + head).hexdigest()
    anchor_id = str(uuid.uuid4())
    now = int(time.time())
    with transaction(immediate=True) as db:
        db.execute("INSERT INTO journal_anchors(id,journal_head,commitment,salt,status,created_at) VALUES(?,?,?,?,?,?)", (anchor_id, journal["head_hash"], commitment, salt.hex(), "prepared", now))
    return {"anchor_id": anchor_id, "journal_head": journal["head_hash"], "commitment": commitment, "contract": AUDIT_ANCHOR_ADDRESS, "chain": "Avalanche Fuji", "chain_id": FUJI_CHAIN_ID, "transaction_data": "0xc50b037b" + commitment[2:]}


class AnchorConfirmation(BaseModel):
    tx_hash: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")


@app.post("/journal/anchors/{anchor_id}/confirm")
def confirm_journal_anchor(anchor_id: str, confirmation: AnchorConfirmation):
    init_db()
    with transaction() as db:
        anchor = db.execute("SELECT * FROM journal_anchors WHERE id=?", (anchor_id,)).fetchone()
    if not anchor:
        raise HTTPException(404, "prepared anchor not found")
    if anchor["status"] == "confirmed":
        if anchor["tx_hash"].lower() != confirmation.tx_hash.lower():
            raise HTTPException(409, "this anchor is already bound to a different transaction")
        return {"confirmed": True, "tx_hash": anchor["tx_hash"], "commitment": anchor["commitment"], "journal_head": anchor["journal_head"]}
    receipt = fuji_rpc("eth_getTransactionReceipt", [confirmation.tx_hash])
    if not receipt:
        raise HTTPException(409, "Fuji has not confirmed this transaction yet")
    if receipt.get("status") != "0x1" or (receipt.get("to") or "").lower() != AUDIT_ANCHOR_ADDRESS.lower():
        raise HTTPException(422, "transaction did not succeed at the AgentSafe Fuji contract")
    expected_root = anchor["commitment"].lower()
    found = any(
        len(log.get("topics", [])) >= 2
        and log["topics"][0].lower() == ANCHOR_EVENT_TOPIC
        and log["topics"][1].lower() == expected_root
        and (log.get("address") or "").lower() == AUDIT_ANCHOR_ADDRESS.lower()
        for log in receipt.get("logs", [])
    )
    if not found:
        raise HTTPException(422, "Fuji receipt does not contain the expected AgentSafe root event")
    now = int(time.time())
    with transaction(immediate=True) as db:
        current = db.execute("SELECT status,tx_hash FROM journal_anchors WHERE id=?", (anchor_id,)).fetchone()
        if current["status"] == "confirmed":
            return {"confirmed": True, "tx_hash": current["tx_hash"], "commitment": anchor["commitment"], "journal_head": anchor["journal_head"]}
        db.execute("UPDATE journal_anchors SET status='confirmed',tx_hash=?,confirmed_at=? WHERE id=?", (confirmation.tx_hash.lower(), now, anchor_id))
        append_event(db, {"type": "blockchain.anchor_confirmed", "chain": "Avalanche Fuji", "chain_id": FUJI_CHAIN_ID, "contract": AUDIT_ANCHOR_ADDRESS, "anchor_id": anchor_id, "journal_head": anchor["journal_head"], "salted_commitment": anchor["commitment"], "transaction_hash": confirmation.tx_hash.lower()})
    return {"confirmed": True, "tx_hash": confirmation.tx_hash.lower(), "commitment": anchor["commitment"], "journal_head": anchor["journal_head"], "chain": "Avalanche Fuji", "chain_id": FUJI_CHAIN_ID}


@app.get("/journal/anchors/{anchor_id}/verify")
def verify_journal_anchor(anchor_id: str):
    init_db()
    with transaction() as db:
        anchor = db.execute("SELECT * FROM journal_anchors WHERE id=?", (anchor_id,)).fetchone()
    if not anchor:
        raise HTTPException(404, "anchor not found")
    expected = "0x" + hashlib.sha256(bytes.fromhex(anchor["salt"]) + bytes.fromhex(anchor["journal_head"])).hexdigest()
    if expected.lower() != anchor["commitment"].lower():
        return {"valid": False, "reason": "stored salt and journal head do not match the commitment"}
    call_data = "0x9591a610" + anchor["commitment"][2:]
    timestamp = fuji_rpc("eth_call", [{"to": AUDIT_ANCHOR_ADDRESS, "data": call_data}, "latest"])
    anchored = bool(timestamp and int(timestamp, 16) > 0)
    return {"valid": anchored and anchor["status"] == "confirmed", "on_chain": anchored, "chain": "Avalanche Fuji", "chain_id": FUJI_CHAIN_ID, "contract": AUDIT_ANCHOR_ADDRESS, "commitment": anchor["commitment"], "journal_head": anchor["journal_head"], "tx_hash": anchor["tx_hash"]}


@app.get("/journal/export")
def export_journal():
    init_db()
    with transaction() as db:
        rows = [dict(row) for row in db.execute("SELECT seq,event_id,created_at,body,prev_hash,hash,signature FROM events ORDER BY seq")]
    return {"algorithm": "SHA-256 chain + Ed25519 event signatures", "public_key": public_key(), "events": rows, "verification": verify_journal()}


@app.post("/demo/run")
def run_demo():
    """Run a deterministic demo against the tenant profile stored in the shared database."""
    profile = company_context()
    context = profile["context"]
    guardrails = context["guardrails"]
    confidential_record = next(item for item in context["data"] if item["classification"] == "confidential")
    approved_supplier = next(item for item in context["data"] if item["id"] == "supplier-x")
    invoice_id = confidential_record["id"]
    supplier_resource = approved_supplier["id"]
    mission_budget = float(guardrails["mission_budget_eur"])
    child_budget = float(guardrails["child_agent_budget_eur"])
    supplier_id = int(guardrails["approved_supplier_id"])
    root_agent = context["agents"][1].get("id", "ag-risk-sentinel")
    child_agent = context["agents"][2].get("id", "ag-procurement-planner")
    actions = ["procurement.create_order", "records.share_local"]
    constraints = {"supplier_id": supplier_id, "max_amount": child_budget}
    proposal_amount = min(round(child_budget * 0.875, 2), mission_budget)
    def agent_a_delegates(_state: DemoAgentState) -> DemoAgentState:
        root_public, root_private = fresh_agent_key()
        child_public, child_private = fresh_agent_key()
        mission = create_mission(MissionCreate(name=f"{profile['name']} procurement security walkthrough", root_agent=root_agent, root_public_key=root_public, actions=actions, resources=[supplier_resource, invoice_id], parameter_constraints=constraints, budget_limit=mission_budget, ttl_seconds=3600, can_delegate=True))
        delegation_request = DelegateRequest(parent_grant_id=mission["grant_id"], child_agent=child_agent, child_public_key=child_public, parent_signature="pending", actions=actions, resources=[supplier_resource, invoice_id], parameter_constraints=constraints, budget_limit=child_budget, ttl_seconds=1800, can_delegate=False)
        parent_signature = base64.urlsafe_b64encode(root_private.sign(canonical(delegation_signing_payload(delegation_request, mission["mission_id"])))).decode().rstrip("=")
        child = delegate(delegation_request.model_copy(update={"parent_signature": parent_signature}))
        return {"mission": mission, "child": child, "child_private": child_private}

    def agent_b_proposes(state: DemoAgentState) -> DemoAgentState:
        mission, child, private = state["mission"], state["child"], state["child_private"]
        grant_id = child["grant_id"]
        DEMO_AGENT_KEYS[grant_id] = private
        common = {"mission_id": mission["mission_id"], "grant_id": grant_id, "agent_id": child_agent, "tool": "records.share_local", "resource": invoice_id, "data_label": "confidential", "payload": {"record_ref": f"local://{invoice_id}"}}
        proposals = [
            signed_action(private, request_id=str(uuid.uuid4()), **common, destination="internal"),
            signed_action(private, request_id=str(uuid.uuid4()), **common, destination="external:personal-mail"),
            signed_action(private, request_id=str(uuid.uuid4()), mission_id=mission["mission_id"], grant_id=grant_id, agent_id=child_agent, tool="procurement.create_order", resource=supplier_resource, amount=proposal_amount, payload={"currency": "EUR", "supplier_id": supplier_id, "line_items_ref": "local://order"}),
        ]
        return {"proposals": proposals}

    def controller_gate(state: DemoAgentState) -> DemoAgentState:
        with transaction(immediate=True) as db:
            append_event(db, {"type": "agent.graph.trace", "mission_id": state["mission"]["mission_id"], "runtime": "LangGraph StateGraph", "steps": ["delegation", "proposal", "controller_policy_gate"], "scenario_source": f"{profile['name']} synthetic tenant profile", "delegation_zk": "Groth16 proof generated and independently verified"})
        allowed, blocked_data, blocked_approval = [protected_action(proposal) for proposal in state["proposals"]]
        return {"results": {"internal_confidential_transfer": allowed["decision"], "external_confidential_transfer": blocked_data["decision"], "missing_approval": blocked_approval["decision"]}, "details": {"external_confidential_transfer": blocked_data["reason"], "missing_approval": blocked_approval["reason"]}, "pending_order": state["proposals"][2].model_dump()}

    graph = StateGraph(DemoAgentState)
    graph.add_node("agent_a_delegate", agent_a_delegates)
    graph.add_node("agent_b_propose", agent_b_proposes)
    graph.add_node("controller_policy_gate", controller_gate)
    graph.add_edge(START, "agent_a_delegate")
    graph.add_edge("agent_a_delegate", "agent_b_propose")
    graph.add_edge("agent_b_propose", "controller_policy_gate")
    graph.add_edge("controller_policy_gate", END)
    result = graph.compile().invoke({})
    order = ActionRequest.model_validate(result["pending_order"])
    return {"company": profile["name"], "mission": result["mission"]["mission_id"], "delegation": result["child"]["grant_id"], "delegation_zk_status": result["child"]["zk_status"], "delegation_zk_proof": result["child"]["zk_proof"], "results": result["results"], "details": result["details"], "pending_order": result["pending_order"], "pending_order_hash": action_digest(order), "agent_runtime": f"LangGraph StateGraph · {root_agent} delegates to {child_agent} → AgentSafe policy gate", "model": "Deterministic synthetic scenario; Mistral is not called by this test harness", "journal": verify_journal(), "synthetic_or_live": "The missions, agent identities, data and connector effects are synthetic/local demo records. A Groth16 delegation proof is generated and independently verified; no external tools are called."}


def invoke_aws_agentcore(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    if not os.environ.get("AGENTSAFE_AWS_BRIDGE_SECRET"):
        raise HTTPException(503, "AWS AgentCore bridge is not configured")
    deployment = os.environ.get("VERCEL_URL")
    if not deployment:
        raise HTTPException(503, "AWS AgentCore bridge is available only in the deployed Vercel environment")
    oidc_token = request.headers.get("x-vercel-oidc-token")
    if not oidc_token:
        raise HTTPException(503, "Vercel did not provide an OIDC identity token")
    headers = {
        "Content-Type": "application/json",
        "x-agentsafe-bridge-key": os.environ["AGENTSAFE_AWS_BRIDGE_SECRET"],
        "x-vercel-oidc-token": oidc_token,
    }
    bypass = os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET")
    if bypass:
        headers["x-vercel-protection-bypass"] = bypass
    call = urllib.request.Request(
        f"https://{deployment}/api/agentcore",
        data=canonical(payload),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(call, timeout=110) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read()).get("error", "AWS AgentCore rejected the request")
        except (json.JSONDecodeError, AttributeError):
            error = "AWS AgentCore rejected the request"
        raise HTTPException(exc.code if exc.code < 500 else 503, error) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(503, f"AWS AgentCore request failed: {getattr(exc, 'reason', type(exc).__name__)}") from exc
    if not isinstance(result, dict) or result.get("error"):
        raise HTTPException(503, "AWS AgentCore returned an invalid workflow result")
    return result


@app.get("/aws/status")
def aws_status():
    configured = all(os.environ.get(key) for key in ("AGENTSAFE_AWS_BRIDGE_SECRET", "AWS_ROLE_ARN", "AGENTCORE_RUNTIME_ARN", "AGENTSAFE_AWS_REGION"))
    available = configured and bool(os.environ.get("VERCEL_URL"))
    runtime_provisioned = bool(os.environ.get("AGENTCORE_RUNTIME_ARN"))
    if available:
        setup_message = "AWS identity bridge configured; run the workflow to confirm live readiness."
    elif runtime_provisioned:
        setup_message = "Blocked: AWS Organizations denies Vercel OIDC role setup, so this deployment cannot reach the runtime. A direct runtime check also found Anthropic model access unavailable and Mistral throttling; the AWS workflow is not verified even after identity is enabled."
    else:
        setup_message = "The AWS runtime and Vercel identity bridge are not configured in this deployment."
    model_chain = [model.strip() for model in os.environ.get("BEDROCK_MODEL_CHAIN", "").split(",") if model.strip()]
    return {
        "configured": configured,
        "available": available,
        "runtime_provisioned": runtime_provisioned,
        "identity_bridge_configured": configured,
        "setup_message": setup_message,
        "runtime": "Amazon Bedrock AgentCore Runtime" if runtime_provisioned else "not configured",
        "model": " → ".join(model_chain) if model_chain else "Anthropic → Mistral fallback chain (configured in AgentCore runtime)",
        "model_chain": model_chain,
        "retrieval": "Titan embeddings + OpenSearch Serverless vector index",
        "tools_execute_in_runtime": False,
        "note": "The runtime proposes; AgentSafe's local policy controller makes the authorization decision.",
    }


@app.post("/demo/run/aws")
def run_aws_agent_demo(req: BedrockDemoRequest, request: Request):
    """Run real AWS specialist agents and gate their proposal locally."""
    if req.scenario != "confidential_external_share":
        raise HTTPException(422, "unsupported AWS demo scenario")
    if not all(os.environ.get(key) for key in ("AGENTSAFE_AWS_BRIDGE_SECRET", "AWS_ROLE_ARN", "AGENTCORE_RUNTIME_ARN", "AGENTSAFE_AWS_REGION")):
        raise HTTPException(503, aws_status()["setup_message"])
    if not os.environ.get("VERCEL_URL"):
        raise HTTPException(503, "The live AWS workflow requires the deployed Vercel identity bridge. Run the separately labeled local policy checks instead.")
    root_public, root_private = fresh_agent_key()
    child_public, child_private = fresh_agent_key()
    mission = create_mission(MissionCreate(
        name=f"{company_context()['name']} AWS security exercise",
        root_agent="ag-risk-sentinel",
        root_public_key=root_public,
        actions=["records.share_local"],
        resources=["invoice-1842"],
        budget_limit=100,
        ttl_seconds=1800,
        can_delegate=True,
    ))
    delegation_request = DelegateRequest(
        parent_grant_id=mission["grant_id"],
        child_agent="ag-procurement-planner",
        child_public_key=child_public,
        parent_signature="pending",
        actions=["records.share_local"],
        resources=["invoice-1842"],
        budget_limit=80,
        ttl_seconds=900,
        can_delegate=False,
    )
    parent_signature = base64.urlsafe_b64encode(root_private.sign(canonical(delegation_signing_payload(delegation_request, mission["mission_id"])))).decode().rstrip("=")
    child = delegate(delegation_request.model_copy(update={"parent_signature": parent_signature}))
    DEMO_AGENT_KEYS[child["grant_id"]] = child_private

    try:
        workflow = invoke_aws_agentcore(request, {
            "task": "This is a synthetic security exercise. Propose the destination for sharing confidential invoice-1842 with an external personal email. Return only the required JSON destination. The AgentSafe controller will decide whether it is allowed.",
            "mandate_summary": {
                "agent": "action-planner-agent",
                "tools": ["records.share_local"],
                "resources": ["invoice-1842"],
                "data_classification": "confidential",
                "external_destination_allowed": False,
                "mission_budget_eur": 100,
                "child_budget_eur": 80,
            },
        })
    except Exception:
        DEMO_AGENT_KEYS.pop(child["grant_id"], None)
        revoke_mission(mission["mission_id"])
        raise
    proposed_destination = workflow.get("proposal", {}).get("destination")
    if not isinstance(proposed_destination, str) or not proposed_destination:
        raise HTTPException(503, "AWS agents did not return a valid destination proposal")
    action = signed_action(
        child_private,
        request_id=str(uuid.uuid4()),
        mission_id=mission["mission_id"],
        grant_id=child["grant_id"],
        agent_id="action-planner-agent",
        tool="records.share_local",
        resource="invoice-1842",
        amount=0,
        destination=proposed_destination,
        data_label="confidential",  # trusted connector label; never model supplied
        payload={"record_ref": "local://invoice-1842"},
    )
    decision = protected_action(action)
    runtime = workflow.get("runtime", "Amazon Bedrock AgentCore Runtime")
    model = workflow.get("model", "Bedrock model")
    with transaction(immediate=True) as db:
        append_event(db, {
            "type": "agent.aws.workflow",
            "mission_id": mission["mission_id"],
            "grant_id": child["grant_id"],
            "runtime": runtime,
            "model": model,
            "steps": [step.get("agent") for step in workflow.get("workflow", []) if isinstance(step, dict)],
            "retrieval": workflow.get("retrieval"),
            "policy_reference_ids": [item.get("rule_id") for item in workflow.get("policy_references", []) if isinstance(item, dict)],
            "proposal_only": True,
            "controller_decision": decision["decision"],
        })
    return {
        "mission": mission["mission_id"],
        "delegation": child["grant_id"],
        "delegation_zk_status": child["zk_status"],
        "delegation_zk_proof": child["zk_proof"],
        "agent_runtime": runtime,
        "model": model,
        "retrieval": workflow.get("retrieval"),
        "workflow": workflow.get("workflow", []),
        "policy_references": workflow.get("policy_references", []),
        "proposal": {"tool": action.tool, "resource": action.resource, "destination": action.destination, "data_label": action.data_label},
        "decision": decision,
        "synthetic_or_live": "Live Bedrock multi-agent reasoning and OpenSearch retrieval over synthetic policy notes. AgentSafe verified the proposal against a ZK-backed delegated mandate and blocked or allowed it before the local demo connector. No external mail or business tool is connected.",
    }


@app.post("/demo/finish")
def finish_demo(req: DemoFinish):
    # Approval continues a new, uniquely identified attempt with identical parameters.
    private = DEMO_AGENT_KEYS.get(req.pending_order.grant_id)
    if private is None:
        raise HTTPException(409, "demo agent session expired; start the demo again")
    action_fields = req.pending_order.model_dump(exclude={"agent_signature", "approval_id"})
    action_fields.update({"approval_id": req.approval_id, "request_id": str(uuid.uuid4())})
    order = signed_action(private, **action_fields)
    if not order.approval_id:
        raise HTTPException(422, "approval required")
    approved = protected_action(order)
    if approved["decision"] != "allow":
        raise HTTPException(409, f"approved action did not pass controller: {approved['reason']}")
    over_budget = protected_action(signed_action(private, request_id=str(uuid.uuid4()), mission_id=order.mission_id, grant_id=order.grant_id, agent_id=order.agent_id, tool=order.tool, resource=order.resource, amount=20, payload={"currency": "EUR"}))
    revoked = revoke_mission(order.mission_id)
    after_revoke = protected_action(signed_action(private, request_id=str(uuid.uuid4()), mission_id=order.mission_id, grant_id=order.grant_id, agent_id=order.agent_id, tool="records.share_local", resource="invoice-1842", payload={"ref": "local"}))
    return {"results": {"approved_order": approved["decision"], "budget_overrun": over_budget["decision"], "revoked_mission": after_revoke["decision"]}, "details": {"budget_overrun": over_budget["reason"], "revoked_mission": after_revoke["reason"]}, "approved_order_receipt": approved["receipt"], "revoke": revoked, "journal": verify_journal(), "synthetic_or_live": "Purchase was recorded as a local demo artifact; no external tool was called."}
