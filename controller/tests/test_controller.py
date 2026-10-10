import os
import sys
import tempfile
import uuid
from pathlib import Path

TEMP_DATA = tempfile.TemporaryDirectory(prefix="agentsafe-tests-")
os.environ["AGENTSAFE_DATA_DIR"] = TEMP_DATA.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import app, fresh_agent_key, signed_action  # noqa: E402
from verify_export import verify  # noqa: E402


def test_policy_workflow_approval_budget_revocation_and_export():
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/health").json()["external_connectors"] is False
        prepared = client.post("/demo/run").json()
        assert prepared["agent_runtime"].startswith("LangGraph StateGraph")
        assert prepared["model"].startswith("Deterministic synthetic scenario")
        assert prepared["results"] == {
            "internal_confidential_transfer": "allow",
            "external_confidential_transfer": "block",
            "missing_approval": "block",
        }

        order = prepared["pending_order"]
        digest = client.post("/actions/hash", json=order).json()["parameter_hash"]
        approval = client.post("/approvals", params={"mission_id": order["mission_id"], "request_hash": digest}).json()
        finished = client.post("/demo/finish", json={"pending_order": order, "approval_id": approval["approval_id"]})
        assert finished.status_code == 200, finished.text
        assert finished.json()["results"] == {"approved_order": "allow", "budget_overrun": "block", "revoked_mission": "block"}

        journal = client.get("/journal/export").json()
        assert not verify(journal)
        assert client.get("/journal/verify").json()["valid"] is True


def test_replay_and_scope_escalation_are_rejected():
    with TestClient(app, base_url="http://127.0.0.1") as client:
        public, private = fresh_agent_key()
        mission = client.post("/missions", json={"name": "test", "root_agent": "root", "root_public_key": public, "actions": ["records.share_local"], "resources": ["file-a"], "budget_limit": 25}).json()
        child_public, _ = fresh_agent_key()
        denied = client.post("/delegations", json={"parent_grant_id": mission["grant_id"], "child_agent": "child", "child_public_key": child_public, "parent_signature": "invalid", "actions": ["procurement.create_order"], "resources": ["file-a"], "budget_limit": 5, "ttl_seconds": 60})
        assert denied.status_code == 403
        body = signed_action(private, request_id=str(uuid.uuid4()), mission_id=mission["mission_id"], grant_id=mission["grant_id"], agent_id="root", tool="records.share_local", resource="file-a", payload={"local_ref": "private-data"}).model_dump()
        tampered = dict(body)
        tampered["request_id"] = str(uuid.uuid4())
        tampered["payload"] = {"local_ref": "different-data"}
        assert client.post("/actions", json=tampered).json()["reason"] == "agent request signature is invalid"
        assert client.post("/actions", json=body).json()["decision"] == "allow"
        replay = client.post("/actions", json=body)
        assert replay.status_code == 409


def test_browser_origin_guard_blocks_remote_sites():
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/demo/run", headers={"Origin": "https://attacker.example"})
        assert response.status_code == 403


def test_export_verifier_detects_tampering():
    with TestClient(app, base_url="http://127.0.0.1") as client:
        public, _ = fresh_agent_key()
        client.post("/missions", json={"name": "test", "root_agent": "root", "root_public_key": public, "actions": ["records.share_local"], "resources": ["file-a"], "budget_limit": 25})
        journal = client.get("/journal/export").json()
        journal["events"][0]["body"] = journal["events"][0]["body"].replace("mission.created", "mission.tampered")
        assert verify(journal)
